"""Regression checks for dynamic mower entity availability."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.dreame_lawn_mower.binary_sensor import (
    BINARY_SENSORS,
    DreameLawnMowerBinarySensor,
    DreameLawnMowerBluetoothConnectedBinarySensor,
    DreameLawnMowerCurrentAppMapLivePathBinarySensor,
    DreameLawnMowerRainDelayActiveBinarySensor,
    DreameLawnMowerRainProtectionEnabledBinarySensor,
)
from custom_components.dreame_lawn_mower.sensor import (
    DreameLawnMowerAppMapCountSensor,
    DreameLawnMowerAvailableVectorMapCountSensor,
    DreameLawnMowerCurrentAppMapAreaSensor,
    DreameLawnMowerCurrentAppMapCutRelationCountSensor,
    DreameLawnMowerCurrentAppMapEdgeCountSensor,
    DreameLawnMowerCurrentAppMapIndexSensor,
    DreameLawnMowerCurrentAppMapMowPathLengthSensor,
    DreameLawnMowerCurrentAppMapMowPathPointCountSensor,
    DreameLawnMowerCurrentAppMapSpotCountSensor,
    DreameLawnMowerCurrentAppMapTrajectoryLengthSensor,
    DreameLawnMowerCurrentAppMapTrajectoryPointCountSensor,
    DreameLawnMowerCurrentAppMapZoneCountSensor,
    DreameLawnMowerCurrentVectorMapIdSensor,
    DreameLawnMowerCurrentVectorMapNameSensor,
    DreameLawnMowerLastPreferenceProbeSensor,
    DreameLawnMowerLastScheduleProbeSensor,
    DreameLawnMowerLastScheduleWriteSensor,
    DreameLawnMowerLastTaskStatusProbeSensor,
    DreameLawnMowerLastWeatherProbeSensor,
    DreameLawnMowerMowingProgressSensor,
    DreameLawnMowerRainDelayEndTimeSensor,
    DreameLawnMowerRainProtectionDurationSensor,
    DreameLawnMowerRuntimeCurrentAreaSensor,
    DreameLawnMowerRuntimeHeadingSensor,
    DreameLawnMowerRuntimeMissionProgressSensor,
    DreameLawnMowerRuntimePositionXSensor,
    DreameLawnMowerRuntimePositionYSensor,
    DreameLawnMowerRuntimeTotalAreaSensor,
    DreameLawnMowerRuntimeTrackLengthSensor,
    DreameLawnMowerRuntimeTrackPointCountSensor,
    DreameLawnMowerRuntimeTrackSegmentCountSensor,
    DreameLawnMowerSelectedMapSensor,
    DreameLawnMowerSelectedMowingActionSensor,
    DreameLawnMowerSelectedTargetSensor,
    DreameLawnMowerSensor,
    DreameLawnMowerWeatherProtectionStatusSensor,
    SENSORS,
)


def _sensor_description(key: str):
    return next(description for description in SENSORS if description.key == key)


def _binary_sensor_description(key: str):
    return next(description for description in BINARY_SENSORS if description.key == key)


def test_cleaning_mode_sensor_can_become_available_after_startup() -> None:
    entity = object.__new__(DreameLawnMowerSensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            cleaning_mode_name="unknown",
            raw_attributes={},
        )
    )
    entity.entity_description = _sensor_description("cleaning_mode")

    assert entity.available is False
    assert entity.native_value is None

    entity.coordinator.data = SimpleNamespace(
        cleaning_mode_name="standard",
        raw_attributes={},
    )

    assert entity.available is True
    assert entity.native_value == "standard"


def test_activity_sensor_exposes_normalized_client_activity() -> None:
    entity = object.__new__(DreameLawnMowerSensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            activity="returning",
            raw_attributes={},
        )
    )
    entity.entity_description = _sensor_description("activity")

    assert entity.available is True
    assert entity.native_value == "returning"


def test_current_cleaned_area_sensor_can_become_available_after_startup() -> None:
    entity = object.__new__(DreameLawnMowerSensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            cleaned_area=None,
            raw_attributes={},
        )
    )
    entity.entity_description = _sensor_description("current_cleaned_area")

    assert entity.available is False
    assert entity.native_value is None

    entity.coordinator.data = SimpleNamespace(
        cleaned_area=42,
        raw_attributes={},
    )

    assert entity.available is True
    assert entity.native_value == 42


def test_current_cleaning_time_sensor_can_become_available_after_startup() -> None:
    entity = object.__new__(DreameLawnMowerSensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            cleaning_time=None,
            raw_attributes={},
        )
    )
    entity.entity_description = _sensor_description("current_cleaning_time")

    assert entity.available is False
    assert entity.native_value is None

    entity.coordinator.data = SimpleNamespace(
        cleaning_time=58,
        raw_attributes={},
    )

    assert entity.available is True
    assert entity.native_value == 58


def test_current_zone_sensor_prefers_zone_name() -> None:
    entity = object.__new__(DreameLawnMowerSensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            current_zone_id=7,
            current_zone_name="Front Lawn",
            raw_attributes={},
        )
    )
    entity.entity_description = _sensor_description("current_zone")

    assert entity.available is True
    assert entity.native_value == "Front Lawn"


def test_active_segment_count_sensor_uses_snapshot_count() -> None:
    entity = object.__new__(DreameLawnMowerSensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            active_segment_count=2,
            raw_attributes={},
        )
    )
    entity.entity_description = _sensor_description("active_segment_count")

    assert entity.available is True
    assert entity.native_value == 2


def test_child_lock_binary_sensor_can_become_available_after_startup() -> None:
    entity = object.__new__(DreameLawnMowerBinarySensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            child_lock=None,
            raw_attributes={},
        )
    )
    entity.entity_description = _binary_sensor_description("child_lock")

    assert entity.available is False
    assert entity.is_on is None

    entity.coordinator.data = SimpleNamespace(
        child_lock=True,
        raw_attributes={},
    )

    assert entity.available is True
    assert entity.is_on is True


def test_device_connected_binary_sensor_can_become_available_after_startup() -> None:
    entity = object.__new__(DreameLawnMowerBinarySensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            device_connected=None,
            raw_attributes={},
        )
    )
    entity.entity_description = _binary_sensor_description("device_connected")

    assert entity.available is False
    assert entity.is_on is None

    entity.coordinator.data = SimpleNamespace(
        device_connected=False,
        raw_attributes={},
    )

    assert entity.available is True
    assert entity.is_on is False


def test_cloud_connected_binary_sensor_preserves_false_value() -> None:
    entity = object.__new__(DreameLawnMowerBinarySensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            cloud_connected=False,
            raw_attributes={},
        )
    )
    entity.entity_description = _binary_sensor_description("cloud_connected")

    assert entity.available is True
    assert entity.is_on is False


def test_raw_docked_binary_sensor_preserves_vendor_flag() -> None:
    entity = object.__new__(DreameLawnMowerBinarySensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            raw_docked=False,
            raw_attributes={},
        )
    )
    entity.entity_description = _binary_sensor_description("raw_docked")

    assert entity.available is True
    assert entity.is_on is False


def test_raw_charging_binary_sensor_preserves_vendor_flag() -> None:
    entity = object.__new__(DreameLawnMowerBinarySensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            raw_charging=False,
            raw_attributes={},
        )
    )
    entity.entity_description = _binary_sensor_description("raw_charging")

    assert entity.available is True
    assert entity.is_on is False


def test_manual_drive_safe_binary_sensor_uses_shared_guard() -> None:
    entity = object.__new__(DreameLawnMowerBinarySensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            activity="returning",
            battery_level=80,
            mowing=False,
            raw_attributes={},
            returning=True,
            state="returning",
        )
    )
    entity.entity_description = _binary_sensor_description("manual_drive_safe")

    assert entity.available is True
    assert entity.is_on is False


def test_manual_drive_block_reason_sensor_reports_none_when_safe() -> None:
    entity = object.__new__(DreameLawnMowerSensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            activity="docked",
            battery_level=80,
            mowing=False,
            raw_attributes={},
            returning=False,
            state="charging",
        )
    )
    entity.entity_description = _sensor_description("manual_drive_block_reason")

    assert entity.available is True
    assert entity.native_value == "none"


def test_manual_drive_block_reason_sensor_reports_current_reason() -> None:
    entity = object.__new__(DreameLawnMowerSensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            activity="docked",
            battery_level=10,
            mowing=False,
            raw_attributes={},
            returning=False,
            state="charging",
        )
    )
    entity.entity_description = _sensor_description("manual_drive_block_reason")

    assert entity.available is True
    assert entity.native_value == "Remote control is blocked while battery is low."


def test_task_active_binary_sensor_uses_effective_started_flag() -> None:
    entity = object.__new__(DreameLawnMowerBinarySensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            started=False,
            raw_attributes={},
        )
    )
    entity.entity_description = _binary_sensor_description("task_active")

    assert entity.available is True
    assert entity.is_on is False


def test_raw_started_binary_sensor_preserves_vendor_flag() -> None:
    entity = object.__new__(DreameLawnMowerBinarySensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            raw_started=True,
            raw_attributes={},
        )
    )
    entity.entity_description = _binary_sensor_description("raw_started")

    assert entity.available is True
    assert entity.is_on is True


def test_raw_returning_binary_sensor_preserves_vendor_flag() -> None:
    entity = object.__new__(DreameLawnMowerBinarySensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            raw_returning=True,
            raw_attributes={},
        )
    )
    entity.entity_description = _binary_sensor_description("raw_returning")

    assert entity.available is True
    assert entity.is_on is True


def test_current_app_map_live_path_binary_sensor_uses_vector_map_summary() -> None:
    entity = object.__new__(DreameLawnMowerCurrentAppMapLivePathBinarySensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        batch_device_data=None,
        app_maps={
            "source": "app_maps_auto",
            "available": True,
            "current_map_index": 1,
            "maps": [
                {"idx": 0, "current": False, "available": True, "created": True},
                {"idx": 1, "current": True, "available": True, "created": True},
            ],
        },
        vector_map_details={
            "source": "batch_vector_map",
            "available": True,
            "maps": [
                {
                    "map_id": 1,
                    "map_index": 0,
                    "map_name": "Front",
                    "contour_count": 2,
                    "has_live_path": False,
                },
                {
                    "map_id": 2,
                    "map_index": 1,
                    "map_name": "Back",
                    "contour_ids": [[5, 0]],
                    "contour_count": 1,
                    "mow_path_count": 1,
                    "mow_path_segment_count": 2,
                    "mow_path_point_count": 4,
                    "mow_path_length_m": 3.75,
                    "has_live_path": True,
                },
            ],
        },
    )

    assert entity.available is True
    assert entity.is_on is True
    assert entity.extra_state_attributes == {
        "source": "batch_vector_map",
        "current_vector_map": {
            "map_id": 2,
            "map_index": 1,
            "map_name": "Back",
            "contour_ids": [[5, 0]],
            "contour_count": 1,
            "mow_path_count": 1,
            "mow_path_segment_count": 2,
            "mow_path_point_count": 4,
            "mow_path_length_m": 3.75,
            "has_live_path": True,
        },
    }


def test_bluetooth_connected_binary_sensor_uses_cached_cloud_state() -> None:
    entity = object.__new__(DreameLawnMowerBluetoothConnectedBinarySensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        bluetooth_connected=False,
    )

    assert entity.available is True
    assert entity.is_on is False
    assert entity.extra_state_attributes == {
        "property_key": "1.53",
        "source": "cloud_property_scan",
    }


def test_bluetooth_connected_binary_sensor_is_unavailable_without_cached_value() -> None:
    entity = object.__new__(DreameLawnMowerBluetoothConnectedBinarySensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        bluetooth_connected=None,
    )

    assert entity.available is False
    assert entity.is_on is None


def test_last_schedule_write_sensor_reports_none_before_service_call() -> None:
    entity = object.__new__(DreameLawnMowerLastScheduleWriteSensor)
    entity.coordinator = SimpleNamespace(last_schedule_write_result=None)

    assert entity.native_value == "none"
    assert entity.extra_state_attributes == {}


def test_last_schedule_write_sensor_reports_dry_run_result() -> None:
    entity = object.__new__(DreameLawnMowerLastScheduleWriteSensor)
    entity.coordinator = SimpleNamespace(
        last_schedule_write_result={
            "dry_run": True,
            "executed": False,
            "changed": True,
            "map_index": 0,
            "plan_id": 0,
            "previous_enabled": True,
            "enabled": False,
            "version": 19383,
            "request": {"t": "SCHDSV2"},
        }
    )

    assert entity.native_value == "dry_run"
    assert entity.extra_state_attributes == {
        "dry_run": True,
        "executed": False,
        "changed": True,
        "map_index": 0,
        "plan_id": 0,
        "previous_enabled": True,
        "enabled": False,
        "version": 19383,
        "request": {"t": "SCHDSV2"},
    }


def test_last_schedule_probe_sensor_reports_none_before_probe() -> None:
    entity = object.__new__(DreameLawnMowerLastScheduleProbeSensor)
    entity.coordinator = SimpleNamespace(last_schedule_probe_result=None)

    assert entity.native_value == "none"
    assert entity.extra_state_attributes == {}


def test_last_schedule_probe_sensor_reports_available_probe() -> None:
    entity = object.__new__(DreameLawnMowerLastScheduleProbeSensor)
    entity.coordinator = SimpleNamespace(
        last_schedule_probe_result={
            "source": "app_action_schedule",
            "available": True,
            "schedules": [{"idx": 0, "version": 19383}],
            "errors": [],
        }
    )

    assert entity.native_value == "available"
    assert entity.extra_state_attributes == {
        "source": "app_action_schedule",
        "available": True,
        "schedule_count": 1,
        "schedules": [{"idx": 0, "version": 19383}],
        "error_count": 0,
    }


def test_last_schedule_probe_sensor_reports_error_probe() -> None:
    entity = object.__new__(DreameLawnMowerLastScheduleProbeSensor)
    entity.coordinator = SimpleNamespace(
        last_schedule_probe_result={
            "source": "app_action_schedule",
            "available": False,
            "schedules": [],
            "errors": [{"stage": "schedule", "error": "cloud unavailable"}],
        }
    )

    assert entity.native_value == "error"
    assert entity.extra_state_attributes == {
        "source": "app_action_schedule",
        "available": False,
        "schedule_count": 0,
        "error_count": 1,
        "errors": [{"stage": "schedule", "error": "cloud unavailable"}],
    }


def test_last_preference_probe_sensor_reports_none_before_probe() -> None:
    entity = object.__new__(DreameLawnMowerLastPreferenceProbeSensor)
    entity.coordinator = SimpleNamespace(last_preference_probe_result=None)

    assert entity.native_value == "none"
    assert entity.extra_state_attributes == {}


def test_last_preference_probe_sensor_reports_available_probe() -> None:
    entity = object.__new__(DreameLawnMowerLastPreferenceProbeSensor)
    entity.coordinator = SimpleNamespace(
        last_preference_probe_result={
            "source": "app_action_mowing_preferences",
            "available": True,
            "property_hint": "2.52",
            "maps": [{"idx": 0, "mode_name": "custom", "preferences": []}],
            "errors": [],
        }
    )

    assert entity.native_value == "available"
    assert entity.extra_state_attributes == {
        "source": "app_action_mowing_preferences",
        "available": True,
        "property_hint": "2.52",
        "map_count": 1,
        "maps": [
            {
                "idx": 0,
                "mode_name": "custom",
                "preference_count": 0,
            }
        ],
        "error_count": 0,
    }


def test_last_preference_probe_sensor_reports_error_probe() -> None:
    entity = object.__new__(DreameLawnMowerLastPreferenceProbeSensor)
    entity.coordinator = SimpleNamespace(
        last_preference_probe_result={
            "source": "app_action_mowing_preferences",
            "available": False,
            "maps": [],
            "errors": [{"stage": "preferences", "error": "cloud unavailable"}],
        }
    )

    assert entity.native_value == "error"
    assert entity.extra_state_attributes == {
        "source": "app_action_mowing_preferences",
        "available": False,
        "map_count": 0,
        "error_count": 1,
        "errors": [{"stage": "preferences", "error": "cloud unavailable"}],
    }


def test_last_task_status_probe_sensor_reports_none_before_probe() -> None:
    entity = object.__new__(DreameLawnMowerLastTaskStatusProbeSensor)
    entity.coordinator = SimpleNamespace(last_task_status_probe_result=None)

    assert entity.native_value == "none"
    assert entity.extra_state_attributes == {}


def test_last_task_status_probe_sensor_reports_app_state() -> None:
    entity = object.__new__(DreameLawnMowerLastTaskStatusProbeSensor)
    entity.coordinator = SimpleNamespace(
        last_task_status_probe_result={
            "captured_at": "2026-04-19T15:00:00+00:00",
            "source": "cloud_property_task_status",
            "available": True,
            "keys": ["1.4", "1.53", "2.1", "2.2", "2.50", "2.56", "2.60", "3.1", "3.2", "5.106"],
            "entry_count": 10,
            "summary": {
                "state": {
                    "value": "6",
                    "label": "Charging",
                    "state_key": "charging",
                },
                "runtime_status": {
                    "length": 33,
                    "frame_valid": True,
                    "candidate_runtime_area_progress_percent": 13.7,
                    "candidate_runtime_current_area_sqm": 72.95,
                    "candidate_runtime_total_area_sqm": 531.0,
                    "candidate_runtime_region_id": 1,
                    "candidate_runtime_task_id": 100,
                    "candidate_runtime_pose_x": 5910,
                    "candidate_runtime_pose_y": 12400,
                    "candidate_runtime_heading_deg": 63.5,
                    "notes": ["unexpected_length", "unexpected_runtime_progress_value"],
                },
                "bluetooth_connected": "false",
                "task_status": {
                    "type": "TASK",
                    "executing": True,
                    "status": True,
                    "operation": 6,
                },
                "error": {"value": "54", "label": "Edge", "active": True},
                "error_active": True,
                "battery_level": "77",
                "status_matrix": {
                    "keys": ["status"],
                    "status_pairs": [[1, 4]],
                    "status_count": 1,
                },
                "auxiliary_live_properties": {"2.60": "1", "3.2": "1"},
                "service_5_latest": {"5.106": "1"},
                "unknown_non_empty_keys": ["2.56", "2.60", "3.2", "5.106"],
            },
            "errors": [],
        }
    )

    assert entity.native_value == "charging"
    assert entity.extra_state_attributes == {
        "captured_at": "2026-04-19T15:00:00+00:00",
        "source": "cloud_property_task_status",
        "available": True,
        "keys": ["1.4", "1.53", "2.1", "2.2", "2.50", "2.56", "2.60", "3.1", "3.2", "5.106"],
        "entry_count": 10,
        "state": {"value": "6", "label": "Charging", "state_key": "charging"},
        "runtime_status": {
            "length": 33,
            "frame_valid": True,
            "candidate_runtime_area_progress_percent": 13.7,
            "candidate_runtime_current_area_sqm": 72.95,
            "candidate_runtime_total_area_sqm": 531.0,
            "candidate_runtime_region_id": 1,
            "candidate_runtime_task_id": 100,
            "candidate_runtime_pose_x": 5910,
            "candidate_runtime_pose_y": 12400,
            "candidate_runtime_heading_deg": 63.5,
            "notes": ["unexpected_length", "unexpected_runtime_progress_value"],
        },
        "bluetooth_connected": "false",
        "task_status": {
            "type": "TASK",
            "executing": True,
            "status": True,
            "operation": 6,
        },
        "error": {"value": "54", "label": "Edge", "active": True},
        "error_active": True,
        "battery_level": "77",
        "status_matrix": {
            "keys": ["status"],
            "status_pairs": [[1, 4]],
            "status_count": 1,
        },
        "auxiliary_live_properties": {"2.60": "1", "3.2": "1"},
        "service_5_latest": {"5.106": "1"},
        "unknown_non_empty_keys": ["2.56", "2.60", "3.2", "5.106"],
        "error_count": 0,
    }


def test_last_task_status_probe_sensor_reports_config_error() -> None:
    entity = object.__new__(DreameLawnMowerLastTaskStatusProbeSensor)
    entity.coordinator = SimpleNamespace(
        last_task_status_probe_result={
            "source": "cloud_property_task_status",
            "available": False,
            "errors": [{"stage": "properties", "error": "cloud unavailable"}],
        }
    )

    assert entity.native_value == "error"
    assert entity.extra_state_attributes == {
        "source": "cloud_property_task_status",
        "available": False,
        "error_count": 1,
        "errors": [{"stage": "properties", "error": "cloud unavailable"}],
    }


def test_last_weather_probe_sensor_reports_none_before_probe() -> None:
    entity = object.__new__(DreameLawnMowerLastWeatherProbeSensor)
    entity.coordinator = SimpleNamespace(last_weather_probe_result=None)

    assert entity.native_value == "none"
    assert entity.extra_state_attributes == {}


def test_last_weather_probe_sensor_reports_available_probe() -> None:
    entity = object.__new__(DreameLawnMowerLastWeatherProbeSensor)
    entity.coordinator = SimpleNamespace(
        last_weather_probe_result={
            "source": "app_action_weather_protection",
            "available": True,
            "present_config_keys": ["WRP"],
            "weather_switch_enabled": True,
            "rain_protection_enabled": True,
            "rain_protection_duration_hours": 8,
            "rain_sensor_sensitivity": 0,
            "errors": [],
            "warnings": [],
        }
    )

    assert entity.native_value == "rain_protection_enabled"
    assert entity.extra_state_attributes == {
        "source": "app_action_weather_protection",
        "available": True,
        "present_config_keys": ["WRP"],
        "weather_switch_enabled": True,
        "rain_protection_enabled": True,
        "rain_protection_duration_hours": 8,
        "rain_sensor_sensitivity": 0,
        "error_count": 0,
        "warning_count": 0,
    }


def test_last_weather_probe_sensor_reports_active_rain_delay() -> None:
    entity = object.__new__(DreameLawnMowerLastWeatherProbeSensor)
    entity.coordinator = SimpleNamespace(
        last_weather_probe_result={
            "source": "app_action_weather_protection",
            "available": True,
            "rain_protection_enabled": True,
            "rain_protection_active": True,
            "rain_protect_end_time": 1776600300,
            "rain_protect_end_time_iso": "2026-04-19T12:05:00+00:00",
            "errors": [],
            "warnings": [],
        }
    )

    assert entity.native_value == "rain_delay_active"
    assert entity.extra_state_attributes == {
        "source": "app_action_weather_protection",
        "available": True,
        "rain_protection_enabled": True,
        "rain_protect_end_time": 1776600300,
        "rain_protect_end_time_iso": "2026-04-19T12:05:00+00:00",
        "rain_protection_active": True,
        "error_count": 0,
        "warning_count": 0,
    }


def test_last_weather_probe_sensor_reports_config_error() -> None:
    entity = object.__new__(DreameLawnMowerLastWeatherProbeSensor)
    entity.coordinator = SimpleNamespace(
        last_weather_probe_result={
            "source": "app_action_weather_protection",
            "available": False,
            "errors": [{"stage": "config", "error": "cloud unavailable"}],
            "warnings": [],
        }
    )

    assert entity.native_value == "error"
    assert entity.extra_state_attributes == {
        "source": "app_action_weather_protection",
        "available": False,
        "error_count": 1,
        "errors": [{"stage": "config", "error": "cloud unavailable"}],
        "warning_count": 0,
    }


def test_weather_protection_status_sensor_uses_cached_weather_state() -> None:
    entity = object.__new__(DreameLawnMowerWeatherProtectionStatusSensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        weather_protection={
            "source": "weather_protection_auto",
            "available": True,
            "rain_protection_enabled": True,
            "rain_protection_active": False,
            "rain_protection_duration_hours": 8,
            "warnings": [],
            "errors": [],
        },
    )

    assert entity.available is True
    assert entity.native_value == "rain_protection_enabled"
    assert entity.extra_state_attributes == {
        "source": "weather_protection_auto",
        "available": True,
        "rain_protection_enabled": True,
        "rain_protection_active": False,
        "rain_protection_duration_hours": 8,
        "error_count": 0,
        "warning_count": 0,
    }


def test_rain_protection_enabled_binary_sensor_uses_cached_weather_state() -> None:
    entity = object.__new__(DreameLawnMowerRainProtectionEnabledBinarySensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        weather_protection={
            "source": "weather_protection_auto",
            "available": True,
            "rain_protection_enabled": True,
            "rain_protection_active": False,
            "warnings": [],
            "errors": [],
        },
    )

    assert entity.available is True
    assert entity.is_on is True
    assert entity.extra_state_attributes == {
        "source": "weather_protection_auto",
        "available": True,
        "rain_protection_enabled": True,
        "rain_protection_active": False,
        "error_count": 0,
        "warning_count": 0,
    }


def test_rain_delay_active_binary_sensor_uses_cached_weather_state() -> None:
    entity = object.__new__(DreameLawnMowerRainDelayActiveBinarySensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        weather_protection={
            "source": "weather_protection_auto",
            "available": True,
            "rain_protection_enabled": True,
            "rain_protection_active": True,
            "rain_protect_end_time_iso": "2026-04-19T12:05:00+00:00",
            "warnings": [],
            "errors": [],
        },
    )

    assert entity.available is True
    assert entity.is_on is True
    assert entity.extra_state_attributes == {
        "source": "weather_protection_auto",
        "available": True,
        "rain_protection_enabled": True,
        "rain_protect_end_time_iso": "2026-04-19T12:05:00+00:00",
        "rain_protection_active": True,
        "error_count": 0,
        "warning_count": 0,
    }


def test_rain_protection_duration_sensor_uses_cached_weather_state() -> None:
    entity = object.__new__(DreameLawnMowerRainProtectionDurationSensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        weather_protection={
            "source": "weather_protection_auto",
            "available": True,
            "rain_protection_duration_hours": 8,
            "warnings": [],
            "errors": [],
        },
    )

    assert entity.available is True
    assert entity.native_value == 8


def test_rain_delay_end_time_sensor_uses_cached_weather_state() -> None:
    entity = object.__new__(DreameLawnMowerRainDelayEndTimeSensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        weather_protection={
            "source": "weather_protection_auto",
            "available": True,
            "rain_protect_end_time_iso": "2026-04-19T12:05:00+00:00",
            "rain_protection_active": True,
            "warnings": [],
            "errors": [],
        },
    )

    assert entity.available is True
    assert entity.native_value.isoformat() == "2026-04-19T12:05:00+00:00"


def test_app_map_count_sensor_uses_cached_app_map_state() -> None:
    entity = object.__new__(DreameLawnMowerAppMapCountSensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        batch_device_data=None,
        app_maps={
            "source": "app_maps_auto",
            "available": True,
            "current_map_index": 0,
            "maps": [
                {
                    "idx": 0,
                    "current": True,
                    "available": True,
                    "created": True,
                    "summary": {
                        "total_area": 531,
                        "map_area_total": 530.33,
                        "map_area_count": 2,
                        "spot_count": 2,
                        "trajectory_count": 1,
                        "trajectory_point_count": 63,
                        "trajectory_length_m": 48.25,
                        "cut_relation_count": 0,
                    },
                },
                {
                    "idx": 1,
                    "current": False,
                    "available": True,
                    "created": True,
                    "summary": {
                        "total_area": 550,
                        "map_area_total": 550.0,
                        "map_area_count": 2,
                        "spot_count": 0,
                        "trajectory_count": 1,
                        "trajectory_point_count": 64,
                        "trajectory_length_m": 52.5,
                        "cut_relation_count": 0,
                    },
                },
            ],
        },
    )

    assert entity.available is True
    assert entity.native_value == 2
    assert entity.extra_state_attributes == {
        "source": "app_maps_auto",
        "app_maps": {
            "source": "app_maps_auto",
            "available": True,
            "map_count": 2,
            "current_map_index": 0,
            "maps": [
                {
                    "idx": 0,
                    "current": True,
                    "available": True,
                    "created": True,
                    "total_area": 531,
                    "map_area_total": 530.33,
                    "map_area_count": 2,
                    "spot_count": 2,
                    "trajectory_count": 1,
                    "trajectory_point_count": 63,
                    "trajectory_length_m": 48.25,
                    "cut_relation_count": 0,
                    "has_live_path": True,
                },
                {
                    "idx": 1,
                    "current": False,
                    "available": True,
                    "created": True,
                    "total_area": 550,
                    "map_area_total": 550.0,
                    "map_area_count": 2,
                    "spot_count": 0,
                    "trajectory_count": 1,
                    "trajectory_point_count": 64,
                    "trajectory_length_m": 52.5,
                    "cut_relation_count": 0,
                    "has_live_path": True,
                },
            ],
        },
    }


def test_current_app_map_sensors_use_cached_current_map_state() -> None:
    coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        batch_device_data=None,
        app_maps={
            "source": "app_maps_auto",
            "available": True,
            "current_map_index": 1,
            "maps": [
                {
                    "idx": 0,
                    "current": False,
                    "available": True,
                    "created": True,
                    "summary": {
                        "total_area": 531,
                        "map_area_total": 530.33,
                        "map_area_count": 2,
                        "spot_count": 2,
                        "trajectory_count": 1,
                        "trajectory_point_count": 63,
                        "trajectory_length_m": 48.25,
                        "cut_relation_count": 0,
                    },
                },
                {
                    "idx": 1,
                    "current": True,
                    "available": True,
                    "created": True,
                    "summary": {
                        "total_area": 550,
                        "map_area_total": 550.0,
                        "map_area_count": 2,
                        "spot_count": 0,
                        "trajectory_count": 1,
                        "trajectory_point_count": 64,
                        "trajectory_length_m": 52.5,
                        "cut_relation_count": 0,
                    },
                },
            ],
        },
    )

    index_entity = object.__new__(DreameLawnMowerCurrentAppMapIndexSensor)
    index_entity.coordinator = coordinator
    area_entity = object.__new__(DreameLawnMowerCurrentAppMapAreaSensor)
    area_entity.coordinator = coordinator
    zone_entity = object.__new__(DreameLawnMowerCurrentAppMapZoneCountSensor)
    zone_entity.coordinator = coordinator
    spot_entity = object.__new__(DreameLawnMowerCurrentAppMapSpotCountSensor)
    spot_entity.coordinator = coordinator
    trajectory_entity = object.__new__(DreameLawnMowerCurrentAppMapTrajectoryPointCountSensor)
    trajectory_entity.coordinator = coordinator
    trajectory_length_entity = object.__new__(
        DreameLawnMowerCurrentAppMapTrajectoryLengthSensor
    )
    trajectory_length_entity.coordinator = coordinator
    cut_relation_entity = object.__new__(DreameLawnMowerCurrentAppMapCutRelationCountSensor)
    cut_relation_entity.coordinator = coordinator

    assert index_entity.available is True
    assert index_entity.native_value == 1
    assert area_entity.native_value == 550
    assert zone_entity.native_value == 2
    assert spot_entity.native_value == 0
    assert trajectory_entity.native_value == 64
    assert trajectory_length_entity.native_value == 52.5
    assert cut_relation_entity.native_value == 0
    assert index_entity.extra_state_attributes == {
        "source": "app_maps_auto",
        "current_app_map": {
            "idx": 1,
            "current": True,
            "available": True,
            "created": True,
            "total_area": 550,
            "map_area_total": 550.0,
            "map_area_count": 2,
            "spot_count": 0,
            "trajectory_count": 1,
            "trajectory_point_count": 64,
            "trajectory_length_m": 52.5,
            "cut_relation_count": 0,
            "has_live_path": True,
        },
    }


def test_current_vector_map_sensors_follow_active_map() -> None:
    coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        batch_device_data=None,
        app_maps={
            "source": "app_maps_auto",
            "available": True,
            "current_map_index": 1,
            "maps": [
                {"idx": 0, "current": False, "available": True, "created": True},
                {"idx": 1, "current": True, "available": True, "created": True},
            ],
        },
        vector_map_details={
            "source": "batch_vector_map",
            "available": True,
            "maps": [
                {
                    "map_id": 1,
                    "map_index": 0,
                    "map_name": "Front",
                    "total_area": 531,
                    "zone_ids": [1],
                    "zone_names": ["Front Yard"],
                    "spot_ids": [9],
                    "contour_ids": [[1, 0], [3, 0]],
                    "contour_count": 2,
                    "clean_point_count": 1,
                    "cruise_point_count": 0,
                    "mow_path_count": 0,
                    "mow_path_segment_count": 0,
                    "mow_path_point_count": 0,
                    "mow_path_length_m": 0.0,
                    "has_live_path": False,
                },
                {
                    "map_id": 2,
                    "map_index": 1,
                    "map_name": "Back",
                    "total_area": 550,
                    "zone_ids": [2],
                    "zone_names": ["Back Yard"],
                    "contour_ids": [[5, 0]],
                    "contour_count": 1,
                    "clean_point_count": 0,
                    "cruise_point_count": 0,
                    "mow_path_count": 1,
                    "mow_path_segment_count": 2,
                    "mow_path_point_count": 4,
                    "mow_path_length_m": 3.75,
                    "runtime_track_segment_count": 3,
                    "runtime_track_point_count": 17,
                    "runtime_track_length_m": 12.4,
                    "runtime_pose_x": 480,
                    "runtime_pose_y": 260,
                    "runtime_heading_deg": 91.5,
                    "has_live_path": True,
                },
            ],
        },
    )

    available_vector_entity = object.__new__(DreameLawnMowerAvailableVectorMapCountSensor)
    available_vector_entity.coordinator = coordinator
    current_vector_name_entity = object.__new__(DreameLawnMowerCurrentVectorMapNameSensor)
    current_vector_name_entity.coordinator = coordinator
    current_vector_id_entity = object.__new__(DreameLawnMowerCurrentVectorMapIdSensor)
    current_vector_id_entity.coordinator = coordinator
    edge_entity = object.__new__(DreameLawnMowerCurrentAppMapEdgeCountSensor)
    edge_entity.coordinator = coordinator
    mow_path_entity = object.__new__(
        DreameLawnMowerCurrentAppMapMowPathPointCountSensor
    )
    mow_path_entity.coordinator = coordinator
    mow_path_length_entity = object.__new__(
        DreameLawnMowerCurrentAppMapMowPathLengthSensor
    )
    mow_path_length_entity.coordinator = coordinator

    assert available_vector_entity.available is True
    assert available_vector_entity.native_value == 2
    assert available_vector_entity.extra_state_attributes == {
        "source": "batch_vector_map",
        "vector_maps": {
            "available": True,
            "available_map_count": 2,
            "map_names": ["Front", "Back"],
            "maps": [
                {
                    "map_id": 1,
                    "map_index": 0,
                    "map_name": "Front",
                    "total_area": 531,
                    "zone_ids": [1],
                    "zone_names": ["Front Yard"],
                    "spot_ids": [9],
                    "contour_ids": [[1, 0], [3, 0]],
                    "contour_count": 2,
                    "clean_point_count": 1,
                    "cruise_point_count": 0,
                    "mow_path_count": 0,
                    "mow_path_segment_count": 0,
                    "mow_path_point_count": 0,
                    "mow_path_length_m": 0.0,
                    "has_live_path": False,
                },
                {
                    "map_id": 2,
                    "map_index": 1,
                    "map_name": "Back",
                    "total_area": 550,
                    "zone_ids": [2],
                    "zone_names": ["Back Yard"],
                    "contour_ids": [[5, 0]],
                    "contour_count": 1,
                    "clean_point_count": 0,
                    "cruise_point_count": 0,
                    "mow_path_count": 1,
                    "mow_path_segment_count": 2,
                    "mow_path_point_count": 4,
                    "mow_path_length_m": 3.75,
                    "runtime_track_segment_count": 3,
                    "runtime_track_point_count": 17,
                    "runtime_track_length_m": 12.4,
                    "runtime_pose_x": 480,
                    "runtime_pose_y": 260,
                    "runtime_heading_deg": 91.5,
                    "has_live_path": True,
                },
            ],
        },
    }
    assert current_vector_name_entity.available is True
    assert current_vector_name_entity.native_value == "Back"
    assert current_vector_id_entity.available is True
    assert current_vector_id_entity.native_value == 2
    assert edge_entity.available is True
    assert edge_entity.native_value == 1
    assert mow_path_entity.available is True
    assert mow_path_entity.native_value == 4
    assert mow_path_length_entity.available is True
    assert mow_path_length_entity.native_value == 3.75
    assert edge_entity.extra_state_attributes == {
        "source": "batch_vector_map",
        "current_vector_map": {
            "map_id": 2,
            "map_index": 1,
            "map_name": "Back",
            "total_area": 550,
            "zone_ids": [2],
            "zone_names": ["Back Yard"],
            "contour_ids": [[5, 0]],
            "contour_count": 1,
            "clean_point_count": 0,
            "cruise_point_count": 0,
            "mow_path_count": 1,
            "mow_path_segment_count": 2,
            "mow_path_point_count": 4,
            "mow_path_length_m": 3.75,
            "runtime_track_segment_count": 3,
            "runtime_track_point_count": 17,
            "runtime_track_length_m": 12.4,
            "runtime_pose_x": 480,
            "runtime_pose_y": 260,
            "runtime_heading_deg": 91.5,
            "has_live_path": True,
        },
    }


def test_mowing_progress_sensor_uses_live_cleaned_area_and_current_map_area() -> None:
    entity = object.__new__(DreameLawnMowerMowingProgressSensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(
            cleaned_area=220,
            cleaning_time=58,
            current_zone_id=7,
            current_zone_name="Front Lawn",
            active_segment_count=2,
        ),
        batch_device_data=None,
        app_maps={
            "source": "app_maps_auto",
            "available": True,
            "current_map_index": 1,
            "maps": [
                {
                    "idx": 0,
                    "current": False,
                    "available": True,
                    "created": True,
                    "summary": {
                        "total_area": 531,
                        "map_area_total": 530.33,
                        "map_area_count": 2,
                        "trajectory_count": 0,
                        "trajectory_point_count": 0,
                        "cut_relation_count": 0,
                    },
                },
                {
                    "idx": 1,
                    "current": True,
                    "available": True,
                    "created": True,
                    "summary": {
                        "total_area": 550,
                        "map_area_total": 550.0,
                        "map_area_count": 2,
                        "trajectory_count": 0,
                        "trajectory_point_count": 0,
                        "cut_relation_count": 0,
                    },
                },
            ],
        },
    )

    assert entity.available is True
    assert entity.native_value == 40.0
    assert entity.extra_state_attributes == {
        "cleaned_area": 220,
        "cleaning_time": 58,
        "current_zone": "Front Lawn",
        "active_segment_count": 2,
        "current_app_map": {
            "idx": 1,
            "current": True,
            "available": True,
            "created": True,
            "total_area": 550,
            "map_area_total": 550.0,
            "map_area_count": 2,
            "trajectory_count": 0,
            "trajectory_point_count": 0,
            "cut_relation_count": 0,
            "has_live_path": False,
        },
    }


def test_runtime_mission_progress_sensor_uses_runtime_blob_area_ratio() -> None:
    entity = object.__new__(DreameLawnMowerRuntimeMissionProgressSensor)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(activity="mowing"),
        runtime_status_blob=SimpleNamespace(
            source="cloud",
            length=33,
            frame_valid=True,
            candidate_runtime_progress_percent=None,
            candidate_runtime_area_progress_percent=13.7,
            candidate_runtime_current_area_sqm=72.95,
            candidate_runtime_total_area_sqm=531.0,
            candidate_runtime_region_id=1,
            candidate_runtime_task_id=100,
            candidate_runtime_pose_x=5910,
            candidate_runtime_pose_y=12400,
            candidate_runtime_heading_deg=63.5,
            candidate_runtime_track_segments=(
                ((9720, 15300), (9720, 15460), (5810, 12180)),
            ),
            notes=("unexpected_length", "unexpected_runtime_progress_value"),
        ),
    )

    assert entity.available is True
    assert entity.native_value == 13.7
    assert entity.extra_state_attributes == {
        "source": "cloud",
        "length": 33,
        "frame_valid": True,
        "area_progress_percent": 13.7,
        "current_area_sqm": 72.95,
        "total_area_sqm": 531.0,
        "region_id": 1,
        "task_id": 100,
        "pose_x": 5910,
        "pose_y": 12400,
        "heading_deg": 63.5,
        "track_segment_count": 1,
        "track_point_count": 3,
        "track_length_m": 52.64,
        "notes": ["unexpected_length", "unexpected_runtime_progress_value"],
    }


def test_runtime_area_and_pose_sensors_use_runtime_blob_values() -> None:
    coordinator = SimpleNamespace(
        data=SimpleNamespace(activity="mowing"),
        runtime_status_blob=SimpleNamespace(
            source="cloud",
            length=33,
            frame_valid=True,
            candidate_runtime_progress_percent=None,
            candidate_runtime_area_progress_percent=13.7,
            candidate_runtime_current_area_sqm=72.95,
            candidate_runtime_total_area_sqm=531.0,
            candidate_runtime_region_id=1,
            candidate_runtime_task_id=100,
            candidate_runtime_pose_x=5910,
            candidate_runtime_pose_y=12400,
            candidate_runtime_heading_deg=63.5,
            candidate_runtime_track_segments=(
                ((9720, 15300), (9720, 15460), (5810, 12180)),
            ),
            notes=("unexpected_length",),
        ),
    )

    current_area = object.__new__(DreameLawnMowerRuntimeCurrentAreaSensor)
    current_area.coordinator = coordinator
    total_area = object.__new__(DreameLawnMowerRuntimeTotalAreaSensor)
    total_area.coordinator = coordinator
    pose_x = object.__new__(DreameLawnMowerRuntimePositionXSensor)
    pose_x.coordinator = coordinator
    pose_y = object.__new__(DreameLawnMowerRuntimePositionYSensor)
    pose_y.coordinator = coordinator
    heading = object.__new__(DreameLawnMowerRuntimeHeadingSensor)
    heading.coordinator = coordinator

    assert current_area.available is True
    assert current_area.native_value == 72.95
    assert total_area.available is True
    assert total_area.native_value == 531.0
    assert pose_x.available is True
    assert pose_x.native_value == 5910
    assert pose_y.available is True
    assert pose_y.native_value == 12400
    assert heading.available is True
    assert heading.native_value == 63.5
    assert heading.extra_state_attributes == {
        "source": "cloud",
        "length": 33,
        "frame_valid": True,
        "area_progress_percent": 13.7,
        "current_area_sqm": 72.95,
        "total_area_sqm": 531.0,
        "region_id": 1,
        "task_id": 100,
        "pose_x": 5910,
        "pose_y": 12400,
        "heading_deg": 63.5,
        "track_segment_count": 1,
        "track_point_count": 3,
        "track_length_m": 52.64,
        "notes": ["unexpected_length"],
    }


def test_runtime_area_and_pose_sensors_hide_stale_blob_values_when_not_active() -> None:
    coordinator = SimpleNamespace(
        data=SimpleNamespace(activity="charging"),
        runtime_status_blob=SimpleNamespace(
            candidate_runtime_current_area_sqm=72.95,
            candidate_runtime_total_area_sqm=531.0,
            candidate_runtime_pose_x=5910,
            candidate_runtime_pose_y=12400,
            candidate_runtime_heading_deg=63.5,
        ),
    )

    current_area = object.__new__(DreameLawnMowerRuntimeCurrentAreaSensor)
    current_area.coordinator = coordinator
    pose_x = object.__new__(DreameLawnMowerRuntimePositionXSensor)
    pose_x.coordinator = coordinator
    heading = object.__new__(DreameLawnMowerRuntimeHeadingSensor)
    heading.coordinator = coordinator

    assert current_area.available is False
    assert current_area.native_value is None
    assert pose_x.available is False
    assert pose_x.native_value is None
    assert heading.available is False
    assert heading.native_value is None


def test_runtime_live_track_sensors_use_current_vector_map_runtime_overlay() -> None:
    coordinator = SimpleNamespace(
        data=SimpleNamespace(activity="mowing"),
        batch_device_data=None,
        app_maps={
            "source": "app_maps_auto",
            "available": True,
            "current_map_index": 1,
            "maps": [
                {"idx": 0, "current": False, "available": True, "created": True},
                {"idx": 1, "current": True, "available": True, "created": True},
            ],
        },
        vector_map_details={
            "source": "batch_vector_map",
            "available": True,
            "maps": [
                {
                    "map_id": 1,
                    "map_index": 0,
                    "map_name": "Front",
                    "has_live_path": False,
                },
                {
                    "map_id": 2,
                    "map_index": 1,
                    "map_name": "Back",
                    "mow_path_count": 1,
                    "mow_path_segment_count": 2,
                    "mow_path_point_count": 4,
                    "mow_path_length_m": 3.75,
                    "runtime_track_segment_count": 3,
                    "runtime_track_point_count": 17,
                    "runtime_track_length_m": 12.4,
                    "runtime_pose_x": 480,
                    "runtime_pose_y": 260,
                    "runtime_heading_deg": 91.5,
                    "has_live_path": True,
                },
            ],
        },
    )

    point_count = object.__new__(DreameLawnMowerRuntimeTrackPointCountSensor)
    point_count.coordinator = coordinator
    track_length = object.__new__(DreameLawnMowerRuntimeTrackLengthSensor)
    track_length.coordinator = coordinator
    segment_count = object.__new__(DreameLawnMowerRuntimeTrackSegmentCountSensor)
    segment_count.coordinator = coordinator

    assert point_count.available is True
    assert point_count.native_value == 17
    assert track_length.available is True
    assert track_length.native_value == 12.4
    assert segment_count.available is True
    assert segment_count.native_value == 3
    assert track_length.extra_state_attributes == {
        "source": "batch_vector_map",
        "current_vector_map": {
            "map_id": 2,
            "map_index": 1,
            "map_name": "Back",
            "mow_path_count": 1,
            "mow_path_segment_count": 2,
            "mow_path_point_count": 4,
            "mow_path_length_m": 3.75,
            "runtime_track_segment_count": 3,
            "runtime_track_point_count": 17,
            "runtime_track_length_m": 12.4,
            "runtime_pose_x": 480,
            "runtime_pose_y": 260,
            "runtime_heading_deg": 91.5,
            "has_live_path": True,
        },
    }


def test_selected_scope_sensors_follow_selected_zone_on_selected_map() -> None:
    coordinator = SimpleNamespace(
        data=SimpleNamespace(activity="paused"),
        selected_mowing_action="zone",
        selected_map_index=1,
        selected_zone_id=3,
        selected_spot_id=None,
        selected_contour_id=None,
        app_maps={
            "current_map_index": 0,
            "maps": [
                {"idx": 0, "current": True, "name": "Front", "available": True},
                {"idx": 1, "current": False, "name": "Back", "available": True},
            ],
        },
        batch_device_data={
            "batch_mowing_preferences": {
                "maps": [
                    {
                        "idx": 0,
                        "preferences": [{"area_id": 1}, {"area_id": 2}],
                    },
                    {
                        "idx": 1,
                        "preferences": [{"area_id": 3}, {"area_id": 4}],
                    },
                ]
            }
        },
        vector_map_details=None,
    )

    action_entity = object.__new__(DreameLawnMowerSelectedMowingActionSensor)
    action_entity.coordinator = coordinator
    map_entity = object.__new__(DreameLawnMowerSelectedMapSensor)
    map_entity.coordinator = coordinator
    target_entity = object.__new__(DreameLawnMowerSelectedTargetSensor)
    target_entity.coordinator = coordinator

    assert action_entity.available is True
    assert action_entity.native_value == "Zone"
    assert map_entity.available is True
    assert map_entity.native_value == "Back (#2)"
    assert target_entity.available is True
    assert target_entity.native_value == "Zone #3"
    assert target_entity.extra_state_attributes == {
        "selected_mowing_action": "zone",
        "selected_mowing_action_label": "Zone",
        "selected_map_index": 1,
        "selected_map_label": "Back (#2)",
        "target_type": "zone",
        "target_id": 3,
        "target_label": "Zone #3",
        "available_target_count": 2,
    }


def test_selected_target_sensor_falls_back_to_first_spot() -> None:
    coordinator = SimpleNamespace(
        data=SimpleNamespace(activity="paused"),
        selected_mowing_action="spot",
        selected_map_index=0,
        selected_zone_id=None,
        selected_spot_id=None,
        selected_contour_id=None,
        app_maps={
            "current_map_index": 0,
            "maps": [
                {
                    "idx": 0,
                    "current": True,
                    "name": "Front",
                    "available": True,
                    "payload": {
                        "spot": [
                            {
                                "id": 9,
                                "data": [[0, 0], [100, 0], [100, 100], [0, 100]],
                            },
                            {
                                "id": 11,
                                "data": [[200, 0], [300, 0], [300, 100], [200, 100]],
                            },
                        ]
                    },
                }
            ],
        },
        batch_device_data=None,
        vector_map_details=None,
    )

    target_entity = object.__new__(DreameLawnMowerSelectedTargetSensor)
    target_entity.coordinator = coordinator

    assert target_entity.available is True
    assert target_entity.native_value == "Spot #9"
    assert target_entity.extra_state_attributes == {
        "selected_mowing_action": "spot",
        "selected_mowing_action_label": "Spot",
        "selected_map_index": 0,
        "selected_map_label": "Front (#1)",
        "target_type": "spot",
        "target_id": 9,
        "target_label": "Spot #9",
        "available_target_count": 2,
    }


def test_selected_target_sensor_uses_selected_edge_on_current_vector_map() -> None:
    coordinator = SimpleNamespace(
        data=SimpleNamespace(activity="paused"),
        selected_mowing_action="edge",
        selected_map_index=1,
        selected_zone_id=None,
        selected_spot_id=None,
        selected_contour_id=(5, 0),
        app_maps={
            "current_map_index": 1,
            "maps": [
                {"idx": 0, "current": False, "name": "Front", "available": True},
                {"idx": 1, "current": True, "name": "Back", "available": True},
            ],
        },
        batch_device_data=None,
        vector_map_details={
            "maps": [
                {"map_index": 0, "contour_ids": [(1, 0), (3, 0)]},
                {"map_index": 1, "contour_ids": [(5, 0), (6, 0)]},
            ]
        },
    )

    target_entity = object.__new__(DreameLawnMowerSelectedTargetSensor)
    target_entity.coordinator = coordinator

    assert target_entity.available is True
    assert target_entity.native_value == "Edge (5, 0)"
    assert target_entity.extra_state_attributes == {
        "selected_mowing_action": "edge",
        "selected_mowing_action_label": "Edge",
        "selected_map_index": 1,
        "selected_map_label": "Back (#2)",
        "target_type": "edge",
        "target_id": [5, 0],
        "target_label": "Edge (5, 0)",
        "available_target_count": 2,
    }
