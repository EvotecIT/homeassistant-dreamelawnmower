"""Battery thresholds read from the mower's BAT record."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device_settings,
)
from custom_components.dreame_lawn_mower.sensor import (
    async_setup_entry as async_setup_sensor_entry,
)
from custom_components.dreame_lawn_mower.sensor_battery_settings import (
    DreameLawnMowerRechargeBatteryLevelSensor,
    DreameLawnMowerResumeBatteryLevelSensor,
)


def _sensor(cls, settings, data=True):
    sensor = object.__new__(cls)
    sensor.coordinator = SimpleNamespace(
        data=SimpleNamespace() if data else None, device_settings=settings
    )
    return sensor


def test_bat_record_from_a_lidax_ultra_1000_decodes_both_thresholds() -> None:
    # Seven slots as reported by a LiDAX Ultra 1000 (firmware 4.3.6_0529).
    decoded = {
        "available": True,
        **device_settings.decode_charging_settings(
            {"BAT": [15, 80, 1, 0, 1080, 480, 1]}
        ),
    }
    resume = _sensor(DreameLawnMowerResumeBatteryLevelSensor, decoded)
    recharge = _sensor(DreameLawnMowerRechargeBatteryLevelSensor, decoded)

    assert resume.native_value == 80
    assert resume.available is True
    assert resume.extra_state_attributes == {"resume_after_charging": True}
    assert recharge.native_value == 15
    assert recharge.available is True


def test_threshold_is_unavailable_without_a_readable_value() -> None:
    readable = {
        "available": True,
        "charging_settings_available": True,
        "resume_battery_level": 80,
    }
    for settings in (
        None,
        {**readable, "charging_settings_available": False},
        {**readable, "available": False},
        {**readable, "resume_battery_level": 180},
        {**readable, "resume_battery_level": True},
    ):
        sensor = _sensor(DreameLawnMowerResumeBatteryLevelSensor, settings)
        assert sensor.native_value is None
        assert sensor.available is False

    offline = _sensor(
        DreameLawnMowerResumeBatteryLevelSensor,
        {
            "available": True,
            "charging_settings_available": True,
            "resume_battery_level": 80,
        },
        data=False,
    )
    assert offline.available is False


def test_threshold_sensors_are_added_once_when_bat_becomes_readable() -> None:
    listeners: list = []
    coordinator = SimpleNamespace(
        data=None,
        device_settings=None,
        client=SimpleNamespace(
            descriptor=SimpleNamespace(model="dreame.mower.g2408", unique_id="u")
        ),
        async_add_listener=lambda listener: listeners.append(listener) or Mock(),
    )
    entry = SimpleNamespace(
        runtime_data=coordinator, entry_id="entry-1", async_on_unload=Mock()
    )
    added: list[object] = []

    def collect(entities, *args, **kwargs):
        added.extend(
            entity
            for entity in entities
            if isinstance(
                entity,
                (
                    DreameLawnMowerResumeBatteryLevelSensor,
                    DreameLawnMowerRechargeBatteryLevelSensor,
                ),
            )
        )

    asyncio.run(async_setup_sensor_entry(SimpleNamespace(data={}), entry, collect))
    assert added == []

    coordinator.device_settings = {
        "available": True,
        "charging_settings_available": True,
    }
    for listener in listeners:
        listener()
    for listener in listeners:
        listener()

    assert sorted(type(entity).__name__ for entity in added) == [
        "DreameLawnMowerRechargeBatteryLevelSensor",
        "DreameLawnMowerResumeBatteryLevelSensor",
    ]
