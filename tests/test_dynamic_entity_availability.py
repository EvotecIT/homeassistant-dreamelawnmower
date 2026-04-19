"""Regression checks for dynamic mower entity availability."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.dreame_lawn_mower.binary_sensor import (
    BINARY_SENSORS,
    DreameLawnMowerBinarySensor,
)
from custom_components.dreame_lawn_mower.sensor import (
    SENSORS,
    DreameLawnMowerLastPreferenceProbeSensor,
    DreameLawnMowerLastScheduleProbeSensor,
    DreameLawnMowerLastScheduleWriteSensor,
    DreameLawnMowerLastWeatherProbeSensor,
    DreameLawnMowerSensor,
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

    assert entity.native_value == "available"
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
