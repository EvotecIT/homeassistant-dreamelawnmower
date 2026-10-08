"""A compact diagnostic entity for model and protocol support."""

from __future__ import annotations

from typing import Any, cast

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity
from .reporting import coordinator_compatibility_summary


class DreameLawnMowerCompatibilitySensor(DreameLawnMowerEntity, SensorEntity):
    """Present capability evidence and the discovered native schedule protocol."""

    _attr_name = "Compatibility"
    _attr_icon = "mdi:information-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_compatibility"

    @property
    def native_value(self) -> str:
        return cast(
            str,
            coordinator_compatibility_summary(self.coordinator)["schedule_protocol"],
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return coordinator_compatibility_summary(self.coordinator)
