"""Diagnostic and probe sensors for Dreame lawn mower."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity
from .task_status_probe import (
    task_status_probe_result_attributes,
    task_status_probe_state,
)


class DreameLawnMowerFirmwareUpdateStatusSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose cached firmware update status from batch device data."""

    _attr_name = "Firmware Update Status"
    _attr_icon = "mdi:update"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_firmware_update_status"

    @property
    def native_value(self) -> str | None:
        """Return a compact firmware update state."""
        return _batch_ota_status_name(self.coordinator.batch_device_data)

    @property
    def available(self) -> bool:
        """Return whether cached OTA state is available."""
        return (
            self.coordinator.data is not None
            and _batch_ota_section(self.coordinator.batch_device_data) is not None
            and self.native_value is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached OTA attributes."""
        return batch_ota_attributes(self.coordinator.batch_device_data)


class DreameLawnMowerWeatherProtectionStatusSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose cached read-only weather/rain protection state."""

    _attr_name = "Weather Protection Status"
    _attr_icon = "mdi:weather-pouring"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_weather_protection_status"

    @property
    def native_value(self) -> str | None:
        """Return a compact weather/rain protection state."""
        result = _weather_section(self.coordinator.weather_protection)
        if result is None:
            return None
        state = _weather_probe_state(result)
        return None if state == "none" else state

    @property
    def available(self) -> bool:
        """Return whether cached weather state is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached weather attributes."""
        return weather_probe_result_attributes(self.coordinator.weather_protection)


class DreameLawnMowerRainProtectionDurationSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the configured rain protection duration."""

    _attr_name = "Rain Protection Duration"
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "h"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_rain_protection_duration"

    @property
    def native_value(self) -> int | None:
        """Return the configured rain protection duration in hours."""
        result = _weather_section(self.coordinator.weather_protection)
        if result is None:
            return None
        value = result.get("rain_protection_duration_hours")
        return value if isinstance(value, int) else None

    @property
    def available(self) -> bool:
        """Return whether a configured rain protection duration exists."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached weather attributes."""
        return weather_probe_result_attributes(self.coordinator.weather_protection)


class DreameLawnMowerRainDelayEndTimeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the active rain-delay end time when the mower reports one."""

    _attr_name = "Rain Delay End Time"
    _attr_icon = "mdi:clock-end"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_rain_delay_end_time"

    @property
    def native_value(self) -> datetime | None:
        """Return the active rain-delay end time when available."""
        result = _weather_section(self.coordinator.weather_protection)
        if result is None:
            return None
        value = result.get("rain_protect_end_time_iso")
        if not isinstance(value, str) or value == "":
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @property
    def available(self) -> bool:
        """Return whether an active rain-delay end time exists."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached weather attributes."""
        return weather_probe_result_attributes(self.coordinator.weather_protection)


class DreameLawnMowerConfiguredScheduleCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the cached batch schedule count."""

    _attr_name = "Configured Schedule Count"
    _attr_icon = "mdi:calendar-multiple"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_configured_schedule_count"

    @property
    def native_value(self) -> int | None:
        """Return the number of decoded cached schedules."""
        return _batch_schedule_count(self.coordinator.batch_device_data)

    @property
    def available(self) -> bool:
        """Return whether cached schedule data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached schedule attributes."""
        return batch_schedule_attributes(self.coordinator.batch_device_data)


class DreameLawnMowerPreferenceMapCountSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the cached batch preference map count."""

    _attr_name = "Preference Map Count"
    _attr_icon = "mdi:map-marker-multiple-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_preference_map_count"

    @property
    def native_value(self) -> int | None:
        """Return the number of maps with cached preference data."""
        return _batch_preference_map_count(self.coordinator.batch_device_data)

    @property
    def available(self) -> bool:
        """Return whether cached preference data is available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe cached preference attributes."""
        return batch_preference_attributes(self.coordinator.batch_device_data)


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
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


class DreameLawnMowerLastBatchDeviceDataProbeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last read-only batch device-data probe result."""

    _attr_name = "Last Batch Device Data Probe"
    _attr_icon = "mdi:database-search"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_last_batch_device_data_probe"
        )

    @property
    def native_value(self) -> str:
        """Return a compact state for the last batch device-data probe."""
        return _batch_device_data_probe_state(
            self.coordinator.last_batch_device_data_probe_result
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last batch device-data probe."""
        return batch_device_data_probe_result_attributes(
            self.coordinator.last_batch_device_data_probe_result
        )


def _batch_device_data_probe_state(result: dict[str, Any] | None) -> str:
    if not result:
        return "none"
    sections = [
        result.get("batch_schedule"),
        result.get("batch_mowing_preferences"),
        result.get("batch_ota_info"),
    ]
    if any(
        isinstance(section, dict)
        and isinstance(section.get("errors"), list)
        and section["errors"]
        for section in sections
    ):
        if not any(
            isinstance(section, dict) and section.get("available")
            for section in sections
        ):
            return "error"
    return (
        "available"
        if any(
            isinstance(section, dict) and section.get("available")
            for section in sections
        )
        else "unavailable"
    )


def batch_device_data_probe_result_attributes(
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return compact, non-secret attributes for a batch device-data probe."""
    if not result:
        return {}

    schedule = result.get("batch_schedule")
    preferences = result.get("batch_mowing_preferences")
    ota = result.get("batch_ota_info")
    attributes: dict[str, Any] = {
        "captured_at": result.get("captured_at"),
        "source": result.get("source"),
        "batch_schedule": _batch_schedule_probe_summary(schedule),
        "batch_mowing_preferences": _batch_preference_probe_summary(preferences),
        "batch_ota_info": _batch_ota_probe_summary(ota),
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def batch_schedule_attributes(result: dict[str, Any] | None) -> dict[str, Any]:
    """Return compact cached batch schedule attributes."""
    summary = _batch_schedule_probe_summary(_batch_schedule_section(result))
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "batch_schedule": summary,
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def batch_preference_attributes(result: dict[str, Any] | None) -> dict[str, Any]:
    """Return compact cached batch preference attributes."""
    summary = _batch_preference_probe_summary(_batch_preference_section(result))
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "batch_mowing_preferences": summary,
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def batch_ota_attributes(result: dict[str, Any] | None) -> dict[str, Any]:
    """Return compact cached batch OTA attributes."""
    summary = _batch_ota_probe_summary(_batch_ota_section(result))
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "batch_ota_info": summary,
        "ota_status_name": _batch_ota_status_name(result),
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def _batch_schedule_count(result: dict[str, Any] | None) -> int | None:
    summary = _batch_schedule_probe_summary(_batch_schedule_section(result))
    if not isinstance(summary, dict):
        return None
    value = summary.get("schedule_count")
    return value if isinstance(value, int) else None


def _batch_preference_map_count(result: dict[str, Any] | None) -> int | None:
    summary = _batch_preference_probe_summary(_batch_preference_section(result))
    if not isinstance(summary, dict):
        return None
    value = summary.get("map_count")
    return value if isinstance(value, int) else None


def _batch_schedule_section(result: dict[str, Any] | None) -> dict[str, Any] | None:
    value = result.get("batch_schedule") if isinstance(result, dict) else None
    return value if isinstance(value, dict) else None


def _batch_preference_section(
    result: dict[str, Any] | None,
) -> dict[str, Any] | None:
    value = result.get("batch_mowing_preferences") if isinstance(result, dict) else None
    return value if isinstance(value, dict) else None


def _batch_ota_section(result: dict[str, Any] | None) -> dict[str, Any] | None:
    value = result.get("batch_ota_info") if isinstance(result, dict) else None
    return value if isinstance(value, dict) else None


def _batch_ota_status_name(result: dict[str, Any] | None) -> str | None:
    ota = _batch_ota_section(result)
    if ota is None:
        return None
    state_name = ota.get("ota_state_name")
    if isinstance(state_name, str) and state_name:
        return state_name
    state = ota.get("ota_state")
    if isinstance(state, int):
        return {
            0: "undefined",
            1: "idle",
            2: "upgrading",
            3: "upgrade_success",
            4: "upgrade_failed",
            5: "cannot_upgrade",
        }.get(state, f"state_{state}")
    status = ota.get("ota_status")
    if isinstance(status, str) and status:
        return status
    if isinstance(status, int):
        if ota.get("update_available") and status == 0:
            return "update_available"
        return f"status_{status}"
    if ota.get("update_available") is True:
        return "update_available"
    if ota.get("available"):
        return "no_update"
    return None


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
    return {key: value for key, value in summary.items() if value is not None}


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
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


class DreameLawnMowerLastTaskStatusProbeSensor(
    DreameLawnMowerEntity,
    SensorEntity,
):
    """Expose the last read-only app task/status probe result."""

    _attr_name = "Last Task Status Probe"
    _attr_icon = "mdi:clipboard-pulse-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_last_task_status_probe"

    @property
    def native_value(self) -> str:
        """Return a compact state for the last task/status probe."""
        return task_status_probe_state(
            self.coordinator.last_task_status_probe_result,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe details for the last task/status probe."""
        return task_status_probe_result_attributes(
            self.coordinator.last_task_status_probe_result,
        )


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


def _weather_section(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    return result


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
        key: value for key, value in attributes.items() if value not in (None, [], {})
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
    return {key: value for key, value in summary.items() if value not in (None, [], {})}


def _preference_probe_entry_summary(preference: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "area_id": preference.get("area_id"),
        "reported_version": preference.get("reported_version"),
        "version": preference.get("version"),
        "efficient_mode_name": preference.get("efficient_mode_name"),
        "mowing_height_cm": preference.get("mowing_height_cm"),
        "mowing_direction_mode_name": preference.get("mowing_direction_mode_name"),
        "mowing_direction_method_name": preference.get(
            "mowing_direction_method_name"
        ),
        "mowing_direction_degrees": preference.get("mowing_direction_degrees"),
        "edge_mowing_auto": preference.get("edge_mowing_auto"),
        "edge_mowing_safe": preference.get("edge_mowing_safe"),
        "edge_cutting_attachment": preference.get("edge_cutting_attachment"),
        "edge_mowing_walk_mode_name": preference.get("edge_mowing_walk_mode_name"),
        "turning_method_name": preference.get("turning_method_name"),
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
    return {key: value for key, value in summary.items() if value not in (None, [], {})}


def _batch_schedule_probe_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    schedules = [
        _schedule_probe_entry_summary(schedule)
        for schedule in value.get("schedules", [])
        if isinstance(schedule, dict)
    ]
    summary = {
        "source": value.get("source"),
        "available": value.get("available"),
        "current_task": value.get("current_task"),
        "schedule_count": len(schedules),
        "schedules": schedules,
    }
    errors = value.get("errors")
    if isinstance(errors, list):
        summary["error_count"] = len(errors)
        if errors:
            summary["errors"] = errors
    return {key: item for key, item in summary.items() if item not in (None, [], {})}


def _batch_preference_probe_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    maps = [
        _preference_probe_map_summary(map_entry)
        for map_entry in value.get("maps", [])
        if isinstance(map_entry, dict)
    ]
    summary = {
        "source": value.get("source"),
        "available": value.get("available"),
        "property_hint": value.get("property_hint"),
        "map_count": len(maps),
        "maps": maps,
    }
    errors = value.get("errors")
    if isinstance(errors, list):
        summary["error_count"] = len(errors)
        if errors:
            summary["errors"] = errors
    return {key: item for key, item in summary.items() if item not in (None, [], {})}


def _batch_ota_probe_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    summary = {
        "source": value.get("source"),
        "available": value.get("available"),
        "update_available": value.get("update_available"),
        "auto_upgrade_enabled": value.get("auto_upgrade_enabled"),
        "ota_info": value.get("ota_info"),
        "ota_status": value.get("ota_status"),
        "ota_state": value.get("ota_state"),
        "ota_state_name": value.get("ota_state_name"),
        "ota_progress": value.get("ota_progress"),
    }
    errors = value.get("errors")
    if isinstance(errors, list):
        summary["error_count"] = len(errors)
        if errors:
            summary["errors"] = errors
    return {key: item for key, item in summary.items() if item not in (None, [], {})}
