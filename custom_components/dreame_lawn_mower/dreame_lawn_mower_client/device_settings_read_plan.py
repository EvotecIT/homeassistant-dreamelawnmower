"""Shared read-only device settings plans for sync and native async transports."""

from __future__ import annotations

from collections.abc import Generator, Mapping
from typing import Any

from .app_read_transport import AppReadRequest
from .client_settings_helpers import (
    _voice_settings_summary,
    _weather_protection_active_summary,
)
from .client_shared_helpers import _app_action_data
from .device_settings import decode_device_settings
from .exceptions import DreameLawnMowerConnectionError
from .maintenance import CMS_GET_REQUEST, maintenance_status_from_app_data
from .payload_utils import _json_safe


def read_device_settings(
    include_raw: bool = False, include_rain_end_time: bool = True,
) -> Generator[AppReadRequest, Any, dict[str, Any]]:
    result: dict[str, Any] = {
        "source": "app_action_device_settings",
        "available": False,
        "config_keys": ["ATA", "BAT", "WRF", "WRP"],
        "fault_hint": "INFO_BAD_WEATHER_PROTECTING",
        "rain_end_time_command": "RPET",
        "errors": [],
        "warnings": [],
    }
    try:
        config_result = yield AppReadRequest({"m": "g", "t": "CFG"})
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
            rain_end_result = yield AppReadRequest({"m": "g", "t": "RPET"})
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


def read_maintenance(
    include_raw: bool = False,
) -> Generator[AppReadRequest, Any, dict[str, Any]]:
    result: dict[str, Any] = {
        "source": "app_action_maintenance_cms",
        "available": False,
        "items": [],
        "raw_cms": None,
        "errors": [],
    }

    try:
        cms_result = yield AppReadRequest(CMS_GET_REQUEST)
        if include_raw:
            result["raw_cms_response"] = _json_safe(cms_result, max_depth=4)
        cms_data = _app_action_data(cms_result)
        result.update(
            maintenance_status_from_app_data(
                cms_data,
                source="app_action_maintenance_cms",
            )
        )
        if result.get("available"):
            return result
    except Exception as err:  # noqa: BLE001 - fallback to CFG may still work
        result["errors"].append({"stage": "cms", "error": str(err)})

    try:
        config_result = yield AppReadRequest({"m": "g", "t": "CFG"})
        if include_raw:
            result["raw_config_response"] = _json_safe(config_result, max_depth=4)
        config = _app_action_data(config_result)
        result.update(
            maintenance_status_from_app_data(
                config,
                source="app_action_config_cms",
            )
        )
    except Exception as err:  # noqa: BLE001 - diagnostic probe returns evidence
        result["errors"].append({"stage": "config", "error": str(err)})
    return result


def read_voice(
    include_raw: bool = False,
) -> Generator[AppReadRequest, Any, dict[str, Any]]:
    result: dict[str, Any] = {
        "source": "app_action_voice_settings",
        "available": False,
        "config_keys": ["LANG", "VOL", "VOICE"],
        "errors": [],
        "warnings": [],
    }

    try:
        config_result = yield AppReadRequest({"m": "g", "t": "CFG"})
        config = _app_action_data(config_result)
        if not isinstance(config, Mapping):
            weather_result = yield from read_device_settings(include_raw=True)
            weather_raw_config = (
                weather_result.get("raw_config")
                if isinstance(weather_result, Mapping)
                else None
            )
            weather_data = _app_action_data(weather_raw_config)
            if isinstance(weather_data, Mapping):
                config_result = weather_raw_config
                config = weather_data
        if include_raw:
            result["raw_config"] = _json_safe(config_result, max_depth=4)
        if not isinstance(config, Mapping):
            raise DreameLawnMowerConnectionError(
                f"CFG returned invalid voice config: {config_result}"
            )
        result["present_config_keys"] = [
            key for key in result["config_keys"] if key in config
        ]
        result.update(_voice_settings_summary(config))
        result["available"] = bool(result["present_config_keys"])
    except Exception as err:  # noqa: BLE001 - diagnostic probe should return evidence
        result["errors"].append({"stage": "config", "error": str(err)})

    return result
