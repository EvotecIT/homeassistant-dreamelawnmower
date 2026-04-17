"""Experimental map camera for Dreame lawn mower."""

from __future__ import annotations

import asyncio
import json
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
from .image import map_placeholder_jpeg, png_bytes_to_jpeg

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
        view = self._last_view
        summary = None if view is None else view.summary
        refreshed_at = self._last_refresh_at
        return {
            "map_cached": self._last_image is not None,
            "map_placeholder": self._last_image is None,
            "map_source": None if view is None else view.source,
            "map_has_image": False if view is None else view.has_image,
            "map_error": self._last_error or (None if view is None else view.error),
            "map_available": summary.available if summary is not None else None,
            "map_id": None if summary is None else summary.map_id,
            "frame_id": None if summary is None else summary.frame_id,
            "timestamp_ms": None if summary is None else summary.timestamp_ms,
            "rotation": None if summary is None else summary.rotation,
            "width": None if summary is None else summary.width,
            "height": None if summary is None else summary.height,
            "grid_size": None if summary is None else summary.grid_size,
            "saved_map": None if summary is None else summary.saved_map,
            "temporary_map": None if summary is None else summary.temporary_map,
            "recovery_map": None if summary is None else summary.recovery_map,
            "empty_map": None if summary is None else summary.empty_map,
            "segment_count": None if summary is None else summary.segment_count,
            "active_segment_count": (
                None if summary is None else summary.active_segment_count
            ),
            "active_area_count": None if summary is None else summary.active_area_count,
            "active_point_count": (
                None if summary is None else summary.active_point_count
            ),
            "path_point_count": None if summary is None else summary.path_point_count,
            "no_go_area_count": None if summary is None else summary.no_go_area_count,
            "virtual_wall_count": (
                None if summary is None else summary.virtual_wall_count
            ),
            "pathway_count": None if summary is None else summary.pathway_count,
            "obstacle_count": None if summary is None else summary.obstacle_count,
            "charger_present": None if summary is None else summary.charger_present,
            "robot_present": None if summary is None else summary.robot_present,
            "last_map_refresh": (
                None if refreshed_at is None else refreshed_at.isoformat()
            ),
        }

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
    """Disabled-by-default JSON map data camera for diagnostics and custom cards."""

    _attr_name = "Map Data"
    _attr_icon = "mdi:code-json"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_map_data"
        self.content_type = "application/json"

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Return structured map data as JSON bytes."""
        del width, height
        view = await self._async_refresh_map_view()
        payload = {
            "device": {
                "name": self._descriptor.name,
                "model": self._descriptor.model,
                "display_model": self._descriptor.display_model,
            },
            "map": view.as_dict(),
        }
        return json.dumps(payload, sort_keys=True).encode()
