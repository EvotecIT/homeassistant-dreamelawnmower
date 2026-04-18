"""Regression checks for dynamic mower entity availability."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.dreame_lawn_mower.binary_sensor import (
    BINARY_SENSORS,
    DreameLawnMowerBinarySensor,
)
from custom_components.dreame_lawn_mower.sensor import (
    SENSORS,
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
