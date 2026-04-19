"""Helpers for Dreame mower batch device-data payloads."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .mowing_preferences import (
    CUTTER_POSITION_NAMES,
    EDGE_MOWING_WALK_MODE_NAMES,
    MOWING_DIRECTION_MODE_NAMES,
    MOWING_EFFICIENCY_MODE_NAMES,
    MOWING_PREFERENCE_MODE_NAMES,
    OBSTACLE_AI_CLASSES,
)
from .schedule import EMPTY_SCHEDULE_VERSION, decode_schedule_payload_text

_DEFAULT_BATCH_CHUNK_COUNT = 10


def decode_batch_schedule_payload(
    batch_data: Mapping[str, Any],
    *,
    include_raw: bool = False,
    map_index_hint: int | None = None,
) -> dict[str, Any]:
    """Decode `SCHEDULE.*` batch device data into schedule summaries."""
    result: dict[str, Any] = {
        "source": "batch_device_data_schedule",
        "available": False,
        "current_task": None,
        "schedules": [],
        "errors": [],
    }

    payload_text = batch_data_text(batch_data, "SCHEDULE")
    schedule: dict[str, Any] = {
        "idx": map_index_hint,
        "label": (
            "active_map"
            if map_index_hint is None
            else f"map_{int(map_index_hint)}"
        ),
        "available": False,
    }
    info_value = batch_data.get("SCHEDULE.info")
    if info_value is not None:
        schedule["reported_size"] = _to_int(_batch_scalar_value(info_value))

    if not payload_text:
        schedule["error"] = "missing_batch_schedule_payload"
        result["errors"].append({"stage": "schedule", "error": schedule["error"]})
        result["schedules"].append(schedule)
        return result

    try:
        payload = json.loads(payload_text)
        version = _to_int(payload.get("v")) if isinstance(payload, Mapping) else None
        plans = decode_schedule_payload_text(payload_text)
        schedule.update(
            {
                "version": version,
                "available": bool(plans),
                "plan_count": len(plans),
                "enabled_plan_count": sum(
                    1
                    for plan in plans
                    if isinstance(plan, Mapping) and plan.get("enabled")
                ),
                "plans": plans,
            }
        )
        if include_raw:
            schedule["raw_text"] = payload_text
        if version == EMPTY_SCHEDULE_VERSION:
            schedule["available"] = False
        result["available"] = schedule["available"]
    except Exception as err:  # noqa: BLE001 - batch probes keep evidence
        schedule["error"] = str(err)
        result["errors"].append({"stage": "schedule", "error": str(err)})

    result["schedules"].append(schedule)
    return result


def decode_batch_mowing_preferences(
    batch_data: Mapping[str, Any],
    *,
    include_raw: bool = False,
    map_indices: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Decode `SETTINGS.*` batch device data into readable preference summaries."""
    result: dict[str, Any] = {
        "source": "batch_device_data_mowing_preferences",
        "available": False,
        "property_hint": "2.52",
        "maps": [],
        "errors": [],
    }

    payload_text = batch_data_text(batch_data, "SETTINGS")
    if not payload_text:
        result["errors"].append(
            {"stage": "settings", "error": "missing_batch_settings_payload"}
        )
        return result

    try:
        payload = json.loads(payload_text)
    except Exception as err:  # noqa: BLE001 - preserve probe evidence
        result["errors"].append({"stage": "settings", "error": str(err)})
        return result

    if not isinstance(payload, Sequence) or isinstance(
        payload, (str, bytes, bytearray)
    ):
        result["errors"].append(
            {"stage": "settings", "error": "invalid_batch_settings_payload"}
        )
        return result

    selected_indices = (
        {int(index) for index in map_indices}
        if map_indices is not None
        else None
    )
    for map_index, map_entry in enumerate(payload):
        if selected_indices is not None and map_index not in selected_indices:
            continue
        entry = _decode_batch_preference_map_entry(
            map_entry,
            map_index=map_index,
            include_raw=include_raw,
        )
        if entry.get("available"):
            result["available"] = True
        result["maps"].append(entry)

    return result


