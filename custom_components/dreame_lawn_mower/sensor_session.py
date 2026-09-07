"""Locally observed timing, kept distinct from device-reported duration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)

from .entity import DreameLawnMowerEntity


class DreameLawnMowerObservedMowingTimeSensor(DreameLawnMowerEntity, SensorEntity):
    """Report time observed in mowing state, with explicit completeness limits."""

    _attr_name = "Observed Mowing Time"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "min"
    _attr_icon = "mdi:timer-outline"
    _unrecorded_attributes = frozenset({"last_observed_at"})

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


class DreameLawnMowerLastObservedRunSensor(DreameLawnMowerEntity, SensorEntity):
    """Keep the previous observed run useful after docking or a restart."""

    _attr_name = "Last Observed Run Duration"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "min"
    _attr_icon = "mdi:timer-check-outline"
    entity_description = SensorEntityDescription(key="last_observed_run_duration")

    def __init__(self, coordinator: Any) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_last_observed_run_duration"
        )

    @property
    def available(self) -> bool:
        """Historical evidence remains readable while the mower is offline."""
        timer = getattr(self.coordinator, "observed_mowing_timer", None)
        return timer is not None and timer.last_run is not None

    @property
    def native_value(self) -> float | None:
        timer = getattr(self.coordinator, "observed_mowing_timer", None)
        run = timer.last_run if timer is not None else None
        return round(run.seconds / 60, 1) if run is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        timer = getattr(self.coordinator, "observed_mowing_timer", None)
        run = timer.last_run if timer is not None else None
        if run is None:
            return {}
        return {
            "source": "observed_mowing_state",
            "partial": run.partial,
            "measurement_state": run.state,
            "observed_since": run.started_at.isoformat(),
            "last_observed_at": run.updated_at.isoformat(),
            "includes_pauses": False,
        }
