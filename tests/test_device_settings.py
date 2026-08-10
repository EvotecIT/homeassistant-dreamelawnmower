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
from custom_components.dreame_lawn_mower.select import (
    DreameLawnMowerRainDelaySelect,
)
from custom_components.dreame_lawn_mower.switch import (
    DreameLawnMowerChargingPeriodSwitch,
    DreameLawnMowerRainProtectionSwitch,
)
from custom_components.dreame_lawn_mower.time import (
    DreameLawnMowerChargingPeriodEndTime,
    DreameLawnMowerChargingPeriodStartTime,
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


def _cfg(*, charging_enabled: int = 0, rain_enabled: int = 1) -> dict:
    return {
        "m": "r",
        "r": 0,
        "d": {
            "BAT": [15, 95, 1, charging_enabled, 1080, 480],
            "WRF": 1,
            "WRP": [rain_enabled, 8, 1],
        },
    }


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
    }
    coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        device_settings=settings,
        async_set_charging_period=AsyncMock(),
        async_set_rain_protection=AsyncMock(),
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

    assert charging.available is True
    assert charging.is_on is False
    assert rain.is_on is True
    assert start.native_value == time(18, 0)
    assert end.native_value == time(8, 0)

    asyncio.run(charging.async_turn_on())
    asyncio.run(rain.async_turn_off())
    asyncio.run(start.async_set_value(time(22, 30)))
    asyncio.run(end.async_set_value(time(6, 15)))
    asyncio.run(delay.async_select_option("3 hours"))

    coordinator.async_set_charging_period.assert_any_await(enabled=True)
    coordinator.async_set_charging_period.assert_any_await(start_minutes=1350)
    coordinator.async_set_charging_period.assert_any_await(end_minutes=375)
    coordinator.async_set_rain_protection.assert_any_await(enabled=False)
    coordinator.async_set_rain_protection.assert_any_await(delay_hours=3)