def decode_batch_ota_info(
    batch_data: Mapping[str, Any],
    *,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Decode `OTA_INFO.*` and related auto-upgrade batch data."""
    result: dict[str, Any] = {
        "source": "batch_device_data_ota_info",
        "available": False,
        "ota_info": None,
        "update_available": None,
        "auto_upgrade_enabled": None,
        "errors": [],
    }

    payload_text = batch_data_text(batch_data, "OTA_INFO")
    if payload_text:
        try:
            ota_info = json.loads(payload_text)
            result["ota_info"] = ota_info
            if isinstance(ota_info, Sequence) and not isinstance(
                ota_info, (str, bytes, bytearray)
            ):
                values = list(ota_info)
                result["update_available"] = (
                    bool(_to_int(values[0])) if values else None
                )
                if len(values) > 1:
                    result["ota_status"] = _to_int(values[1])
            result["available"] = True
            if include_raw:
                result["raw_text"] = payload_text
        except Exception as err:  # noqa: BLE001 - preserve probe evidence
            result["errors"].append({"stage": "ota", "error": str(err)})

    auto_upgrade = _to_int(_batch_scalar_value(batch_data.get("prop.s_auto_upgrade")))
    if auto_upgrade is not None:
        result["auto_upgrade_enabled"] = bool(auto_upgrade)
        result["available"] = True

    return result


def batch_data_text(
    batch_data: Mapping[str, Any],
    prefix: str,
    *,
    max_chunk_count: int = _DEFAULT_BATCH_CHUNK_COUNT,
) -> str | None:
    """Return a concatenated batch text payload such as `SETTINGS.*`."""
    size = _batch_text_size(batch_data, prefix)
    parts: list[str] = []
    for index in range(max_chunk_count):
        key = f"{prefix}.{index}"
        if key not in batch_data:
            if parts:
                break
            continue
        part = _batch_text_part(batch_data[key])
        if part is None:
            if parts:
                break
            continue
        parts.append(part)
    if not parts:
        return None
    text = "".join(parts)
    if size is not None:
        text = text[:size]
    return text


def _decode_batch_preference_map_entry(
    value: Any,
    *,
    map_index: int,
    include_raw: bool,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "idx": map_index,
        "label": f"map_{map_index}",
        "available": False,
        "preferences": [],
    }
    if not isinstance(value, Mapping):
        entry["error"] = "invalid_batch_settings_map_entry"
        return entry

    mode = _to_int(value.get("mode"))
    settings = value.get("settings")
    entry["mode"] = mode
    entry["mode_name"] = _label(MOWING_PREFERENCE_MODE_NAMES, mode)

    preferences: list[dict[str, Any]] = []
    if isinstance(settings, Mapping):
        for area_key, raw_preference in settings.items():
            if not isinstance(raw_preference, Mapping):
                continue
            area_id = _to_int(raw_preference.get("id"))
            if area_id is None:
                area_id = _to_int(area_key)
            if area_id is None:
                continue
            preference = {
                "version": _to_int(raw_preference.get("version")),
                "reported_version": _to_int(raw_preference.get("version")),
                "map_index": map_index,
                "area_id": area_id,
                "efficient_mode": _to_int(raw_preference.get("efficientMode")),
                "efficient_mode_name": _label(
                    MOWING_EFFICIENCY_MODE_NAMES,
                    _to_int(raw_preference.get("efficientMode")),
                ),
                "mowing_height_cm": _float_or_none(raw_preference.get("mowingHeight")),
                "mowing_direction_mode": _to_int(
                    raw_preference.get("mowingDirectionMode")
                ),
                "mowing_direction_mode_name": _label(
                    MOWING_DIRECTION_MODE_NAMES,
                    _to_int(raw_preference.get("mowingDirectionMode")),
                ),
                "mowing_direction_degrees": _to_int(
                    raw_preference.get("mowingDirection")
                ),
                "edge_mowing_auto": _bool_or_none(raw_preference.get("edgeMowingAuto")),
                "edge_mowing_walk_mode": _to_int(
                    raw_preference.get("edgeMowingWalkMode")
                ),
                "edge_mowing_walk_mode_name": _label(
                    EDGE_MOWING_WALK_MODE_NAMES,
                    _to_int(raw_preference.get("edgeMowingWalkMode")),
                ),
                "edge_mowing_obstacle_avoidance": _bool_or_none(
                    raw_preference.get("edgeMowingObstacleAvoidance")
                ),
                "cutter_position": _to_int(raw_preference.get("cutterPosition")),
                "cutter_position_name": _label(
                    CUTTER_POSITION_NAMES,
                    _to_int(raw_preference.get("cutterPosition")),
                ),
                "edge_mowing_num": _to_int(raw_preference.get("edgeMowingNum")),
                "obstacle_avoidance_enabled": _bool_or_none(
                    raw_preference.get("obstacleAvoidanceEnabled")
                ),
                "obstacle_avoidance_height_cm": _float_or_none(
                    raw_preference.get("obstacleAvoidanceHeight")
                ),
                "obstacle_avoidance_distance_cm": _float_or_none(
                    raw_preference.get("obstacleAvoidanceDistance")
                ),
                "obstacle_avoidance_ai": _to_int(
                    raw_preference.get("obstacleAvoidanceAi")
                ),
                "obstacle_avoidance_ai_classes": _ai_class_names(
                    _to_int(raw_preference.get("obstacleAvoidanceAi"))
                ),
                "edge_mowing_safe": _bool_or_none(
                    raw_preference.get("edgeMowingSafe"),
                    default=True,
                ),
            }
            if include_raw:
                preference["raw_setting"] = dict(raw_preference)
            preferences.append(preference)

    entry["preferences"] = preferences
    entry["area_count"] = len(preferences)
    entry["available"] = bool(preferences)
    if include_raw:
        entry["raw_settings"] = (
            dict(settings) if isinstance(settings, Mapping) else settings
        )
    return entry


def _batch_text_size(batch_data: Mapping[str, Any], prefix: str) -> int | None:
    return _to_int(_batch_scalar_value(batch_data.get(f"{prefix}.info")))


def _batch_scalar_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        for key in ("value", "data", "content"):
            if key in value:
                return value.get(key)
    return value


def _batch_text_part(value: Any) -> str | None:
    scalar = _batch_scalar_value(value)
    if scalar is None:
        return None
    return str(scalar)


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any, *, default: bool | None = None) -> bool | None:
    integer = _to_int(value)
    if integer is None:
        return default
    return bool(integer)


def _label(labels: Mapping[int, str], value: int | None) -> str:
    if value is None:
        return "unknown"
    return labels.get(value, f"unknown_{value}")


def _ai_class_names(mask: int | None) -> list[str]:
    if mask is None:
        return []
    return [name for bit, name in OBSTACLE_AI_CLASSES if mask & bit]
