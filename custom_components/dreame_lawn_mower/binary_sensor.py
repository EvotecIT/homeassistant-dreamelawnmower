"""Binary sensors for Dreame lawn mower."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ACTIVITY_ERROR,
    ACTIVITY_MOWING,
    ACTIVITY_PAUSED,
    ACTIVITY_RETURNING,
    DOMAIN,
)
from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity


@dataclass(frozen=True, slots=True)
class DreameBinarySensorDescription:
    """Simple metadata for a mower binary sensor."""

    key: str
    name: str
    value_fn: Callable[[Any], bool | None]
    exists_fn: Callable[[Any], bool] = lambda _: True
    entity_registry_enabled_default: bool = True
    entity_registry_visible_default: bool = True
    translation_key: str | None = None
    translation_placeholders: dict[str, str] | None = None
    force_update: bool = False
    device_class: BinarySensorDeviceClass | None = None
    unit_of_measurement: str | None = None
    icon: str | None = None
    entity_category: EntityCategory | None = None


def _raw_flag(snapshot: Any, key: str) -> bool | None:
    """Return a raw boolean flag from the mower attributes."""
    value = snapshot.raw_attributes.get(key)
    if value is None:
        return None
    return bool(value)


def _raw_docked(snapshot: Any) -> bool | None:
    """Return the vendor-reported dock flag when known."""
    return getattr(snapshot, "raw_docked", None)


BINARY_SENSORS = [
    DreameBinarySensorDescription(
        key="error_active",
        name="Error Active",
        value_fn=lambda snapshot: snapshot.activity == ACTIVITY_ERROR,
        icon="mdi:alert-circle-outline",
    ),
    DreameBinarySensorDescription(
        key="online",
        name="Online",
        value_fn=lambda snapshot: snapshot.online
        if snapshot.online is not None
        else snapshot.available,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameBinarySensorDescription(
        key="charging",
        name="Charging",
        value_fn=lambda snapshot: snapshot.charging,
        icon="mdi:battery-charging",
    ),
    DreameBinarySensorDescription(
        key="docked",
        name="Docked",
        value_fn=lambda snapshot: snapshot.docked,
        icon="mdi:home-map-marker",
    ),
    DreameBinarySensorDescription(
        key="raw_docked",
        name="Raw Docked Flag",
        value_fn=_raw_docked,
        exists_fn=lambda snapshot: _raw_docked(snapshot) is not None,
        icon="mdi:home-map-marker",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameBinarySensorDescription(
        key="paused",
        name="Paused",
        value_fn=lambda snapshot: snapshot.activity == ACTIVITY_PAUSED,
        icon="mdi:pause-circle-outline",
    ),
    DreameBinarySensorDescription(
        key="mowing",
        name="Mowing",
        value_fn=lambda snapshot: snapshot.activity == ACTIVITY_MOWING,
        icon="mdi:robot-mower-outline",
    ),
    DreameBinarySensorDescription(
        key="returning",
        name="Returning",
        value_fn=lambda snapshot: snapshot.activity == ACTIVITY_RETURNING,
        icon="mdi:home-import-outline",
    ),
    DreameBinarySensorDescription(
        key="task_active",
        name="Task Active",
        value_fn=lambda snapshot: snapshot.started,
        icon="mdi:play-circle-outline",
    ),
    DreameBinarySensorDescription(
        key="mapping_available",
        name="Mapping Available",
        value_fn=lambda snapshot: snapshot.mapping_available,
        exists_fn=lambda snapshot: snapshot.mapping_available
        or "map" in snapshot.capabilities,
        icon="mdi:map-check-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameBinarySensorDescription(
        key="scheduled_task",
        name="Scheduled Task",
        value_fn=lambda snapshot: snapshot.scheduled_clean,
        icon="mdi:calendar-check-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameBinarySensorDescription(
        key="shortcut_task",
        name="Shortcut Task",
        value_fn=lambda snapshot: snapshot.shortcut_task,
        exists_fn=lambda snapshot: snapshot.shortcut_task
        or "shortcuts" in snapshot.capabilities,
        icon="mdi:flash-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameBinarySensorDescription(
        key="child_lock",
        name="Child Lock",
        value_fn=lambda snapshot: snapshot.child_lock,
        exists_fn=lambda snapshot: snapshot.child_lock is not None,
        icon="mdi:lock-outline",
        entity_registry_enabled_default=False,
    ),
    DreameBinarySensorDescription(
        key="raw_paused",
        name="Raw Paused Flag",
        value_fn=lambda snapshot: _raw_flag(snapshot, "paused"),
        exists_fn=lambda snapshot: "paused" in snapshot.raw_attributes,
        icon="mdi:pause-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameBinarySensorDescription(
        key="raw_running",
        name="Raw Running Flag",
        value_fn=lambda snapshot: _raw_flag(snapshot, "running"),
        exists_fn=lambda snapshot: "running" in snapshot.raw_attributes,
        icon="mdi:play-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameBinarySensorDescription(
        key="raw_returning",
        name="Raw Returning Flag",
        value_fn=lambda snapshot: _raw_flag(snapshot, "returning"),
        exists_fn=lambda snapshot: "returning" in snapshot.raw_attributes,
        icon="mdi:home-import-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up mower binary sensors."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DreameLawnMowerBinarySensor(coordinator, description)
            for description in BINARY_SENSORS
        ]
    )


class DreameLawnMowerBinarySensor(DreameLawnMowerEntity, BinarySensorEntity):
    """Coordinator-backed binary sensor."""

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        description: DreameBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self._descriptor.unique_id}_{description.key}"
        self._attr_name = description.name
        self._attr_device_class = description.device_class
        self._attr_icon = description.icon
        self._attr_entity_category = description.entity_category

    @property
    def is_on(self) -> bool | None:
        """Return the binary sensor value."""
        if not self.available:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return whether the binary sensor currently has meaningful mower data."""
        snapshot = self.coordinator.data
        return snapshot is not None and self.entity_description.exists_fn(snapshot)
