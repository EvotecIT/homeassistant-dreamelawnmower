"""Contracts for mower-native charging and rain settings."""

from __future__ import annotations

import asyncio
from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.dreame_lawn_mower.button import (
    DreameLawnMowerCaptureWeatherProbeButton,
)
from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.coordinator import (
    DreameLawnMowerCoordinator,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device_settings,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client import (
    DreameLawnMowerClient,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerCommandRejectedError,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
)
from custom_components.dreame_lawn_mower.preference_switch import (
    DreameLawnMowerPreferenceAiClassSwitch,
)
from custom_components.dreame_lawn_mower.select import (
    DreameLawnMowerRainDelaySelect,
)
from custom_components.dreame_lawn_mower.select import (
    async_setup_entry as async_setup_select_entry,
)
from custom_components.dreame_lawn_mower.switch import (
    DreameLawnMowerAntiTheftSwitch,
    DreameLawnMowerChargingPeriodSwitch,
    DreameLawnMowerRainProtectionSwitch,
    reported_preference_switch_keys,
)
from custom_components.dreame_lawn_mower.switch import (
    async_setup_entry as async_setup_switch_entry,
)
from custom_components.dreame_lawn_mower.time import (
    DreameLawnMowerChargingPeriodEndTime,
    DreameLawnMowerChargingPeriodStartTime,
)
from custom_components.dreame_lawn_mower.time import (
    async_setup_entry as async_setup_time_entry,
)


def _client() -> DreameLawnMowerClient:
    return DreameLawnMowerClient(
        username="user@example.invalid",
        password="secret",
        country="eu",
        account_type="dreame",
        descriptor=DreameLawnMowerDescriptor(
            did="device-1",
            name="Garden Mower",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
    )


def _cfg(
    *,
    charging_enabled: int = 0,
    rain_enabled: int = 1,
    anti_theft: list[int] | None = None,
) -> dict:
    payload = {
        "m": "r",
        "r": 0,
        "d": {
            "BAT": [15, 95, 1, charging_enabled, 1080, 480],
            "WRF": 1,
            "WRP": [rain_enabled, 8, 1],
        },
    }
    if anti_theft is not None:
        payload["d"]["ATA"] = anti_theft
    return payload


def test_decode_device_settings_keeps_battery_thresholds_separate() -> None:
    assert device_settings.BATTERY_SETTING_LENGTH == 6
    settings = device_settings.decode_device_settings(_cfg()["d"])

    assert settings == {
        "charging_settings_available": True,
        "recharge_battery_level": 15,
        "resume_battery_level": 95,
        "resume_after_charging": True,
        "charging_period_enabled": False,
        "charging_period_start_minutes": 1080,
        "charging_period_end_minutes": 480,
        "battery_settings_raw": [15, 95, 1, 0, 1080, 480],
        "rain_settings_available": True,
        "rain_protection_enabled": True,
        "rain_protection_duration_hours": 8,
        "rain_sensor_sensitivity": 1,
        "rain_protection_raw": [1, 8, 1],
        "weather_switch_enabled": True,
    }


def test_setting_payloads_are_narrow_and_preserve_rain_sensitivity() -> None:
    assert device_settings.build_charging_period_request(
        enabled=True,
        start_minutes=1320,
        end_minutes=360,
    ) == {
        "m": "s",
        "t": "BAT",
        "d": {"type": "charging", "value": [1, 1320, 360]},
    }
    assert device_settings.build_rain_protection_request(
        enabled=False,
        delay_hours=3,
        sensitivity=1,
    ) == {
        "m": "s",
        "t": "WRP",
        "d": {"value": 0, "time": 3, "sen": 1},
    }


def test_anti_theft_codec_reports_only_fields_present_on_the_mower() -> None:
    three_field = device_settings.decode_device_settings(
        _cfg(anti_theft=[0, 1, 0])["d"]
    )
    assert three_field["anti_theft_supported_settings"] == [
        "lift_alarm_enabled",
        "off_map_alarm_enabled",
        "real_time_location_enabled",
    ]
    assert three_field["off_map_alarm_enabled"] is True
    assert "pin_check_before_power_off_enabled" not in three_field

    four_field = device_settings.decode_device_settings(
        _cfg(anti_theft=[1, 0, 1, 1, 7])["d"]
    )
    assert four_field["pin_check_before_power_off_enabled"] is True
    assert four_field["anti_theft_settings_raw"] == [1, 0, 1, 1, 7]


def test_anti_theft_request_preserves_unmodified_and_future_fields() -> None:
    assert device_settings.build_anti_theft_settings_request(
        [0, 1, 0, 1, 7],
        lift_alarm_enabled=True,
        pin_check_before_power_off_enabled=False,
    ) == {
        "m": "s",
        "t": "ATA",
        "d": {"value": [1, 1, 0, 0, 7]},
    }

    with pytest.raises(ValueError, match="not reported"):
        device_settings.build_anti_theft_settings_request(
            [0, 0, 0],
            pin_check_before_power_off_enabled=True,
        )


def test_charging_write_preserves_times_and_requires_cfg_readback() -> None:
    client = _client()
    responses = iter(
        [
            _cfg(charging_enabled=0),
            {"m": "r", "r": 0, "d": {}},
            _cfg(charging_enabled=1),
        ]
    )
    requests: list[dict] = []

    def call(request: dict, **kwargs) -> dict:  # noqa: ARG001
        requests.append(request)
        return next(responses)

    client._sync_call_app_action = call  # type: ignore[method-assign]
    settings = client._sync_set_charging_period(enabled=True)

    assert settings["charging_period_enabled"] is True
    assert requests[1] == {
        "m": "s",
        "t": "BAT",
        "d": {"type": "charging", "value": [1, 1080, 480]},
    }


def test_charging_write_rejects_acknowledged_but_ignored_update() -> None:
    client = _client()
    responses = iter(
        [
            _cfg(charging_enabled=0),
            {"m": "r", "r": 0, "d": {}},
            _cfg(charging_enabled=0),
        ]
    )
    client._sync_call_app_action = lambda request, **kwargs: next(responses)  # type: ignore[method-assign]  # noqa: ARG005

    with pytest.raises(DreameLawnMowerCommandRejectedError, match="did not confirm"):
        client._sync_set_charging_period(enabled=True)


def test_charging_write_rejects_changed_battery_threshold() -> None:
    client = _client()
    changed = _cfg(charging_enabled=1)
    changed["d"]["BAT"][0] = 20
    responses = iter(
        [
            _cfg(charging_enabled=0),
            {"m": "r", "r": 0, "d": {}},
            changed,
        ]
    )
    client._sync_call_app_action = lambda request, **kwargs: next(responses)  # type: ignore[method-assign]  # noqa: ARG005

    with pytest.raises(DreameLawnMowerCommandRejectedError, match="thresholds"):
        client._sync_set_charging_period(enabled=True)


def test_rain_write_preserves_sensitivity_and_confirms_delay() -> None:
    client = _client()
    responses = iter(
        [
            _cfg(rain_enabled=1),
            {"m": "r", "r": 0, "d": {}},
            _cfg(rain_enabled=0),
            {"m": "r", "r": 0, "d": {"endTime": 0}},
        ]
    )
    requests: list[dict] = []

    def call(request: dict, **kwargs) -> dict:  # noqa: ARG001
        requests.append(request)
        return next(responses)

    client._sync_call_app_action = call  # type: ignore[method-assign]
    settings = client._sync_set_rain_protection(enabled=False)

    assert settings["rain_protection_enabled"] is False
    assert requests[1] == {
        "m": "s",
        "t": "WRP",
        "d": {"value": 0, "time": 8, "sen": 1},
    }


def test_rain_write_rejects_changed_sensitivity() -> None:
    client = _client()
    changed = _cfg(rain_enabled=0)
    changed["d"]["WRP"][2] = 0
    responses = iter(
        [
            _cfg(rain_enabled=1),
            {"m": "r", "r": 0, "d": {}},
            changed,
            {"m": "r", "r": 0, "d": {"endTime": 0}},
        ]
    )
    client._sync_call_app_action = lambda request, **kwargs: next(responses)  # type: ignore[method-assign]  # noqa: ARG005

    with pytest.raises(DreameLawnMowerCommandRejectedError, match="sensitivity"):
        client._sync_set_rain_protection(enabled=False)


def test_anti_theft_write_preserves_other_flags_and_requires_readback() -> None:
    client = _client()
    responses = iter(
        [
            _cfg(anti_theft=[0, 1, 0]),
            {"m": "r", "r": 0, "d": {}},
            _cfg(anti_theft=[1, 1, 0]),
        ]
    )
    requests: list[dict] = []

    def call(request: dict, **kwargs) -> dict:  # noqa: ARG001
        requests.append(request)
        return next(responses)

    client._sync_call_app_action = call  # type: ignore[method-assign]
    settings = client._sync_set_anti_theft_settings(lift_alarm_enabled=True)

    assert settings["lift_alarm_enabled"] is True
    assert requests[1] == {
        "m": "s",
        "t": "ATA",
        "d": {"value": [1, 1, 0]},
    }


def test_anti_theft_write_rejects_acknowledged_but_ignored_update() -> None:
    client = _client()
    responses = iter(
        [
            _cfg(anti_theft=[0, 0, 0]),
            {"m": "r", "r": 0, "d": {}},
            _cfg(anti_theft=[0, 0, 0]),
        ]
    )
    client._sync_call_app_action = lambda request, **kwargs: next(responses)  # type: ignore[method-assign]  # noqa: ARG005

    with pytest.raises(DreameLawnMowerCommandRejectedError, match="did not confirm"):
        client._sync_set_anti_theft_settings(lift_alarm_enabled=True)


def test_weather_probe_updates_canonical_device_settings_cache() -> None:
    payload = {
        "source": "app_action_weather_protection",
        "available": True,
        "present_config_keys": ["BAT", "WRP"],
        "errors": [],
        "charging_settings_available": True,
        "rain_settings_available": True,
    }
    coordinator = SimpleNamespace(
        client=SimpleNamespace(
            descriptor=SimpleNamespace(title="Test mower"),
        ),
        async_capture_weather_protection=AsyncMock(return_value=payload),
        hass=SimpleNamespace(),
        entry=SimpleNamespace(entry_id="entry-1"),
    )
    button = object.__new__(DreameLawnMowerCaptureWeatherProbeButton)
    button.coordinator = coordinator

    with patch(
        "custom_components.dreame_lawn_mower.button.persistent_notification.async_create"
    ):
        asyncio.run(button.async_press())

    coordinator.async_capture_weather_protection.assert_awaited_once_with()


def test_weather_probe_serializes_and_updates_canonical_cache() -> None:
    payload = {
        "available": True,
        "present_config_keys": ["BAT", "WRP"],
        "errors": [],
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._device_settings_write_lock = asyncio.Lock()
    coordinator.client = SimpleNamespace(
        async_get_weather_protection=AsyncMock(return_value=payload)
    )
    coordinator.last_weather_probe_result = None
    coordinator.device_settings = None
    coordinator.weather_protection = None
    coordinator.weather_protection_refreshed_at = None
    coordinator.async_update_listeners = Mock()

    result = asyncio.run(coordinator.async_capture_weather_protection())

    assert result is payload
    assert coordinator.last_weather_probe_result is payload
    assert coordinator.device_settings["source"] == "weather_probe"
    assert coordinator.weather_protection == coordinator.device_settings
    coordinator.async_update_listeners.assert_called_once_with()


def test_device_settings_refresh_and_write_cache_are_serialized() -> None:
    old_payload = {
        "available": True,
        "present_config_keys": ["BAT", "WRP"],
        "errors": [],
        "charging_period_enabled": False,
    }
    new_payload = {
        **old_payload,
        "charging_period_enabled": True,
    }

    async def scenario() -> None:
        read_started = asyncio.Event()
        release_read = asyncio.Event()

        async def read_settings(*, include_raw: bool) -> dict:
            assert include_raw is False
            read_started.set()
            await release_read.wait()
            return old_payload

        coordinator = object.__new__(DreameLawnMowerCoordinator)
        coordinator._device_settings_write_lock = asyncio.Lock()
        coordinator.client = SimpleNamespace(
            async_get_device_settings=read_settings,
            async_set_charging_period=AsyncMock(return_value=new_payload),
        )
        coordinator.device_settings = None
        coordinator.weather_protection = None
        coordinator.weather_protection_refreshed_at = None
        coordinator.async_update_listeners = Mock()

        refresh_task = asyncio.create_task(
            coordinator.async_refresh_device_settings(force=True)
        )
        await read_started.wait()
        write_task = asyncio.create_task(
            coordinator.async_set_charging_period(enabled=True)
        )
        await asyncio.sleep(0)
        assert write_task.done() is False

        release_read.set()
        await asyncio.gather(refresh_task, write_task)

        assert coordinator.device_settings["charging_period_enabled"] is True
        coordinator.client.async_set_charging_period.assert_awaited_once_with(
            enabled=True,
            start_minutes=None,
            end_minutes=None,
        )

    asyncio.run(scenario())


def test_settings_entities_are_thin_over_confirmed_coordinator_writes() -> None:
    settings = {
        "available": True,
        "charging_settings_available": True,
        "charging_period_enabled": False,
        "charging_period_start_minutes": 1080,
        "charging_period_end_minutes": 480,
        "rain_settings_available": True,
        "rain_protection_enabled": True,
        "rain_protection_duration_hours": 8,
        "anti_theft_settings_available": True,
        "anti_theft_supported_settings": ["lift_alarm_enabled"],
        "lift_alarm_enabled": False,
    }
    coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        device_settings=settings,
        async_set_charging_period=AsyncMock(),
        async_set_rain_protection=AsyncMock(),
        async_set_anti_theft_settings=AsyncMock(),
    )

    charging = object.__new__(DreameLawnMowerChargingPeriodSwitch)
    charging.coordinator = coordinator
    rain = object.__new__(DreameLawnMowerRainProtectionSwitch)
    rain.coordinator = coordinator
    start = object.__new__(DreameLawnMowerChargingPeriodStartTime)
    start.coordinator = coordinator
    end = object.__new__(DreameLawnMowerChargingPeriodEndTime)
    end.coordinator = coordinator
    delay = object.__new__(DreameLawnMowerRainDelaySelect)
    delay.coordinator = coordinator
    delay._hours_by_label = {"3 hours": 3}
    lift_alarm = object.__new__(DreameLawnMowerAntiTheftSwitch)
    lift_alarm.coordinator = coordinator
    lift_alarm._setting_key = "lift_alarm_enabled"

    assert charging.available is True
    assert charging.is_on is False
    assert rain.is_on is True
    assert start.native_value == time(18, 0)
    assert end.native_value == time(8, 0)
    assert lift_alarm.available is True
    assert lift_alarm.is_on is False

    asyncio.run(charging.async_turn_on())
    asyncio.run(rain.async_turn_off())
    asyncio.run(start.async_set_value(time(22, 30)))
    asyncio.run(end.async_set_value(time(6, 15)))
    asyncio.run(delay.async_select_option("3 hours"))
    asyncio.run(lift_alarm.async_turn_on())

    coordinator.async_set_charging_period.assert_any_await(enabled=True)
    coordinator.async_set_charging_period.assert_any_await(start_minutes=1350)
    coordinator.async_set_charging_period.assert_any_await(end_minutes=375)
    coordinator.async_set_rain_protection.assert_any_await(enabled=False)
    coordinator.async_set_rain_protection.assert_any_await(delay_hours=3)
    coordinator.async_set_anti_theft_settings.assert_awaited_once_with(
        lift_alarm_enabled=True
    )


def test_reported_preference_switch_keys_omit_missing_optional_fields() -> None:
    result = reported_preference_switch_keys(
        {
            "batch_mowing_preferences": {
                "maps": [
                    {
                        "preferences": [
                            {
                                "edge_mowing_auto": True,
                                "edge_mowing_safe": False,
                                "edge_cutting_attachment": None,
                                "obstacle_avoidance_ai": 3,
                            }
                        ]
                    }
                ]
            }
        }
    )

    assert result == {
        "edge_mowing_auto",
        "edge_mowing_safe",
        "obstacle_avoidance_ai",
    }


def test_mova_awd_switch_platform_omits_locked_ai_classes() -> None:
    descriptor = DreameLawnMowerDescriptor(
        did="device-1",
        name="MOVA mower",
        model="mova.mower.g2584a",
        display_model="LiDAX Ultra 2000 AWD",
        account_type="mova",
        country="us",
    )
    coordinator = SimpleNamespace(
        client=SimpleNamespace(descriptor=descriptor),
        data=SimpleNamespace(),
        device_settings=None,
        batch_device_data={
            "batch_mowing_preferences": {
                "maps": [
                    {
                        "preferences": [
                            {
                                "obstacle_avoidance_enabled": True,
                                "obstacle_avoidance_ai": 4,
                            }
                        ]
                    }
                ]
            }
        },
        schedules=None,
        async_add_listener=lambda listener: Mock(),  # noqa: ARG005
    )
    hass = SimpleNamespace(data={DOMAIN: {"entry-1": coordinator}})
    entry = SimpleNamespace(entry_id="entry-1", async_on_unload=Mock())
    added: list[object] = []

    asyncio.run(async_setup_switch_entry(hass, entry, added.extend))

    ai_switches = [
        entity
        for entity in added
        if isinstance(entity, DreameLawnMowerPreferenceAiClassSwitch)
    ]
    assert [entity._preference_description.key for entity in ai_switches] == [
        "objects"
    ]


def test_setting_platforms_add_only_reported_entities_and_follow_late_discovery() -> (
    None
):
    descriptor = _client().descriptor
    listeners: list = []
    coordinator = SimpleNamespace(
        client=SimpleNamespace(descriptor=descriptor),
        data=SimpleNamespace(),
        device_settings=None,
        batch_device_data=None,
        schedules=None,
        async_add_listener=lambda listener: listeners.append(listener) or Mock(),
    )
    hass = SimpleNamespace(data={DOMAIN: {"entry-1": coordinator}})
    entry = SimpleNamespace(entry_id="entry-1", async_on_unload=Mock())
    added: list[object] = []

    async def setup() -> None:
        await async_setup_switch_entry(hass, entry, added.extend)
        await async_setup_select_entry(hass, entry, added.extend)
        await async_setup_time_entry(hass, entry, added.extend)

    asyncio.run(setup())

    assert not any(
        isinstance(
            entity,
            (
                DreameLawnMowerAntiTheftSwitch,
                DreameLawnMowerChargingPeriodSwitch,
                DreameLawnMowerRainProtectionSwitch,
                DreameLawnMowerRainDelaySelect,
                DreameLawnMowerChargingPeriodStartTime,
                DreameLawnMowerChargingPeriodEndTime,
            ),
        )
        for entity in added
    )

    coordinator.device_settings = {
        "available": True,
        "charging_settings_available": True,
        "rain_settings_available": True,
        "anti_theft_settings_available": True,
        "anti_theft_supported_settings": [
            "lift_alarm_enabled",
            "off_map_alarm_enabled",
            "real_time_location_enabled",
        ],
    }
    for listener in listeners:
        listener()

    assert (
        sum(isinstance(entity, DreameLawnMowerAntiTheftSwitch) for entity in added) == 3
    )
    assert not any(
        isinstance(entity, DreameLawnMowerAntiTheftSwitch)
        and entity._setting_key == "pin_check_before_power_off_enabled"
        for entity in added
    )
    assert (
        sum(isinstance(entity, DreameLawnMowerChargingPeriodSwitch) for entity in added)
        == 1
    )
    assert (
        sum(isinstance(entity, DreameLawnMowerRainProtectionSwitch) for entity in added)
        == 1
    )
    assert (
        sum(isinstance(entity, DreameLawnMowerRainDelaySelect) for entity in added) == 1
    )
    assert (
        sum(
            isinstance(entity, DreameLawnMowerChargingPeriodStartTime)
            for entity in added
        )
        == 1
    )
    assert (
        sum(
            isinstance(entity, DreameLawnMowerChargingPeriodEndTime) for entity in added
        )
        == 1
    )

    for listener in listeners:
        listener()
    assert (
        sum(isinstance(entity, DreameLawnMowerAntiTheftSwitch) for entity in added) == 3
    )
