"""Entity helpers for Dreame lawn mower."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import DreameLawnMowerCoordinator

_OFFLINE_REPORTING_ENTITY_KEYS = frozenset(
    {"online", "device_connected", "cloud_connected"}
)


class DreameLawnMowerEntity(CoordinatorEntity[DreameLawnMowerCoordinator]):
    """Shared base entity for Dreame lawn mower entities."""

    _attr_has_entity_name = True

    def __getattribute__(self, name: str) -> Any:
        """Enforce cloud-offline availability across specialized entities."""
        if name == "available":
            try:
                coordinator = object.__getattribute__(self, "coordinator")
            except AttributeError:
                pass
            else:
                snapshot = getattr(coordinator, "data", None)
                if snapshot is not None and not getattr(snapshot, "available", True):
                    try:
                        description = object.__getattribute__(
                            self,
                            "entity_description",
                        )
                    except AttributeError:
                        return False
                    if (
                        getattr(description, "key", None)
                        not in _OFFLINE_REPORTING_ENTITY_KEYS
                    ):
                        return False
        return super().__getattribute__(name)

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._descriptor = coordinator.client.descriptor

    @property
    def device_info(self) -> dict[str, Any]:
        """Return dynamic device metadata for the registry."""
        snapshot = self.coordinator.data
        descriptor = snapshot.descriptor if snapshot is not None else self._descriptor
        return {
            "identifiers": {("dreame_lawn_mower", descriptor.unique_id)},
            "manufacturer": "Dreametech",
            "model": descriptor.display_model,
            "name": descriptor.name,
            "sw_version": getattr(snapshot, "firmware_version", None),
            "hw_version": getattr(snapshot, "hardware_version", None),
            "serial_number": getattr(snapshot, "serial_number", None),
        }
