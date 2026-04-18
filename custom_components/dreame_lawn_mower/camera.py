"""Experimental map camera for Dreame lawn mower."""

from __future__ import annotations

import logging
from datetime import timedelta
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
from .map_cache import DreameLawnMowerMapCameraCache

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
    map_cache = DreameLawnMowerMapCameraCache(ttl=_MAP_CACHE_TTL)
    async_add_entities(
        [
            DreameLawnMowerMapCamera(coordinator, map_cache),
            DreameLawnMowerMapDataCamera(coordinator, map_cache),
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

    @property
    def available(self) -> bool:
        """Return whether the entity can reasonably provide a map."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return False
        return (
            self._map_cache.last_image is not None
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
            self._map_cache.last_view,
            image_cached=self._map_cache.last_image is not None,
            refreshed_at=self._map_cache.last_refresh_at,
            last_error=self._map_cache.last_error,
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
        if self._map_cache.last_image is not None and self._map_cache.is_fresh():
            return self._map_cache.last_image

        view = await self._async_refresh_map_view()
        if view.image_png is not None:
            try:
                image = png_bytes_to_jpeg(view.image_png)
                self._map_cache.store_image(image)
                self._map_cache.last_error = None
                self.async_write_ha_state()
                return image
            except Exception as err:
                _LOGGER.warning("Failed to convert Dreame mower map image: %s", err)
                self._map_cache.last_error = str(err)
                self.async_write_ha_state()

        return self._map_cache.last_image or map_placeholder_jpeg(
            detail=self._map_cache.last_error or view.error
        )

    async def _async_refresh_map_view(self) -> DreameLawnMowerMapView:
        """Return a cached map view or refresh it on demand."""
        try:
            view = await self._map_cache.async_get_view(
                lambda: self.coordinator.client.async_refresh_map_view(
                    timeout=_MAP_TIMEOUT_SECONDS,
                    interval=_MAP_POLL_INTERVAL_SECONDS,
                )
            )
            self.async_write_ha_state()
            return view
        except Exception as err:
            _LOGGER.warning("Failed to refresh Dreame mower map image: %s", err)
            view = self._map_cache.store_error(str(err))
            self.async_write_ha_state()
            return view


class DreameLawnMowerMapDataCamera(DreameLawnMowerMapCamera):
    """Disabled-by-default map diagnostics camera."""

    _attr_name = "Map Diagnostics"
    _attr_icon = "mdi:code-json"

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
