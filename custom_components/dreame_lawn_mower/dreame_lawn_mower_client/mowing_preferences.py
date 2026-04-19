"""Helpers for Dreame app-action mowing preference payloads."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

MOWING_PREFERENCE_PROPERTY_KEY = "2.52"

MOWING_PREFERENCE_MODE_NAMES = {
    0: "global",
    1: "custom",
}

MOWING_DIRECTION_MODE_NAMES = {
    0: "none",
    1: "rotation",
    2: "checkerboard",
}

MOWING_EFFICIENCY_MODE_NAMES = {
    0: "standard",
    1: "efficient",
}

EDGE_MOWING_WALK_MODE_NAMES = {
    0: "line",
    1: "side",
}

CUTTER_POSITION_NAMES = {
    0: "center",
    1: "left",
}

OBSTACLE_AI_CLASSES = (
    (1, "people"),
    (2, "animals"),
    (4, "objects"),
)


def decode_mowing_preference_payload(payload: Sequence[Any]) -> dict[str, Any]:
    """Decode one app `PRE` payload into named mower preference fields."""
    values = list(payload)
    obstacle_ai_mask = _int_at(values, 15)
    preference = {
        "version": _int_at(values, 0),
        "map_index": _int_at(values, 1),
        "area_id": _int_at(values, 2),
        "efficient_mode": _int_at(values, 3),
        "efficient_mode_name": _label(
            MOWING_EFFICIENCY_MODE_NAMES,
            _int_at(values, 3),
        ),
        "mowing_height_cm": _height_at(values, 4),
        "mowing_direction_mode": _int_at(values, 5),
        "mowing_direction_mode_name": _label(
            MOWING_DIRECTION_MODE_NAMES,
            _int_at(values, 5),
        ),
        "mowing_direction_degrees": _mowing_direction_at(values, 6),
        "edge_mowing_auto": _bool_at(values, 7),
        "edge_mowing_walk_mode": _int_at(values, 8),
        "edge_mowing_walk_mode_name": _label(
            EDGE_MOWING_WALK_MODE_NAMES,
            _int_at(values, 8),
        ),
        "edge_mowing_obstacle_avoidance": _bool_at(values, 9),
        "cutter_position": _int_at(values, 10),
        "cutter_position_name": _label(
            CUTTER_POSITION_NAMES,
            _int_at(values, 10),
        ),
        "edge_mowing_num": _int_at(values, 11),
        "obstacle_avoidance_enabled": _bool_at(values, 12),
        "obstacle_avoidance_height_cm": _int_at(values, 13),
        "obstacle_avoidance_distance_cm": _int_at(values, 14),
        "obstacle_avoidance_ai": obstacle_ai_mask,
        "obstacle_avoidance_ai_classes": _ai_class_names(obstacle_ai_mask),
        "edge_mowing_safe": _bool_at(values, 16, default=True),
    }
    return preference


def summarize_mowing_preference_info(info: Any) -> dict[str, Any]:
    """Return a compact summary of an app `PREI` response data object."""
    if not isinstance(info, dict):
        return {"valid": False, "value_type": type(info).__name__}

    mode = _to_int(info.get("type"))
    versions = _preference_version_entries(info.get("ver"))
    return {
        "valid": True,
        "mode": mode,
        "mode_name": _label(MOWING_PREFERENCE_MODE_NAMES, mode),
        "area_count": len(versions),
        "areas": versions,
    }


def _preference_version_entries(value: Any) -> list[dict[str, int | None]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    entries: list[dict[str, int | None]] = []
    for item in value:
        if not isinstance(item, Sequence) or isinstance(item, str | bytes | bytearray):
            continue
        values = list(item)
        if len(values) < 2:
            continue
        entries.append({"area_id": _to_int(values[0]), "version": _to_int(values[1])})
    return entries


def _ai_class_names(mask: int | None) -> list[str]:
    if mask is None:
        return []
    return [name for bit, name in OBSTACLE_AI_CLASSES if mask & bit]


def _height_at(values: Sequence[Any], index: int) -> float | None:
    value = _to_int(values[index]) if len(values) > index else None
    if value is None:
        return None
    return value / 10


def _mowing_direction_at(values: Sequence[Any], index: int) -> int | None:
    value = _to_int(values[index]) if len(values) > index else None
    if value is None:
        return None
    return 180 - value


def _int_at(values: Sequence[Any], index: int) -> int | None:
    return _to_int(values[index]) if len(values) > index else None


def _bool_at(
    values: Sequence[Any],
    index: int,
    *,
    default: bool | None = None,
) -> bool | None:
    if len(values) <= index:
        return default
    value = _to_int(values[index])
    return bool(value) if value is not None else default


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _label(labels: dict[int, str], value: int | None) -> str:
    if value is None:
        return "unknown"
    return labels.get(value, f"unknown_{value}")
