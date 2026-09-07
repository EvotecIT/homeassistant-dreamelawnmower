"""Locally observed timing, kept distinct from device-reported duration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .entity import DreameLawnMowerEntity


class DreameLawnMowerObservedMowingTimeSensor(DreameLawnMowerEntity, SensorEntity):
    """Report time observed in mowing state, with explicit completeness limits."""

    _attr_name = "Observed Mowing Time"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "min"
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: Any) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_observed_mowing_time"

    @property
    def native_value(self) -> float | None:
        timer = getattr(self.coordinator, "observed_mowing_timer", None)
        return timer.minutes if timer is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        timer = getattr(self.coordinator, "observed_mowing_timer", None)
        return timer.attributes() if timer is not None else {}
