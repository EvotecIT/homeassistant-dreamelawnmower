"""Experimental map camera for Dreame lawn mower."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import timedelta
from functools import partial
from pathlib import Path
from time import monotonic
from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_MAP_LABEL_SCALE,
    CONF_MAP_MARKER_IMAGE,
    CONF_MAP_MARKER_SCALE,
    CONF_MAP_MOWING_PATH_STYLE,
    CONF_MAP_ROTATION,
    CONF_MAP_ROTATIONS,
    CONF_MAP_SPOT_AREA_STYLE,
    CONF_MAP_STROKE_SCALE,
    CONF_MAP_THEME,
    DEFAULT_MAP_LABEL_SCALE,
    DEFAULT_MAP_MARKER_SCALE,
    DEFAULT_MAP_MOWING_PATH_STYLE,
    DEFAULT_MAP_ROTATION,
    DEFAULT_MAP_SPOT_AREA_STYLE,
    DEFAULT_MAP_STROKE_SCALE,
    DEFAULT_MAP_THEME,
    DOMAIN,
)
from .control_options import active_map_index
from .coordinator import DreameLawnMowerCoordinator, runtime_tracking_active
from .debug import sanitize_diagnostic_text
from .diagnostic_events import record_diagnostic_event
from .dreame_lawn_mower_client.client import render_app_map_payload_png
from .dreame_lawn_mower_client.feature_capabilities import (
    CAPABILITY_UNSUPPORTED,
    FEATURE_LIVE_VIDEO,
    resolve_feature_capability,
)
from .dreame_lawn_mower_client.map_visuals import (
    MapRenderStyle,
    load_map_marker,
    map_render_style,
)
from .dreame_lawn_mower_client.models import DreameLawnMowerMapView
from .ha_tasks import create_background_task
from .image import (
    app_maps_contact_sheet_jpeg,
    map_diagnostics_jpeg,
    map_placeholder_jpeg,
    png_bytes_to_jpeg,
)
from .map_attributes import map_camera_attributes
from .map_cache import (
    DreameLawnMowerMapCameraCache,
    map_camera_available,
    map_camera_followup_refresh_required,
    map_camera_refresh_demand_active,
    map_camera_should_refresh,
)
from .map_image_variants import MapImageVariants
from .map_preview import CONF_MAP_RESTART_PREVIEW, RestartMapPreview, preview_scope
from .point_cloud_api import current_point_cloud_api_path
from .video_camera import DreameLawnMowerVideoCamera

_LOGGER = logging.getLogger(__name__)
_MAP_CACHE_TTL = timedelta(seconds=60)
_MAP_TIMEOUT_SECONDS = 6.0
_MAP_POLL_INTERVAL_SECONDS = 0.5
_MAP_ACTIVE_REFRESH_WINDOW_SECONDS = 90.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the mower map camera."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    map_cache = DreameLawnMowerMapCameraCache(ttl=_MAP_CACHE_TTL)
    live_map_cache = DreameLawnMowerMapCameraCache(ttl=_MAP_CACHE_TTL)
    all_maps_cache = DreameLawnMowerMapCameraCache(ttl=_MAP_CACHE_TTL)
    entities: list[Camera] = [
        DreameLawnMowerMapCamera(coordinator, map_cache),
        DreameLawnMowerLivePathMapCamera(coordinator, live_map_cache),
        DreameLawnMowerAllMapsCamera(coordinator, all_maps_cache),
        DreameLawnMowerMapDataCamera(coordinator, map_cache),
    ]
    observed_features, advertised_features = coordinator.feature_capability_evidence()
    video_capability = resolve_feature_capability(
        FEATURE_LIVE_VIDEO,
        snapshot=coordinator.data,
        descriptor=coordinator.client.descriptor,
        observed=FEATURE_LIVE_VIDEO in observed_features,
        advertised=FEATURE_LIVE_VIDEO in advertised_features,
    )
    if video_capability.state != CAPABILITY_UNSUPPORTED:
        entities.append(DreameLawnMowerVideoCamera(coordinator, entry))
    async_add_entities(entities)


class DreameLawnMowerMapCamera(
    CoordinatorEntity[DreameLawnMowerCoordinator],
    Camera,
):
    """Experimental read-only mower map camera."""

    _attr_has_entity_name = True
    _attr_name = "Map"
    _attr_icon = "mdi:map-search-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True
    _requires_map_capability = True
    _prewarm_map_image = True
    _supports_restart_preview = True
    _refresh_cached_view_on_coordinator_update = True
    # Camera attributes describe the current rendered/diagnostic view. Persisting
    # their large map payloads on every refresh creates unbounded recorder churn.
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        map_cache: DreameLawnMowerMapCameraCache,
    ) -> None:
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self._descriptor = coordinator.client.descriptor
        self._attr_unique_id = f"{self._descriptor.unique_id}_map"
        self._attr_brand = "Dreametech"
        self._attr_model = self._descriptor.display_model
        self.content_type = "image/jpeg"
        self._map_cache = map_cache
        self._image_variants = MapImageVariants()
        self._restart_preview: RestartMapPreview | None = None
        self._preview_attempted_scope: str | None = None
        self._preview_saved_at: float | None = None
        self._map_refresh_task: asyncio.Task[bytes | None] | None = None
        self._map_refresh_pending = False
        self._last_refresh_context: tuple[Any, ...] | None = None
        self._last_image_request_at: float | None = None

    async def async_added_to_hass(self) -> None:
        """Warm an enabled map camera without delaying entity setup."""
        await super().async_added_to_hass()
        if self._supports_restart_preview and self.coordinator.entry.options.get(
            CONF_MAP_RESTART_PREVIEW, False
        ):
            self._restart_preview = RestartMapPreview(
                self.hass, self.coordinator.entry.entry_id
            )
            self.coordinator.map_restart_preview = self._restart_preview
        self._last_refresh_context = self._map_refresh_context
        if self._prewarm_map_image and self.available:
            self._start_map_refresh()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel a private warm-up task when the entity is removed."""
        self._map_refresh_pending = False
        task = self._map_refresh_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await super().async_will_remove_from_hass()

    def _handle_coordinator_update(self) -> None:
        """Refresh enabled map cameras when transient map context changes."""
        context = self._map_refresh_context
        context_changed = context != self._last_refresh_context
        if (
            self._last_refresh_context is not None
            and context[0] != self._last_refresh_context[0]
        ):
            self._invalidate_map_context(context)
        active = bool(
            self.coordinator.data and runtime_tracking_active(self.coordinator.data)
        )
        if map_camera_should_refresh(
            context_changed=context_changed,
            runtime_active=active,
            manages_cached_view=self._refresh_cached_view_on_coordinator_update,
            demand_active=map_camera_refresh_demand_active(
                self._last_image_request_at,
                now=monotonic(),
                window_seconds=_MAP_ACTIVE_REFRESH_WINDOW_SECONDS,
            ),
        ):
            self._invalidate_map_context(context)
            if self.available:
                if (
                    self._map_refresh_task is not None
                    and not self._map_refresh_task.done()
                ):
                    self._map_refresh_pending = True
                self._start_map_refresh()
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        """Return whether the entity can reasonably provide a map."""
        return map_camera_available(
            self.coordinator.data,
            image_cached=self._map_cache.last_image is not None,
            requires_map_capability=self._requires_map_capability,
        )

    @property
    def device_info(self) -> dict[str, Any]:
        """Return dynamic device metadata for the registry."""
        snapshot = self.coordinator.data
        descriptor = snapshot.descriptor if snapshot is not None else self._descriptor
        return {
            "identifiers": {(DOMAIN, descriptor.unique_id)},
            "manufacturer": "Dreametech",
            "model": descriptor.display_model,
            "name": descriptor.name,
            "sw_version": getattr(snapshot, "firmware_version", None),
            "hw_version": getattr(snapshot, "hardware_version", None),
            "serial_number": getattr(snapshot, "serial_number", None),
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the latest cached map summary."""
        attributes = map_camera_attributes(
            self._map_cache.last_view,
            image_cached=self._map_cache.last_image is not None,
            refreshed_at=self._map_cache.last_refresh_at,
            last_error=self._map_cache.last_error,
            image_placeholder=self._map_cache.last_image_is_placeholder,
            runtime_status_blob=getattr(
                self.coordinator,
                "runtime_status_blob",
                None,
            ),
        )
        attributes["point_cloud_api_path"] = current_point_cloud_api_path(
            self.coordinator.entry.entry_id,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
            selected_map_index=self.coordinator.selected_map_index,
        )
        attributes["restart_preview"] = self._preview_saved_at is not None
        attributes["restart_preview_saved_at"] = self._preview_saved_at
        return attributes

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Return the latest mower map image as JPEG bytes."""
        if not self.available:
            return None
        self._last_image_request_at = monotonic()
        context = self._map_refresh_context
        if context != self._last_refresh_context:
            self._invalidate_map_context(context)
        image = await self._async_camera_image_impl()
        if image is None:
            return None
        return await self._image_variants.async_get(
            image, width, height, self.hass.async_add_executor_job
        )

    def _invalidate_map_context(self, context: tuple[Any, ...]) -> None:
        """Never retain another lawn's image while a new map is loading."""
        changed_map = (
            self._last_refresh_context is not None
            and context[0] != self._last_refresh_context[0]
        )
        drop_preview = (
            self._preview_saved_at is not None
            and self._last_refresh_context is not None
            and (context[0], *context[2:])
            != (self._last_refresh_context[0], *self._last_refresh_context[2:])
        )
        self._last_refresh_context = context
        self._map_cache.invalidate_view(drop_image=changed_map or drop_preview)
        if changed_map or drop_preview:
            self._preview_saved_at = None
        if self._map_refresh_task is not None and not self._map_refresh_task.done():
            self._map_refresh_pending = True

    async def _async_camera_image_impl(self) -> bytes | None:
        """Build the camera image after shared availability gating."""
        return await self._async_get_map_image()

    async def _async_get_map_image(self) -> bytes | None:
        """Return cached bytes immediately and refresh stale maps in the background."""
        if (
            self._map_cache.last_image is None
            and self._restart_preview is not None
            and self._selected_map_index is not None
        ):
            context = self._map_render_context
            scope = preview_scope(self._descriptor.unique_id, context)
            if scope != self._preview_attempted_scope:
                self._preview_attempted_scope = scope
                try:
                    restored = await self._restart_preview.async_load(scope)
                except Exception:  # Optional disk cache must not break live maps.
                    _LOGGER.debug("Unable to read optional map restart preview")
                    restored = None
                if (
                    restored is not None
                    and context == self._map_render_context
                    and self._map_cache.last_image is None
                ):
                    self._map_cache.store_image(restored[0])
                    self._preview_saved_at = restored[1]
                    self.async_write_ha_state()
        if self._map_cache.last_image is not None:
            if (
                self._map_cache.view_image_needs_render(
                    render_context=self._map_rotation,
                )
                or not self._map_cache.is_fresh()
            ):
                self._start_map_refresh()
            return self._map_cache.last_image

        # Native map downloads can exceed HA's camera request timeout. Keep the
        # cold-cache path responsive, just like the all-maps camera, while the
        # one background refresh owns download and cache publication.
        self._start_map_refresh()
        return await self.hass.async_add_executor_job(
            partial(
                map_placeholder_jpeg,
                title="Dreame map loading",
                detail="The selected mower map is being prepared in the background.",
            )
        )

    def _start_map_refresh(self) -> asyncio.Task[bytes | None]:
        """Start or reuse the entity's in-flight map render."""
        task = self._map_refresh_task
        if task is not None and not task.done():
            return task
        task = create_background_task(
            self.hass,
            self._async_refresh_and_render_map_image(),
            "dreame-lawn-mower-map-render",
        )
        self._map_refresh_task = task
        task.add_done_callback(self._map_refresh_finished)
        return task

    def _map_refresh_finished(self, task: asyncio.Task[bytes | None]) -> None:
        """Forget the completed refresh without clearing its cached result."""
        if self._map_refresh_task is task:
            self._map_refresh_task = None
            if self._map_refresh_pending:
                self._map_refresh_pending = False
                self._map_cache.invalidate_view()
                if map_camera_followup_refresh_required(
                    pending=True,
                    available=self.available,
                ):
                    self._start_map_refresh()

    async def _async_refresh_and_render_map_image(self) -> bytes | None:
        """Refresh the source view and atomically replace rendered JPEG bytes."""
        map_index = self._selected_map_index
        rotation = self._map_rotation
        render_context = self._map_render_context
        view = await self._async_refresh_map_view()
        if (
            map_index is not None
            and view.image_png is not None
            and (
                view.source not in {"app_action_map", "batch_vector_map"}
                or view.summary is None
                or view.summary.map_id != map_index
            )
        ):
            # The client can discover a map switch before the coordinator does.
            # Wait for its next inventory update rather than retrying in a loop
            # or publishing another lawn with this map's presentation settings.
            self._map_cache.invalidate_view(drop_image=True)
            self._map_cache.last_error = (
                "Map identity changed; waiting for inventory refresh"
            )
            self.async_write_ha_state()
            return None
        if self._map_render_context != render_context:
            self._map_cache.invalidate_view(drop_image=True)
            self._map_refresh_pending = True
            return None
        if view.image_png is not None:
            if self._map_cache.image_matches_source(
                view.image_png,
                render_context=self._map_rotation,
            ):
                self._map_cache.last_error = None
                return self._map_cache.last_image
            try:
                image = await self.hass.async_add_executor_job(
                    partial(
                        png_bytes_to_jpeg,
                        view.image_png,
                        rotation=(
                            rotation - (view.details or {}).get("render_rotation", 0)
                        )
                        % 360,
                    )
                )
                if self._map_render_context != render_context:
                    self._map_cache.invalidate_view(drop_image=True)
                    self._map_refresh_pending = True
                    return None
                self._map_cache.store_image(
                    image,
                    source_image=view.image_png,
                    render_context=self._map_rotation,
                )
                self._map_cache.last_error = None
                self._preview_saved_at = None
                self.async_write_ha_state()
                if (
                    self._restart_preview is not None
                    and self._selected_map_index is not None
                    and self.coordinator.entry.options.get(CONF_MAP_RESTART_PREVIEW)
                ):
                    try:
                        await self._restart_preview.async_save(
                            image,
                            preview_scope(self._descriptor.unique_id, render_context),
                        )
                    except Exception:
                        _LOGGER.debug("Unable to save optional map restart preview")
                return image
            except Exception as err:
                safe_error = sanitize_diagnostic_text(err)
                _LOGGER.warning(
                    "Failed to convert Dreame mower map image: %s", safe_error
                )
                record_diagnostic_event(
                    self.coordinator,
                    code="map_image_conversion_failed",
                    source="map_camera",
                    message=safe_error,
                )
                self._map_cache.last_error = safe_error
                self.async_write_ha_state()

        if self._map_cache.last_image is not None:
            return self._map_cache.last_image
        return await self.hass.async_add_executor_job(
            partial(
                map_placeholder_jpeg,
                detail=self._map_cache.last_error or view.error,
            )
        )

    async def _async_refresh_map_view(self) -> DreameLawnMowerMapView:
        """Return a cached map view or refresh it on demand."""
        try:
            view = await self._map_cache.async_get_view(
                lambda: self.coordinator.client.async_refresh_map_view(
                    timeout=_MAP_TIMEOUT_SECONDS,
                    interval=_MAP_POLL_INTERVAL_SECONDS,
                    label_scale=self._map_label_scale,
                    style=self._map_style,
                )
            )
            self.async_write_ha_state()
            return view
        except Exception as err:
            safe_error = sanitize_diagnostic_text(err)
            _LOGGER.warning("Failed to refresh Dreame mower map image: %s", safe_error)
            record_diagnostic_event(
                self.coordinator,
                code="map_refresh_failed",
                source="map_camera",
                message=safe_error,
            )
            view = self._map_cache.store_error(safe_error)
            self.async_write_ha_state()
            return view

    @property
    def _map_label_scale(self) -> float:
        """Return configured label scaling for locally rendered map text."""
        return float(
            self.coordinator.entry.options.get(
                CONF_MAP_LABEL_SCALE,
                DEFAULT_MAP_LABEL_SCALE,
            )
        )

    @property
    def _map_rotation(self) -> int:
        """Return the configured clockwise display rotation."""
        return self._rotation_for_map_index(self._selected_map_index)

    def _rotation_for_map_index(self, map_index: int | None) -> int:
        """Share each saved map's display orientation with the contact sheet."""
        rotations = self.coordinator.entry.options.get(CONF_MAP_ROTATIONS, {})
        if isinstance(rotations, dict) and map_index is not None:
            value = rotations.get(str(map_index), rotations.get(map_index))
            if value in (0, 90, 180, 270):
                return int(value)
        return int(
            self.coordinator.entry.options.get(CONF_MAP_ROTATION, DEFAULT_MAP_ROTATION)
        )

    @property
    def _selected_map_index(self) -> int | None:
        return active_map_index(
            self.coordinator.app_maps,
            selected_map_index=self.coordinator.selected_map_index,
        )

    @property
    def _map_style(self) -> MapRenderStyle:
        options = self.coordinator.entry.options
        return map_render_style(
            options.get(CONF_MAP_THEME, DEFAULT_MAP_THEME),
            rotation=self._map_rotation,
            stroke_scale=options.get(
                CONF_MAP_STROKE_SCALE,
                DEFAULT_MAP_STROKE_SCALE,
            ),
            marker_scale=options.get(
                CONF_MAP_MARKER_SCALE,
                DEFAULT_MAP_MARKER_SCALE,
            ),
            marker_image=self._map_marker_image,
            spot_area_style=options.get(
                CONF_MAP_SPOT_AREA_STYLE,
                DEFAULT_MAP_SPOT_AREA_STYLE,
            ),
            mowing_path_style=options.get(
                CONF_MAP_MOWING_PATH_STYLE,
                DEFAULT_MAP_MOWING_PATH_STYLE,
            ),
        )

    @property
    def _map_marker_image(self) -> bytes | None:
        """Load a small marker image only from Home Assistant's www directory."""
        return load_map_marker(
            Path(self.hass.config.path("www")),
            self.coordinator.entry.options.get(CONF_MAP_MARKER_IMAGE),
        )

    @property
    def _map_render_context(self) -> tuple[Any, ...]:
        """Capture map/style identity without rejecting normal telemetry updates."""
        context = self._map_refresh_context
        return (context[0], *context[2:])

    @property
    def _map_refresh_context(self) -> tuple[Any, ...]:
        blob = self.coordinator.runtime_status_blob
        options = self.coordinator.entry.options
        return (
            self._selected_map_index,
            getattr(blob, "hex", None),
            options.get(CONF_MAP_THEME, DEFAULT_MAP_THEME),
            options.get(CONF_MAP_STROKE_SCALE, DEFAULT_MAP_STROKE_SCALE),
            options.get(CONF_MAP_MARKER_SCALE, DEFAULT_MAP_MARKER_SCALE),
            options.get(CONF_MAP_MARKER_IMAGE, ""),
            options.get(CONF_MAP_SPOT_AREA_STYLE, DEFAULT_MAP_SPOT_AREA_STYLE),
            options.get(CONF_MAP_MOWING_PATH_STYLE, DEFAULT_MAP_MOWING_PATH_STYLE),
            self._map_rotation,
            self._map_label_scale,
            tuple(sorted(
                (str(key), value)
                for key, value in options.get(CONF_MAP_ROTATIONS, {}).items()
            )),
        )


class DreameLawnMowerLivePathMapCamera(DreameLawnMowerMapCamera):
    """Disabled-by-default camera dedicated to live vector/path rendering."""

    _attr_name = "Live Path Map"
    _supports_restart_preview = False
    _attr_icon = "mdi:map-marker-path"
    _attr_entity_registry_enabled_default = False
    _refresh_cached_view_on_coordinator_update = False

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        map_cache: DreameLawnMowerMapCameraCache,
    ) -> None:
        super().__init__(coordinator, map_cache)
        self._attr_unique_id = f"{self._descriptor.unique_id}_live_path_map"

    async def _async_refresh_map_view(self) -> DreameLawnMowerMapView:
        """Return a cached live/vector map view or refresh it on demand."""
        try:
            view = await self._map_cache.async_get_view(
                lambda: self.coordinator.client.async_refresh_vector_map_view(
                    label_scale=self._map_label_scale,
                    current_map_index=self._selected_map_index,
                    style=self._map_style,
                )
            )
            self.async_write_ha_state()
            return view
        except Exception as err:
            safe_error = sanitize_diagnostic_text(err)
            _LOGGER.warning(
                "Failed to refresh Dreame mower live-path map image: %s", safe_error
            )
            record_diagnostic_event(
                self.coordinator,
                code="live_path_map_refresh_failed",
                source="map_camera",
                message=safe_error,
            )
            view = self._map_cache.store_error(safe_error, source="batch_vector_map")
            self.async_write_ha_state()
            return view


