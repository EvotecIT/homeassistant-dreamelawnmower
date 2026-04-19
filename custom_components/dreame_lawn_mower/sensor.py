"""Sensors for Dreame lawn mower."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity
from .manual_control import remote_control_block_reason


@dataclass(frozen=True, slots=True)
class DreameSensorDescription:
    key: str
    name: str
    value_fn: Callable[[Any], Any]
    exists_fn: Callable[[Any], bool] = lambda _: True
    entity_registry_enabled_default: bool = True
    entity_registry_visible_default: bool = True
    translation_key: str | None = None
    translation_placeholders: dict[str, str] | None = None
    force_update: bool = False
    device_class: SensorDeviceClass | None = None
    unit_of_measurement: str | None = None
    native_unit_of_measurement: str | None = None
    suggested_unit_of_measurement: str | None = None
    suggested_display_precision: int | None = None
    state_class: SensorStateClass | str | None = None
    last_reset: datetime | None = None
    options: list[str] | None = None
    icon: str | None = None
    entity_category: EntityCategory | None = None


def _raw_attribute(snapshot: Any, key: str) -> Any:
    """Return a raw mower attribute when available."""
    return snapshot.raw_attributes.get(key)


SENSORS = [
    DreameSensorDescription(
        key="activity",
        name="Activity",
        value_fn=lambda snapshot: snapshot.activity,
        icon="mdi:robot-mower",
    ),
    DreameSensorDescription(
        key="state_name",
        name="State Name",
        value_fn=lambda snapshot: snapshot.state_name,
        icon="mdi:state-machine",
    ),
    DreameSensorDescription(
        key="task_status",
        name="Task Status",
        value_fn=lambda snapshot: snapshot.task_status_name or "unknown",
        icon="mdi:clipboard-text-clock-outline",
    ),
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
        value_fn=lambda snapshot: snapshot.error_display or "none",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="error_code",
        name="Error Code",
        value_fn=lambda snapshot: "none"
        if snapshot.error_code in (None, -1, 0)
        else snapshot.error_code,
        icon="mdi:numeric",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    DreameSensorDescription(
        key="raw_error",
        name="Raw Error",
        value_fn=lambda snapshot: getattr(snapshot, "error_text", None) or "none",
        icon="mdi:text-box-search-outline",
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
        key="unknown_property_count",
        name="Unknown Property Count",
        value_fn=lambda snapshot: getattr(snapshot, "unknown_property_count", 0),
        icon="mdi:help-box-multiple-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="realtime_property_count",
        name="Realtime Property Count",
        value_fn=lambda snapshot: getattr(snapshot, "realtime_property_count", 0),
        icon="mdi:transit-connection-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="last_realtime_method",
        name="Last Realtime Method",
        value_fn=lambda snapshot: getattr(snapshot, "last_realtime_method", None),
        icon="mdi:message-badge-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="manual_drive_block_reason",
        name="Manual Drive Block Reason",
        value_fn=lambda snapshot: remote_control_block_reason(snapshot) or "none",
        icon="mdi:shield-alert-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    DreameSensorDescription(
        key="cleaning_mode",
        name="Cleaning Mode",
        value_fn=lambda snapshot: snapshot.cleaning_mode_name,
        exists_fn=lambda snapshot: bool(snapshot.cleaning_mode_name)
        and snapshot.cleaning_mode_name != "unknown",
        icon="mdi:grass",
        entity_registry_enabled_default=False,
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
    async_add_entities(
        [
            DreameLawnMowerSensor(coordinator, description)
            for description in SENSORS
        ]
        + [DreameLawnMowerLastScheduleWriteSensor(coordinator)]
        + [DreameLawnMowerLastScheduleProbeSensor(coordinator)]
        + [DreameLawnMowerLastPreferenceProbeSensor(coordinator)]
        + [DreameLawnMowerLastWeatherProbeSensor(coordinator)]
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
        if not self.available:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return whether the sensor currently has meaningful mower data."""
        snapshot = self.coordinator.data
        return snapshot is not None and self.entity_description.exists_fn(snapshot)


class DreameLawnMowerLastScheduleWriteSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last guarded schedule write or dry-run result."""

    _attr_name = "Last Schedule Write"
    _attr_icon = "mdi:calendar-edit"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_last_schedule_write"

    @property
    def native_value(self) -> str:
        """Return a compact state for the last schedule write result."""
        return _schedule_write_state(self.coordinator.last_schedule_write_result)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last schedule write result."""
        return schedule_write_result_attributes(
            self.coordinator.last_schedule_write_result
        )


