"""Shared device-setting mutation policy, independent of HTTP transport."""
from __future__ import annotations

from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from typing import Any

from .client_shared_helpers import _ensure_app_write_succeeded
from .device_settings import (
    build_anti_theft_settings_request,
    build_charging_period_request,
    build_rain_protection_request,
    validate_rain_delay,
    validate_time_of_day,
)
from .exceptions import (
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
)


@dataclass(frozen=True)
class ReadDeviceSettings:
    include_rain_end_time: bool


@dataclass(frozen=True)
class WriteDeviceSettings:
    action: Mapping[str, Any]


type SettingsPlan = Generator[
    ReadDeviceSettings | WriteDeviceSettings, Any, dict[str, Any]
]


def run_settings_write(
    plan: SettingsPlan, read: Callable[..., dict[str, Any]],
    write: Callable[[Mapping[str, Any]], Any],
) -> dict[str, Any]:
    """Drive the shared preflight/write/readback through the sync transport."""
    try:
        request = next(plan)
        while True:
            response = (
                read(include_rain_end_time=request.include_rain_end_time)
                if isinstance(request, ReadDeviceSettings) else write(request.action)
            )
            try:
                request = plan.send(response)
            except StopIteration as completed:
                result: dict[str, Any] = completed.value
                return result
    finally:
        plan.close()


def plan_charging_period(
    *,
    enabled: bool | None = None,
    start_minutes: int | None = None,
    end_minutes: int | None = None,
) -> SettingsPlan:
    """Update only the BAT charging window and require CFG readback."""
    current: dict[str, Any] = (yield ReadDeviceSettings(False))
    if not current.get("charging_settings_available"):
        raise DreameLawnMowerConnectionError(
            "The mower did not report charging-period settings."
        )
    next_enabled = (
        bool(current["charging_period_enabled"])
        if enabled is None
        else bool(enabled)
    )
    next_start = (
        int(current["charging_period_start_minutes"])
        if start_minutes is None
        else validate_time_of_day(start_minutes, "start")
    )
    next_end = (
        int(current["charging_period_end_minutes"])
        if end_minutes is None
        else validate_time_of_day(end_minutes, "end")
    )
    request = build_charging_period_request(
        enabled=next_enabled,
        start_minutes=next_start,
        end_minutes=next_end,
    )
    response = (yield WriteDeviceSettings(request))
    _ensure_app_write_succeeded(response, operation="Charging period update")
    refreshed: dict[str, Any] = (yield ReadDeviceSettings(False))
    expected = (
        next_enabled,
        next_start,
        next_end,
        current.get("recharge_battery_level"),
        current.get("resume_battery_level"),
        current.get("resume_after_charging"),
    )
    actual = (
        refreshed.get("charging_period_enabled"),
        refreshed.get("charging_period_start_minutes"),
        refreshed.get("charging_period_end_minutes"),
        refreshed.get("recharge_battery_level"),
        refreshed.get("resume_battery_level"),
        refreshed.get("resume_after_charging"),
    )
    if actual != expected:
        raise DreameLawnMowerCommandRejectedError(
            "The mower acknowledged the charging-period update but CFG did "
            "not confirm the requested values and preserved BAT thresholds."
        )
    return refreshed

def plan_rain_protection(
    *,
    enabled: bool | None = None,
    delay_hours: int | None = None,
) -> SettingsPlan:
    """Update WRP while preserving sensitivity and require CFG readback."""
    current: dict[str, Any] = (yield ReadDeviceSettings(False))
    if not current.get("rain_settings_available"):
        raise DreameLawnMowerConnectionError(
            "The mower did not report rain-protection settings."
        )
    next_enabled = (
        bool(current["rain_protection_enabled"])
        if enabled is None
        else bool(enabled)
    )
    next_delay = (
        int(current["rain_protection_duration_hours"])
        if delay_hours is None
        else validate_rain_delay(delay_hours)
    )
    request = build_rain_protection_request(
        enabled=next_enabled,
        delay_hours=next_delay,
        sensitivity=int(current["rain_sensor_sensitivity"]),
    )
    response = (yield WriteDeviceSettings(request))
    _ensure_app_write_succeeded(response, operation="Rain protection update")
    refreshed: dict[str, Any] = (yield ReadDeviceSettings(True))
    expected = (
        next_enabled,
        next_delay,
        current.get("rain_sensor_sensitivity"),
    )
    actual = (
        refreshed.get("rain_protection_enabled"),
        refreshed.get("rain_protection_duration_hours"),
        refreshed.get("rain_sensor_sensitivity"),
    )
    if actual != expected:
        raise DreameLawnMowerCommandRejectedError(
            "The mower acknowledged the rain-protection update but CFG did "
            "not confirm the requested values and preserved sensitivity."
        )
    return refreshed

def plan_anti_theft_settings(
    *,
    lift_alarm_enabled: bool | None = None,
    off_map_alarm_enabled: bool | None = None,
    real_time_location_enabled: bool | None = None,
    pin_check_before_power_off_enabled: bool | None = None,
) -> SettingsPlan:
    """Update ATA flags and require an exact CFG readback."""
    current: dict[str, Any] = (yield ReadDeviceSettings(False))
    if not current.get("anti_theft_settings_available"):
        raise DreameLawnMowerConnectionError(
            "The mower did not report anti-theft settings."
        )
    request = build_anti_theft_settings_request(
        current["anti_theft_settings_raw"],
        lift_alarm_enabled=lift_alarm_enabled,
        off_map_alarm_enabled=off_map_alarm_enabled,
        real_time_location_enabled=real_time_location_enabled,
        pin_check_before_power_off_enabled=pin_check_before_power_off_enabled,
    )
    response = (yield WriteDeviceSettings(request))
    _ensure_app_write_succeeded(response, operation="Anti-theft settings update")
    refreshed: dict[str, Any] = (yield ReadDeviceSettings(False))
    if refreshed.get("anti_theft_settings_raw") != request["d"]["value"]:
        raise DreameLawnMowerCommandRejectedError(
            "The mower acknowledged the anti-theft update but CFG did not "
            "confirm the requested values."
        )
    return refreshed