class DreameLawnMowerMapDataCamera(DreameLawnMowerMapCamera):
    """Disabled-by-default map diagnostics camera."""

    _attr_name = "Map Diagnostics"
    _supports_restart_preview = False
    _attr_icon = "mdi:code-json"
    _attr_entity_registry_enabled_default = False
    _requires_map_capability = False
    _prewarm_map_image = False
    _refresh_cached_view_on_coordinator_update = False

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        map_cache: DreameLawnMowerMapCameraCache,
    ) -> None:
        super().__init__(coordinator, map_cache)
        self._attr_unique_id = f"{self._descriptor.unique_id}_map_data"
        self.content_type = "image/jpeg"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the latest structured map view for diagnostics."""
        attributes = super().extra_state_attributes
        if self._map_cache.last_view is not None:
            attributes["map_view"] = self._map_cache.last_view.as_dict()
        return attributes

    async def _async_camera_image_impl(self) -> bytes | None:
        """Return a readable diagnostics card as JPEG bytes."""
        view = self._map_cache.last_view
        if view is None:
            view = await self._async_refresh_map_view()
        elif not self._map_cache.is_fresh():
            self._start_map_refresh()
        summary = view.summary
        lines = [
            f"Device: {self._descriptor.name} ({self._descriptor.display_model})",
            f"Source: {view.source}",
            f"Available: {view.available}",
            f"Has rendered image: {view.has_image}",
            f"Error: {view.error or 'none'}",
        ]
        if summary is not None:
            lines.extend(
                [
                    f"Map ID: {summary.map_id}",
                    f"Frame ID: {summary.frame_id}",
                    f"Size: {summary.width} x {summary.height}",
                    f"Segments: {summary.segment_count}",
                    f"Path points: {summary.path_point_count}",
                    f"No-go areas: {summary.no_go_area_count}",
                    f"Spot areas: {summary.spot_area_count}",
                    f"Virtual walls: {summary.virtual_wall_count}",
                    f"Robot present: {summary.robot_present}",
                    f"Charger present: {summary.charger_present}",
                ]
            )
        else:
            lines.append("Summary: no structured map payload was returned.")
        if view.app_maps:
            maps = view.app_maps.get("maps")
            lines.extend(
                [
                    f"App map count: {view.app_maps.get('map_count')}",
                    f"Current app map: {view.app_maps.get('current_map_index')}",
                    f"Available app maps: {view.app_maps.get('available_map_count')}",
                    f"3D map objects: {view.app_maps.get('object_count')}",
                ]
            )
            if isinstance(maps, list):
                for entry in maps[:6]:
                    if not isinstance(entry, dict):
                        continue
                    lines.append(
                        "Map {idx}: current={current} available={available} "
                        "areas={areas} points={points}".format(
                            idx=entry.get("idx"),
                            current=entry.get("current"),
                            available=entry.get("available"),
                            areas=entry.get("map_area_count"),
                            points=entry.get("boundary_point_count"),
                        )
                    )

        return await self.hass.async_add_executor_job(
            partial(map_diagnostics_jpeg, lines=lines)
        )


class DreameLawnMowerAllMapsCamera(DreameLawnMowerMapCamera):
    """Disabled-by-default contact sheet of all mower app maps."""

    _attr_name = "All Maps"
    _supports_restart_preview = False
    _attr_icon = "mdi:map-multiple-outline"
    _attr_entity_registry_enabled_default = False
    _requires_map_capability = False
    _prewarm_map_image = False
    _refresh_cached_view_on_coordinator_update = False

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        map_cache: DreameLawnMowerMapCameraCache,
    ) -> None:
        super().__init__(coordinator, map_cache)
        self._attr_unique_id = f"{self._descriptor.unique_id}_all_maps"
        self.content_type = "image/jpeg"

    async def _async_camera_image_impl(self) -> bytes | None:
        """Return cached bytes while one contact-sheet refresh runs."""
        if self._map_cache.last_image is not None:
            if not self._map_cache.is_fresh():
                self._start_map_refresh()
            return self._map_cache.last_image

        self._start_map_refresh()
        return await self.hass.async_add_executor_job(
            partial(
                map_placeholder_jpeg,
                title="Dreame all maps loading",
                detail="The map contact sheet is being prepared in the background.",
            )
        )

    async def _async_refresh_and_render_map_image(self) -> bytes | None:
        """Fetch and cache a contact sheet without blocking camera requests."""
        render_context = self._map_render_context
        try:
            app_maps = await self.coordinator.client.async_get_app_maps(
                include_payload=True,
                include_objects=False,
            )
            image = await self.hass.async_add_executor_job(
                partial(
                    _all_maps_contact_sheet_from_payload,
                    app_maps,
                    label_scale=self._map_label_scale,
                    style=self._map_style,
                    rotations={
                        item["idx"]: self._rotation_for_map_index(item["idx"])
                        for item in app_maps.get("maps", [])
                        if isinstance(item, dict) and isinstance(item.get("idx"), int)
                    },
                )
            )
            if self._map_render_context != render_context:
                self._map_refresh_pending = True
                return None
            self._map_cache.store_view(
                DreameLawnMowerMapView(source="app_maps_contact_sheet")
            )
            self._map_cache.store_image(image)
            self.async_write_ha_state()
            return image
        except Exception as err:
            safe_error = sanitize_diagnostic_text(err)
            _LOGGER.warning(
                "Failed to refresh Dreame mower all-map image: %s", safe_error
            )
            record_diagnostic_event(
                self.coordinator,
                code="all_maps_refresh_failed",
                source="map_camera",
                message=safe_error,
            )
            self._map_cache.store_error(
                safe_error,
                source="app_maps_contact_sheet",
            )
            image = self._map_cache.last_image
            if image is None:
                image = await self.hass.async_add_executor_job(
                    partial(
                        map_placeholder_jpeg,
                        title="Dreame all maps unavailable",
                        detail=safe_error,
                    )
                )
                self._map_cache.store_image(image, placeholder=True)
            self.async_write_ha_state()
            return image


def _all_maps_contact_sheet_from_payload(
    app_maps: dict[str, Any],
    *,
    label_scale: float = 1.0,
    style: MapRenderStyle | None = None,
    rotations: dict[int, int] | None = None,
) -> bytes:
    """Render all drawable app map payloads into one contact sheet."""
    rendered: list[dict[str, object]] = []
    maps = app_maps.get("maps")
    if isinstance(maps, list):
        for item in maps:
            if not isinstance(item, dict):
                continue
            entry: dict[str, object] = {
                "idx": item.get("idx"),
                "current": item.get("current"),
                "summary": item.get("summary"),
            }
            payload = item.get("payload")
            try:
                image_png, width, height = render_app_map_payload_png(
                    payload,
                    label_scale=label_scale,
                    style=replace(
                        style or map_render_style(),
                        rotation=(rotations or {}).get(
                            item.get("idx"), (style or map_render_style()).rotation
                        ),
                    ),
                )
                entry.update(
                    {
                        "image_png": image_png,
                        "width": width,
                        "height": height,
                    }
                )
            except (TypeError, ValueError) as err:
                entry["error"] = str(err)
            rendered.append(entry)
    return app_maps_contact_sheet_jpeg(
        maps=rendered,
        map_count=_int_or_none(app_maps.get("map_count")),
        current_map_index=_int_or_none(app_maps.get("current_map_index")),
    )


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None
