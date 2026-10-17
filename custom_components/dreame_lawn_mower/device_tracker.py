"""GPS position of the mower, from validated map positions and a calibration."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity
from .georeference import current_georeference_map_index, entry_georeference
from .runtime_data import DreameLawnMowerConfigEntry

PARALLEL_UPDATES = 0

# HA's default zero means no measured accuracy is supplied. Manual alignment
# cannot establish a metre-level accuracy bound for every garden and model.
LOCATION_ACCURACY_METERS = 0

# Only evidence that describes where the mower is now. A ``last_known`` pose
# is history and must never be presented as the current location.
CURRENT_POSITION_STATUSES = frozenset({"current", "known_dock"})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DreameLawnMowerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the mower GPS tracker."""
    async_add_entities([DreameLawnMowerGpsTracker(entry.runtime_data)])


class DreameLawnMowerGpsTracker(DreameLawnMowerEntity, TrackerEntity):
    """Project the camera's validated mower position through the calibration."""

    _attr_translation_key = "gps_position"
    _attr_source_type = SourceType.GPS
    _attr_location_accuracy = LOCATION_ACCURACY_METERS

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_gps_position"
        self._position_status: str | None = None
        self._position_observed_at: str | None = None
        self._written_available: bool | None = None
        self._update_position()
        self._written_available = self.available

    @property
    def force_update(self) -> bool:
        """Write state only when the position or its evidence changes."""
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Describe the evidence behind the published coordinates.

        ``position_observed_at`` is when the evidence for the published state
        was first seen. It is not refreshed while the location and status stay
        the same (a docked mower), so it is not a freshness signal: the status
        is. ``current`` expires on its own when telemetry stops and
        ``known_dock`` lasts only while the mower reports being docked.
        """
        return {
            "position_status": self._position_status,
            "position_observed_at": self._position_observed_at,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        # Repeated identical evidence would write one history row per poll.
        if self._update_position() or self.available != self._written_available:
            super()._handle_coordinator_update()
            self._written_available = self.available

    def _update_position(self) -> bool:
        """Recompute the position, returning whether the published one changed."""
        reader = getattr(self.coordinator.client, "current_map_position", None)
        position = reader() if callable(reader) else None
        map_index = current_georeference_map_index(self.coordinator)
        georeference = entry_georeference(
            self.coordinator.entry,
            self.coordinator.app_maps,
            map_index
            if position is not None and position.map_index == map_index
            else None,
        )
        latitude = longitude = None
        observed_at = None
        if position is None or position.status not in CURRENT_POSITION_STATUSES:
            status = position.status if position is not None else "unavailable"
        else:
            status = position.status
            observed_at = datetime.fromtimestamp(position.observed_at, UTC).isoformat()
            if georeference is not None:
                latitude, longitude = georeference.to_gps(position.x, position.y)
                if (
                    not math.isfinite(latitude)
                    or not math.isfinite(longitude)
                    or not -90 <= latitude <= 90
                    or not -180 <= longitude <= 180
                ):
                    latitude = longitude = None
        if (latitude, longitude, status) == (
            self._attr_latitude,
            self._attr_longitude,
            self._position_status,
        ):
            return False
        self._attr_latitude, self._attr_longitude = latitude, longitude
        self._position_status = status
        self._position_observed_at = observed_at
        return True
