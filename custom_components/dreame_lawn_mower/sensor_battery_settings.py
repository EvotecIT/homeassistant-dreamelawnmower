"""Sensors for the mower-native battery thresholds stored in the BAT record."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import PERCENTAGE, EntityCategory

from .coordinator import DreameLawnMowerCoordinator
from .device_settings_control import device_settings_section
from .entity import DreameLawnMowerEntity


class DreameLawnMowerBatteryThresholdSensor(DreameLawnMowerEntity, SensorEntity):
    """One battery threshold set in the app (Settings > Charging)."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _setting_key: str
    _unique_suffix: str

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_{self._unique_suffix}"

    def _settings(self) -> Any:
        settings = device_settings_section(self.coordinator.device_settings)
        if settings is None or not settings.get("charging_settings_available"):
            return None
        return settings

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def native_value(self) -> int | None:
        settings = self._settings()
        if settings is None:
            return None
        value = settings.get(self._setting_key)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if not 0 <= value <= 100:
            return None
        return value


class DreameLawnMowerResumeBatteryLevelSensor(DreameLawnMowerBatteryThresholdSensor):
    """Battery level at which the mower resumes an interrupted task."""

    _attr_name = "Resume Battery Level"
    _attr_icon = "mdi:battery-arrow-up"
    _setting_key = "resume_battery_level"
    _unique_suffix = "resume_battery_level"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        settings = self._settings()
        if settings is None:
            return None
        return {"resume_after_charging": bool(settings.get("resume_after_charging"))}


class DreameLawnMowerRechargeBatteryLevelSensor(DreameLawnMowerBatteryThresholdSensor):
    """Battery level at which the mower returns to the station to recharge."""

    _attr_name = "Recharge Battery Level"
    _attr_icon = "mdi:battery-arrow-down"
    _setting_key = "recharge_battery_level"
    _unique_suffix = "recharge_battery_level"
