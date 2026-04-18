"""Experimental map camera for Dreame lawn mower."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DreameLawnMowerCoordinator
from .dreame_client.models import DreameLawnMowerMapView
from .image import map_diagnostics_jpeg, map_placeholder_jpeg, png_bytes_to_jpeg
from .map_attributes import map_camera_attributes

_LOGGER = logging.getLogger(__name__)
_MAP_CACHE_TTL = timedelta(seconds=60)
_MAP_TIMEOUT_SECONDS = 6.0
_MAP_POLL_INTERVAL_SECONDS = 0.5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the mower map camera."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DreameLawnMowerMapCamera(coordinator),
            DreameLawnMowerMapDataCamera(coordinator),
        ]
    )


class DreameLawnMowerMapCamera(
    CoordinatorEntity[DreameLawnMowerCoordinator],
    Camera,
):
    """Experimental read-only mower map camera."""

    _attr_has_entity_name = True
    _attr_name = "Map"
    _attr_icon = "mdi:map-search-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        Camera.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self._descriptor = coordinator.client.descriptor
        self._attr_unique_id = f"{self._descriptor.unique_id}_map"
        self._attr_brand = "Dreametech"
        self._attr_model = self._descriptor.display_model
        self.content_type = "image/jpeg"
        self._last_image: bytes | None = None
        self._last_view: DreameLawnMowerMapView | None = None
        self._last_refresh_at: datetime | None = None
        self._last_error: str | None = None
        self._refresh_lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        """Return whether the entity can reasonably provide a map."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return False
        return (
            self._last_image is not None
            or snapshot.mapping_available
            or "map" in snapshot.capabilities
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
        return map_camera_attributes(
            self._last_view,
            image_cached=self._last_image is not None,
            refreshed_at=self._last_refresh_at,
            last_error=self._last_error,
        )

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Return the latest mower map image as JPEG bytes."""
        del width, height
        return await self._async_get_map_image()

    async def _async_get_map_image(self) -> bytes | None:
        """Return a cached map image or refresh it on demand."""
        if self._last_image is not None and self._cache_is_fresh():
            return self._last_image

        view = await self._async_refresh_map_view()
        if view.image_png is not None:
            try:
                self._last_image = png_bytes_to_jpeg(view.image_png)
                self._last_error = None
                self.async_write_ha_state()
                return self._last_image
            except Exception as err:
                _LOGGER.warning("Failed to convert Dreame mower map image: %s", err)
                self._last_error = str(err)
                self.async_write_ha_state()

        return self._last_image or map_placeholder_jpeg(
            detail=self._last_error or view.error
        )

    async def _async_refresh_map_view(self) -> DreameLawnMowerMapView:
        """Return a cached map view or refresh it on demand."""
        if self._last_view is not None and self._cache_is_fresh():
            return self._last_view

        async with self._refresh_lock:
            if self._last_view is not None and self._cache_is_fresh():
                return self._last_view

            try:
                view = await self.coordinator.client.async_refresh_map_view(
                    timeout=_MAP_TIMEOUT_SECONDS,
                    interval=_MAP_POLL_INTERVAL_SECONDS,
                )
            except Exception as err:
                _LOGGER.warning("Failed to refresh Dreame mower map image: %s", err)
                self._last_error = str(err)
                self._last_refresh_at = datetime.now(UTC)
                self._last_view = DreameLawnMowerMapView(
                    source="legacy_current_map",
                    error=self._last_error,
                )
                self.async_write_ha_state()
                return self._last_view

            self._last_view = view
            self._last_error = view.error
            self._last_refresh_at = datetime.now(UTC)
            self.async_write_ha_state()
            return view

    def _cache_is_fresh(self) -> bool:
        """Return whether the cached map image is still fresh."""
        return self._last_refresh_at is not None and (
            datetime.now(UTC) - self._last_refresh_at
        ) <= _MAP_CACHE_TTL


class DreameLawnMowerMapDataCamera(DreameLawnMowerMapCamera):
    """Disabled-by-default map diagnostics camera."""

    _attr_name = "Map Diagnostics"
    _attr_icon = "mdi:code-json"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_map_data"
        self.content_type = "image/jpeg"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the latest structured map view for diagnostics."""
        attributes = super().extra_state_attributes
        if self._last_view is not None:
            attributes["map_view"] = self._last_view.as_dict()
        return attributes

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Return a readable diagnostics card as JPEG bytes."""
        del width, height
        view = await self._async_refresh_map_view()
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

        return map_diagnostics_jpeg(lines=lines)
