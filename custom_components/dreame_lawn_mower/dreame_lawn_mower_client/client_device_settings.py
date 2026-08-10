"""Confirmed mower-native device-setting reads and writes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from .client_settings_helpers import _weather_protection_active_summary
from .client_shared_helpers import _app_action_data, _ensure_app_write_succeeded
from .device_settings import (
    build_charging_period_request,
    build_rain_protection_request,
    decode_device_settings,
    validate_rain_delay,
    validate_time_of_day,
)
from .exceptions import (
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
)
from .payload_utils import _json_safe


class _DreameLawnMowerClientDeviceSettingsMixin:
    def _sync_get_device_settings(
        self,
        include_raw: bool = False,
        include_rain_end_time: bool = True,
    ) -> dict[str, Any]:
        """Read CFG once and decode all settings owned by this integration."""
        result: dict[str, Any] = {
            "source": "app_action_device_settings",
            "available": False,
            "config_keys": ["BAT", "WRF", "WRP"],
            "fault_hint": "INFO_BAD_WEATHER_PROTECTING",
            "rain_end_time_command": "RPET",
            "errors": [],
            "warnings": [],
        }
        try:
            config_result = self._sync_call_app_action({"m": "g", "t": "CFG"})
            if include_raw:
                result["raw_config"] = _json_safe(config_result, max_depth=4)
            config = _app_action_data(config_result)
            if not isinstance(config, Mapping):
                raise DreameLawnMowerConnectionError(
                    "CFG returned no device-settings record."
                )
            result["present_config_keys"] = [
                key for key in result["config_keys"] if key in config
            ]
            result.update(decode_device_settings(config))
            rain_end_time = config.get("rainProtectEndTime")
            if rain_end_time is None:
                rain_end_time = config.get("rain_protect_end_time")
            if rain_end_time is not None:
                result["rain_protect_end_time"] = rain_end_time
                result["rain_protect_end_time_present"] = True
            result["available"] = True
        except Exception as err:  # noqa: BLE001 - return partial diagnostic evidence
            result["errors"].append({"stage": "config", "error": str(err)})

        if include_rain_end_time:
            try:
                rain_end_result = self._sync_call_app_action({"m": "g", "t": "RPET"})
                if include_raw:
                    result["raw_rain_end_time"] = _json_safe(
                        rain_end_result,
                        max_depth=4,
                    )
                rain_end_data = _app_action_data(rain_end_result)
                if isinstance(rain_end_data, Mapping):
                    end_time = rain_end_data.get("endTime")
                    if end_time is None:
                        end_time = rain_end_data.get("end_time")
                    result["rain_protect_end_time_present"] = end_time is not None
                    if end_time is not None:
                        result["rain_protect_end_time"] = end_time
                        result["available"] = True
                elif rain_end_data is None:
                    result["rain_protect_end_time_present"] = False
                else:
                    result["warnings"].append(
                        {
                            "stage": "rain_end_time",
                            "warning": "RPET returned unexpected data.",
                        }
                    )
            except Exception as err:  # noqa: BLE001 - RPET may be conditionally available
                result["warnings"].append(
                    {"stage": "rain_end_time", "warning": str(err)}
                )

        result.update(_weather_protection_active_summary(result))
        return result

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

    async def async_get_device_settings(
        self,
        *,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Return decoded mower-native device settings."""
        return await asyncio.to_thread(self._sync_get_device_settings, include_raw)

    async def async_get_weather_protection(
        self,
        *,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Keep the historical weather read API over the CFG settings owner."""
        return await asyncio.to_thread(self._sync_get_weather_protection, include_raw)

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
