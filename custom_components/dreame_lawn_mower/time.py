"""Time entities for mower-native charging-period settings."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DreameLawnMowerCoordinator
from .device_settings_control import (
    device_settings_section,
    minutes_to_time,
    time_to_minutes,
)
from .entity import DreameLawnMowerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up charging-period time entities."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities_added = False

    @callback
    def async_add_supported_entities() -> None:
        nonlocal entities_added
        settings = device_settings_section(coordinator.device_settings)
        if (
            entities_added
            or settings is None
            or not settings.get("charging_settings_available")
        ):
            return
        entities_added = True
        async_add_entities(
            [
                DreameLawnMowerChargingPeriodStartTime(coordinator),
                DreameLawnMowerChargingPeriodEndTime(coordinator),
            ]
        )

    async_add_supported_entities()
    entry.async_on_unload(coordinator.async_add_listener(async_add_supported_entities))


class DreameLawnMowerChargingPeriodTime(DreameLawnMowerEntity, TimeEntity):
    """Base for one end of the mower-native charging window."""

    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        settings = device_settings_section(self.coordinator.device_settings)
        return bool(
            self.coordinator.data is not None
            and settings
            and settings.get("charging_settings_available")
        )

    @property
    def native_value(self) -> time | None:
        return minutes_to_time(self._minutes)

    @property
    def _minutes(self) -> int | None:
        raise NotImplementedError


class DreameLawnMowerChargingPeriodStartTime(DreameLawnMowerChargingPeriodTime):
    """Start of the mower-native charging period."""

    _attr_name = "Charging Period Start"
    _attr_icon = "mdi:clock-start"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_charging_period_start"

    @property
    def _minutes(self) -> int | None:
        settings = device_settings_section(self.coordinator.device_settings)
        if settings is None:
            return None
        value = settings.get("charging_period_start_minutes")
        return int(value) if value is not None else None

    async def async_set_value(self, value: time) -> None:
        await self.coordinator.async_set_charging_period(
            start_minutes=time_to_minutes(value)
        )


class DreameLawnMowerChargingPeriodEndTime(DreameLawnMowerChargingPeriodTime):
    """End of the mower-native charging period."""

    _attr_name = "Charging Period End"
    _attr_icon = "mdi:clock-end"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_charging_period_end"

    @property
    def _minutes(self) -> int | None:
        settings = device_settings_section(self.coordinator.device_settings)
        if settings is None:
            return None
        value = settings.get("charging_period_end_minutes")
        return int(value) if value is not None else None

    async def async_set_value(self, value: time) -> None:
        await self.coordinator.async_set_charging_period(
            end_minutes=time_to_minutes(value)
        )