def _schedule_write_state(result: dict[str, Any] | None) -> str:
    if not result:
        return "none"
    return "executed" if result.get("executed") else "dry_run"


def schedule_write_result_attributes(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact, non-secret attributes for a schedule write result."""
    if not result:
        return {}

    target_plan = result.get("target_plan")
    schedule = result.get("schedule")
    attributes: dict[str, Any] = {
        "source": result.get("source"),
        "action": result.get("action"),
        "dry_run": result.get("dry_run"),
        "executed": result.get("executed"),
        "changed": result.get("changed"),
        "map_index": result.get("map_index"),
        "plan_id": result.get("plan_id"),
        "previous_enabled": result.get("previous_enabled"),
        "enabled": result.get("enabled"),
        "version": result.get("version"),
        "request": result.get("request"),
    }
    if isinstance(schedule, dict):
        attributes["schedule"] = schedule
    if isinstance(target_plan, dict):
        attributes["target_plan"] = target_plan
    if result.get("response_data") is not None:
        attributes["response_data"] = result.get("response_data")
    return {
        key: value
        for key, value in attributes.items()
        if value is not None
    }


class DreameLawnMowerLastScheduleProbeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last read-only schedule probe result."""

    _attr_name = "Last Schedule Probe"
    _attr_icon = "mdi:calendar-search"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_last_schedule_probe"

    @property
    def native_value(self) -> str:
        """Return a compact state for the last schedule probe."""
        return _schedule_probe_state(self.coordinator.last_schedule_probe_result)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last schedule probe."""
        return schedule_probe_result_attributes(
            self.coordinator.last_schedule_probe_result
        )


def _schedule_probe_state(result: dict[str, Any] | None) -> str:
    if not result:
        return "none"
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        return "error"
    return "available" if result.get("available") else "unavailable"


def schedule_probe_result_attributes(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact, non-secret attributes for a schedule probe result."""
    if not result:
        return {}

    errors = result.get("errors")
    schedules = [
        _schedule_probe_entry_summary(schedule)
        for schedule in result.get("schedules", [])
        if isinstance(schedule, dict)
    ]
    attributes: dict[str, Any] = {
        "captured_at": result.get("captured_at"),
        "source": result.get("source"),
        "available": result.get("available"),
        "current_task": result.get("current_task"),
        "schedule_selection": result.get("schedule_selection"),
        "schedule_count": len(schedules),
        "schedules": schedules,
    }
    if isinstance(errors, list):
        attributes["error_count"] = len(errors)
        attributes["errors"] = errors
    return {
        key: value
        for key, value in attributes.items()
        if value not in (None, [], {})
    }


def _schedule_probe_entry_summary(schedule: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "idx": schedule.get("idx"),
        "label": schedule.get("label"),
        "available": schedule.get("available"),
        "version": schedule.get("version"),
        "plan_count": schedule.get("plan_count"),
        "enabled_plan_count": schedule.get("enabled_plan_count"),
        "error": schedule.get("error"),
    }
    return {
        key: value
        for key, value in summary.items()
        if value is not None
    }


class DreameLawnMowerLastPreferenceProbeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last read-only mowing preference probe result."""

    _attr_name = "Last Preference Probe"
    _attr_icon = "mdi:tune-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_last_preference_probe"

    @property
    def native_value(self) -> str:
        """Return a compact state for the last preference probe."""
        return _preference_probe_state(
            self.coordinator.last_preference_probe_result,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last preference probe."""
        return preference_probe_result_attributes(
            self.coordinator.last_preference_probe_result,
        )


def _preference_probe_state(result: dict[str, Any] | None) -> str:
    if not result:
        return "none"
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        return "error"
    return "available" if result.get("available") else "unavailable"


def preference_probe_result_attributes(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact, non-secret attributes for a preference probe result."""
    if not result:
        return {}

    errors = result.get("errors")
    maps = [
        _preference_probe_map_summary(map_entry)
        for map_entry in result.get("maps", [])
        if isinstance(map_entry, dict)
    ]
    attributes: dict[str, Any] = {
        "captured_at": result.get("captured_at"),
        "source": result.get("source"),
        "available": result.get("available"),
        "property_hint": result.get("property_hint"),
        "map_count": len(maps),
        "maps": maps,
    }
    if isinstance(errors, list):
        attributes["error_count"] = len(errors)
        attributes["errors"] = errors
    return {
        key: value
        for key, value in attributes.items()
        if value not in (None, [], {})
    }


class DreameLawnMowerLastWeatherProbeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last read-only weather/rain protection probe result."""

    _attr_name = "Last Weather Probe"
    _attr_icon = "mdi:weather-pouring"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_last_weather_probe"

    @property
    def native_value(self) -> str:
        """Return a compact state for the last weather probe."""
        return _weather_probe_state(
            self.coordinator.last_weather_probe_result,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last weather probe."""
        return weather_probe_result_attributes(
            self.coordinator.last_weather_probe_result,
        )


def _weather_probe_state(result: dict[str, Any] | None) -> str:
    if not result:
        return "none"
    errors = result.get("errors")
    if isinstance(errors, list) and errors and not result.get("available"):
        return "error"
    if result.get("rain_protection_active"):
        return "rain_delay_active"
    if result.get("rain_protection_enabled"):
        return "rain_protection_enabled"
    return "available" if result.get("available") else "unavailable"


def weather_probe_result_attributes(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact, non-secret attributes for a weather probe result."""
    if not result:
        return {}

    errors = result.get("errors")
    warnings = result.get("warnings")
    attributes: dict[str, Any] = {
        "captured_at": result.get("captured_at"),
        "source": result.get("source"),
        "available": result.get("available"),
        "fault_hint": result.get("fault_hint"),
        "present_config_keys": result.get("present_config_keys"),
        "weather_switch_enabled": result.get("weather_switch_enabled"),
        "rain_protection_enabled": result.get("rain_protection_enabled"),
        "rain_protection_duration_hours": result.get(
            "rain_protection_duration_hours",
        ),
        "rain_sensor_sensitivity": result.get("rain_sensor_sensitivity"),
        "rain_protect_end_time": result.get("rain_protect_end_time"),
        "rain_protect_end_time_iso": result.get("rain_protect_end_time_iso"),
        "rain_protect_end_time_present": result.get(
            "rain_protect_end_time_present",
        ),
        "rain_protection_active": result.get("rain_protection_active"),
        "rain_protection_raw": result.get("rain_protection_raw"),
    }
    if isinstance(errors, list):
        attributes["error_count"] = len(errors)
        attributes["errors"] = errors
    if isinstance(warnings, list):
        attributes["warning_count"] = len(warnings)
        attributes["warnings"] = warnings
    return {
        key: value
        for key, value in attributes.items()
        if value not in (None, [], {})
    }


def _preference_probe_map_summary(map_entry: dict[str, Any]) -> dict[str, Any]:
    preferences = [
        _preference_probe_entry_summary(preference)
        for preference in map_entry.get("preferences", [])
        if isinstance(preference, dict)
    ]
    summary = {
        "idx": map_entry.get("idx"),
        "label": map_entry.get("label"),
        "available": map_entry.get("available"),
        "mode": map_entry.get("mode"),
        "mode_name": map_entry.get("mode_name"),
        "area_count": map_entry.get("area_count"),
        "preference_count": len(preferences),
        "preferences": preferences,
        "error": map_entry.get("error"),
    }
    return {
        key: value
        for key, value in summary.items()
        if value not in (None, [], {})
    }


def _preference_probe_entry_summary(preference: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "area_id": preference.get("area_id"),
        "reported_version": preference.get("reported_version"),
        "version": preference.get("version"),
        "efficient_mode_name": preference.get("efficient_mode_name"),
        "mowing_height_cm": preference.get("mowing_height_cm"),
        "mowing_direction_mode_name": preference.get("mowing_direction_mode_name"),
        "mowing_direction_degrees": preference.get("mowing_direction_degrees"),
        "edge_mowing_auto": preference.get("edge_mowing_auto"),
        "edge_mowing_safe": preference.get("edge_mowing_safe"),
        "edge_mowing_walk_mode_name": preference.get("edge_mowing_walk_mode_name"),
        "cutter_position_name": preference.get("cutter_position_name"),
        "edge_mowing_num": preference.get("edge_mowing_num"),
        "edge_mowing_obstacle_avoidance": preference.get(
            "edge_mowing_obstacle_avoidance",
        ),
        "obstacle_avoidance_enabled": preference.get("obstacle_avoidance_enabled"),
        "obstacle_avoidance_height_cm": preference.get(
            "obstacle_avoidance_height_cm",
        ),
        "obstacle_avoidance_distance_cm": preference.get(
            "obstacle_avoidance_distance_cm",
        ),
        "obstacle_avoidance_ai_classes": preference.get(
            "obstacle_avoidance_ai_classes",
        ),
    }
    return {
        key: value
        for key, value in summary.items()
        if value not in (None, [], {})
    }
