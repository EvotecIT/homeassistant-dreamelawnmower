"""Entity helpers for Dreame lawn mower."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import DreameLawnMowerCoordinator


class DreameLawnMowerEntity(CoordinatorEntity[DreameLawnMowerCoordinator]):
    """Shared base entity for Dreame lawn mower entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._descriptor = coordinator.client.descriptor
        self._attr_device_info = {
            "identifiers": {("dreame_lawn_mower", self._descriptor.unique_id)},
            "manufacturer": "Dreametech",
            "model": self._descriptor.display_model,
            "name": self._descriptor.name,
            "sw_version": getattr(coordinator.data, "firmware_version", None),
            "hw_version": getattr(coordinator.data, "hardware_version", None),
        }

