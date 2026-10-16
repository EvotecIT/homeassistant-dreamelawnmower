"""GPS position of the mower, from validated map positions and a calibration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity
from .georeference import entry_georeference
from .runtime_data import DreameLawnMowerConfigEntry

PARALLEL_UPDATES = 0

# Reported accuracy in metres. The map pose itself is finer than this; the
# bound is dominated by how well a hand-set calibration lines the map up with
# aerial imagery. It is a conservative estimate, not a measured figure.
LOCATION_ACCURACY_METERS = 2

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
        self._update_position()

    @property
    def force_update(self) -> bool:
        """Write state only when the position or its evidence changes."""
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Describe the evidence behind the published coordinates."""
        return {
            "position_status": self._position_status,
            "position_observed_at": self._position_observed_at,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_position()
        super()._handle_coordinator_update()

    def _update_position(self) -> None:
        georeference = entry_georeference(self.coordinator.entry)
        reader = getattr(self.coordinator.client, "current_map_position", None)
        position = reader() if callable(reader) else None
        if position is None or position.status not in CURRENT_POSITION_STATUSES:
            self._attr_latitude = self._attr_longitude = None
            self._position_status = (
                position.status if position is not None else "unavailable"
            )
            self._position_observed_at = None
            return
        self._position_status = position.status
        self._position_observed_at = datetime.fromtimestamp(
            position.observed_at, UTC
        ).isoformat()
        if georeference is None:
            self._attr_latitude = self._attr_longitude = None
            return
        self._attr_latitude, self._attr_longitude = georeference.to_gps(
            position.x, position.y
        )
