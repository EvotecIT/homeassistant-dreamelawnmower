"""Confirmed mower-native device-setting reads and writes."""

from __future__ import annotations

from typing import Any

from .app_read_transport import run_app_read
from .client_transport import _DreameLawnMowerClientTransport
from .device_settings_read_plan import read_device_settings
from .device_settings_write_plan import (
    plan_anti_theft_settings,
    plan_charging_period,
    plan_rain_protection,
    run_settings_write,
)


class _DreameLawnMowerClientDeviceSettingsMixin(_DreameLawnMowerClientTransport):
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
        self, *, enabled: bool | None = None, start_minutes: int | None = None,
        end_minutes: int | None = None,
    ) -> dict[str, Any]:
        return run_settings_write(
            plan_charging_period(enabled=enabled, start_minutes=start_minutes,
                                 end_minutes=end_minutes),
            self._sync_get_device_settings, self._sync_call_app_action,
        )

    def _sync_set_rain_protection(
        self, *, enabled: bool | None = None, delay_hours: int | None = None,
    ) -> dict[str, Any]:
        return run_settings_write(
            plan_rain_protection(enabled=enabled, delay_hours=delay_hours),
            self._sync_get_device_settings, self._sync_call_app_action,
        )

    def _sync_set_anti_theft_settings(
        self, *, lift_alarm_enabled: bool | None = None,
        off_map_alarm_enabled: bool | None = None,
        real_time_location_enabled: bool | None = None,
        pin_check_before_power_off_enabled: bool | None = None,
    ) -> dict[str, Any]:
        return run_settings_write(
            plan_anti_theft_settings(
                lift_alarm_enabled=lift_alarm_enabled,
                off_map_alarm_enabled=off_map_alarm_enabled,
                real_time_location_enabled=real_time_location_enabled,
                pin_check_before_power_off_enabled=pin_check_before_power_off_enabled,
            ), self._sync_get_device_settings, self._sync_call_app_action,
        )
