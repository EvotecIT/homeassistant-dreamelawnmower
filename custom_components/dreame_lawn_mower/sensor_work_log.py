"""Lifetime mower work-log total sensors."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)

from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity


class _DreameLawnMowerWorkLogTotalSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Base class for one mower-owned lifetime total."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        *,
        key: str,
        name: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{self._descriptor.unique_id}_{key}"

    @property
    def available(self) -> bool:
        """Return whether authoritative lifetime totals have been cached."""
        return self.coordinator.data is not None and self.native_value is not None


class DreameLawnMowerTotalMowedAreaSensor(
    _DreameLawnMowerWorkLogTotalSensor,
):
    """Expose lifetime mowed area from MIHIS."""

    _attr_device_class = SensorDeviceClass.AREA
    _attr_native_unit_of_measurement = "m²"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(
            coordinator,
            key="total_mowed_area",
            name="Total Mowed Area",
            icon="mdi:texture-box",
        )

    @property
    def native_value(self) -> float | None:
        """Return lifetime mowed area in square metres."""
        totals = self.coordinator.work_log_totals
        return totals.total_mowed_area_sqm if totals is not None else None


class DreameLawnMowerTotalMowingTimeSensor(
    _DreameLawnMowerWorkLogTotalSensor,
):
    """Expose lifetime mowing time from MIHIS."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(
            coordinator,
            key="total_mowing_time",
            name="Total Mowing Time",
            icon="mdi:timer-sand-complete",
        )

    @property
    def native_value(self) -> int | None:
        """Return lifetime mowing time in minutes."""
        totals = self.coordinator.work_log_totals
        return totals.total_mowing_time_minutes if totals is not None else None


class DreameLawnMowerTotalMowingSessionsSensor(
    _DreameLawnMowerWorkLogTotalSensor,
):
    """Expose lifetime mowing-session count from MIHIS."""

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(
            coordinator,
            key="total_mowing_sessions",
            name="Total Mowing Sessions",
            icon="mdi:counter",
        )

    @property
    def native_value(self) -> int | None:
        """Return lifetime mowing-session count."""
        totals = self.coordinator.work_log_totals
        return totals.total_mowing_sessions if totals is not None else None
