"""Sensors for Dreame lawn mower."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity


@dataclass(frozen=True, slots=True)
class DreameSensorDescription:
    key: str
    name: str
    value_fn: Callable[[Any], Any]
    exists_fn: Callable[[Any], bool] = lambda _: True
    device_class: SensorDeviceClass | None = None
    native_unit_of_measurement: str | None = None
    icon: str | None = None
    entity_category: EntityCategory | None = None


def _raw_attribute(snapshot: Any, key: str) -> Any:
    """Return a raw mower attribute when available."""
    return snapshot.raw_attributes.get(key)


SENSORS = [
    DreameSensorDescription(
        key="battery",
        name="Battery",
        value_fn=lambda snapshot: snapshot.battery_level,
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement="%",
    ),
    DreameSensorDescription(
        key="error",
        name="Error",
        value_fn=lambda snapshot: snapshot.error_name or "none",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="firmware_version",
        name="Firmware Version",
        value_fn=lambda snapshot: snapshot.firmware_version,
        icon="mdi:package-up",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="hardware_version",
        name="Hardware Version",
        value_fn=lambda snapshot: snapshot.hardware_version,
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="serial_number",
        name="Serial Number",
        value_fn=lambda snapshot: snapshot.serial_number,
        exists_fn=lambda snapshot: bool(snapshot.serial_number),
        icon="mdi:barcode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="cloud_update_time",
        name="Cloud Update Time",
        value_fn=lambda snapshot: snapshot.cloud_update_time,
        exists_fn=lambda snapshot: bool(snapshot.cloud_update_time),
        icon="mdi:clock-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="cleaning_mode",
        name="Cleaning Mode",
        value_fn=lambda snapshot: snapshot.cleaning_mode_name,
        exists_fn=lambda snapshot: bool(snapshot.cleaning_mode_name)
        and snapshot.cleaning_mode_name != "unknown",
        icon="mdi:grass",
    ),
    DreameSensorDescription(
        key="mower_state",
        name="Mower State",
        value_fn=lambda snapshot: _raw_attribute(snapshot, "mower_state"),
        exists_fn=lambda snapshot: bool(_raw_attribute(snapshot, "mower_state")),
        icon="mdi:robot-mower-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up mower sensors."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    snapshot = coordinator.data
    async_add_entities(
        [
            DreameLawnMowerSensor(coordinator, description)
            for description in SENSORS
            if snapshot is not None and description.exists_fn(snapshot)
        ]
    )


class DreameLawnMowerSensor(DreameLawnMowerEntity, SensorEntity):
    """Simple coordinator-backed mower sensor."""

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        description: DreameSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self._descriptor.unique_id}_{description.key}"
        self._attr_name = description.name
        self._attr_device_class = description.device_class
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_icon = description.icon
        self._attr_entity_category = description.entity_category

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)
