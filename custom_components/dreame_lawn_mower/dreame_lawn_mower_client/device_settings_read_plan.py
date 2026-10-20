"""Shared read-only device settings plans for sync and native async transports."""

from __future__ import annotations

import time
from collections.abc import Generator, Mapping
from typing import Any

from .app_read_transport import AppReadRequest
from .client_map_helpers import _current_app_map_index
from .client_settings_helpers import (
    _voice_settings_summary,
    _weather_protection_active_summary,
)
from .client_shared_helpers import _app_action_data
from .device_settings import decode_device_settings
from .exceptions import DreameLawnMowerConnectionError
from .maintenance import CMS_GET_REQUEST, maintenance_status_from_app_data
from .model_families import dreame_model_family
from .mowing_preferences_read_plan import read_mowing_preferences
from .payload_utils import _json_safe


def _settings_request(action: Mapping[str, Any]) -> AppReadRequest:
    """Retain the legacy settings read policy, including setter confirmations."""
    return AppReadRequest(action, retry_count=2, timeout=20.0)


def read_device_settings(
    include_raw: bool = False,
    include_rain_end_time: bool = True,
) -> Generator[AppReadRequest, Any, dict[str, Any]]:
    result: dict[str, Any] = {
        "source": "app_action_device_settings",
        "available": False,
        "config_keys": ["ATA", "BAT", "DND", "WRF", "WRP"],
        "fault_hint": "INFO_BAD_WEATHER_PROTECTING",
        "rain_end_time_command": "RPET",
        "errors": [],
        "warnings": [],
    }
    try:
        config_result = yield _settings_request({"m": "g", "t": "CFG"})
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
            rain_end_result = yield _settings_request({"m": "g", "t": "RPET"})
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
            result["warnings"].append({"stage": "rain_end_time", "warning": str(err)})

    result.update(_weather_protection_active_summary(result))
    return result


def read_maintenance(
    include_raw: bool = False,
    *,
    model: str | None = None,
) -> Generator[AppReadRequest, Any, dict[str, Any]]:
    result: dict[str, Any] = {
        "source": "app_action_maintenance_cms",
        "available": False,
        "items": [],
        "raw_cms": None,
        "errors": [],
    }

    try:
        cms_result = yield _settings_request(CMS_GET_REQUEST)
        if include_raw:
            result["raw_cms_response"] = _json_safe(cms_result, max_depth=4)
        cms_data = _app_action_data(cms_result)
        result.update(
            maintenance_status_from_app_data(
                cms_data,
                source="app_action_maintenance_cms",
                model=model,
            )
        )
        if result.get("available"):
            return (yield from _read_edge_trimmer_context(result, model))
    except Exception as err:  # noqa: BLE001 - fallback to CFG may still work
        result["errors"].append({"stage": "cms", "error": str(err)})

    try:
        config_result = yield _settings_request({"m": "g", "t": "CFG"})
        if include_raw:
            result["raw_config_response"] = _json_safe(config_result, max_depth=4)
        config = _app_action_data(config_result)
        result.update(
            maintenance_status_from_app_data(
                config,
                source="app_action_config_cms",
                model=model,
            )
        )
    except Exception as err:  # noqa: BLE001 - diagnostic probe returns evidence
        result["errors"].append({"stage": "config", "error": str(err)})
    return (yield from _read_edge_trimmer_context(result, model))


def _read_edge_trimmer_context(
    result: dict[str, Any],
    model: str | None,
) -> Generator[AppReadRequest, Any, dict[str, Any]]:
    """Bound optional installation proof; failed context never hides valid CMS."""
    counters = result.get("raw_cms")
    if (
        dreame_model_family(model) != "q2501"
        or not isinstance(counters, list)
        or len(counters) <= 3
        or type(counters[3]) is not int
        or counters[3] < 0
    ):
        return result
    deadline = time.monotonic() + 5.0
    try:
        inventory = yield AppReadRequest(
            {"m": "g", "t": "MAPL"},
            retry_count=0,
            timeout=5.0,
            deadline=deadline,
        )
        index = _current_app_map_index(inventory)
        if index is None:
            return result
        preferences = yield from read_mowing_preferences(
            map_indices=[index],
            deadline=deadline,
        )
        maps = preferences.get("maps", [])
        entry = next((item for item in maps if item.get("idx") == index), None)
        if not isinstance(entry, Mapping) or not entry.get("available"):
            return result
        # Custom mode advertises zones rather than area 0. Each surviving PRE
        # record already passed identity and revision checks in the shared reader.
        # An installed mode in any current zone establishes the device attachment;
        # unavailable/unknown siblings must not replace that positive evidence.
        records = [
            item
            for item in entry.get("preferences", [])
            if isinstance(item, Mapping)
            and (
                (entry.get("mode") == 0 and item.get("area_id") == 0)
                or (
                    entry.get("mode") == 1
                    and type(item.get("area_id")) is int
                    and item["area_id"] > 0
                )
            )
        ]
        modes = [item.get("edge_cutting_attachment") for item in records]
        mode = next(
            (
                candidate
                for candidate in (2, 1)
                if any(type(value) is int and value == candidate for value in modes)
            ),
            None,
        )
        if (
            mode is None
            and modes
            and not entry.get("errors")
            and all(type(value) is int and value == 0 for value in modes)
        ):
            mode = 0
        if type(mode) is int and mode in (0, 1, 2):
            result.update(
                maintenance_status_from_app_data(
                    {"value": counters},
                    source=result["source"],
                    model=model,
                    edge_trimmer_mode=mode,
                )
            )
    except Exception as error:
        result["errors"].append({"stage": "edge_trimmer_context", "error": str(error)})
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
        config_result = yield _settings_request({"m": "g", "t": "CFG"})
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
