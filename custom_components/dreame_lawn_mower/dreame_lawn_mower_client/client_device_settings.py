"""Confirmed mower-native device-setting reads and writes."""

from __future__ import annotations

import asyncio
from typing import Any

from .app_read_transport import run_app_read
from .client_shared_helpers import _ensure_app_write_succeeded
from .device_settings import (
    build_anti_theft_settings_request,
    build_charging_period_request,
    build_rain_protection_request,
    validate_rain_delay,
    validate_time_of_day,
)
from .device_settings_read_plan import read_device_settings
from .exceptions import (
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
)


class _DreameLawnMowerClientDeviceSettingsMixin:
    def _sync_get_device_settings(
        self,
        include_raw: bool = False,
        include_rain_end_time: bool = True,
    ) -> dict[str, Any]:
        """Read CFG once and decode all settings owned by this integration."""
        return run_app_read(
            read_device_settings(include_raw, include_rain_end_time),
            self._sync_call_app_action,
        )

    def _sync_get_weather_protection(
        self,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Keep the historical weather read contract over the settings owner."""
        result = self._sync_get_device_settings(include_raw=include_raw)
        result["source"] = "app_action_weather_protection"
        return result

    def _sync_set_charging_period(
        self,
        *,
        enabled: bool | None = None,
        start_minutes: int | None = None,
        end_minutes: int | None = None,
    ) -> dict[str, Any]:
        """Update only the BAT charging window and require CFG readback."""
        current = self._sync_get_device_settings(include_rain_end_time=False)
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
        response = self._sync_call_app_action(request)
        _ensure_app_write_succeeded(response, operation="Charging period update")
        refreshed = self._sync_get_device_settings(include_rain_end_time=False)
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

    def _sync_set_rain_protection(
        self,
        *,
        enabled: bool | None = None,
        delay_hours: int | None = None,
    ) -> dict[str, Any]:
        """Update WRP while preserving sensitivity and require CFG readback."""
        current = self._sync_get_device_settings(include_rain_end_time=False)
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
        response = self._sync_call_app_action(request)
        _ensure_app_write_succeeded(response, operation="Rain protection update")
        refreshed = self._sync_get_device_settings(include_rain_end_time=True)
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

    def _sync_set_anti_theft_settings(
        self,
        *,
        lift_alarm_enabled: bool | None = None,
        off_map_alarm_enabled: bool | None = None,
        real_time_location_enabled: bool | None = None,
        pin_check_before_power_off_enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Update ATA flags and require an exact CFG readback."""
        current = self._sync_get_device_settings(include_rain_end_time=False)
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
        response = self._sync_call_app_action(request)
        _ensure_app_write_succeeded(response, operation="Anti-theft settings update")
        refreshed = self._sync_get_device_settings(include_rain_end_time=False)
        if refreshed.get("anti_theft_settings_raw") != request["d"]["value"]:
            raise DreameLawnMowerCommandRejectedError(
                "The mower acknowledged the anti-theft update but CFG did not "
                "confirm the requested values."
            )
        return refreshed

    async def async_set_charging_period(
        self,
        *,
        enabled: bool | None = None,
        start_minutes: int | None = None,
        end_minutes: int | None = None,
    ) -> dict[str, Any]:
        """Set and confirm the mower-native charging period."""
        return await asyncio.to_thread(
            self._sync_set_charging_period,
            enabled=enabled,
            start_minutes=start_minutes,
            end_minutes=end_minutes,
        )

    async def async_set_rain_protection(
        self,
        *,
        enabled: bool | None = None,
        delay_hours: int | None = None,
    ) -> dict[str, Any]:
        """Set and confirm the mower-native rain protection settings."""
        return await asyncio.to_thread(
            self._sync_set_rain_protection,
            enabled=enabled,
            delay_hours=delay_hours,
        )

    async def async_set_anti_theft_settings(
        self,
        *,
        lift_alarm_enabled: bool | None = None,
        off_map_alarm_enabled: bool | None = None,
        real_time_location_enabled: bool | None = None,
        pin_check_before_power_off_enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Set and confirm the mower-native anti-theft flags."""
        return await asyncio.to_thread(
            self._sync_set_anti_theft_settings,
            lift_alarm_enabled=lift_alarm_enabled,
            off_map_alarm_enabled=off_map_alarm_enabled,
            real_time_location_enabled=real_time_location_enabled,
            pin_check_before_power_off_enabled=pin_check_before_power_off_enabled,
        )
