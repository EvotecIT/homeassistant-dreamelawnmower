"""Helpers for Dreame app-action mowing preference payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MOWING_PREFERENCE_PROPERTY_KEY = "2.52"
MOWING_PREFERENCE_MODE_FIELD = "preference_mode"

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

OBSTACLE_AI_CLASS_BITS = {
    name: bit
    for bit, name in OBSTACLE_AI_CLASSES
}

MOWING_PREFERENCE_UPDATE_FIELDS = (
    "efficient_mode",
    "mowing_height_cm",
    "mowing_direction_mode",
    "mowing_direction_degrees",
    "edge_mowing_auto",
    "edge_mowing_walk_mode",
    "edge_mowing_obstacle_avoidance",
    "cutter_position",
    "edge_mowing_num",
    "obstacle_avoidance_enabled",
    "obstacle_avoidance_height_cm",
    "obstacle_avoidance_distance_cm",
    "obstacle_avoidance_ai",
    "obstacle_avoidance_ai_classes",
    "edge_mowing_safe",
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


def mowing_preference_mode_name(value: Any) -> str | None:
    """Return a normalized mode label when available."""
    return _label(MOWING_PREFERENCE_MODE_NAMES, _to_int(value))


def normalize_mowing_preference_mode(value: Any) -> int:
    """Normalize a mower preference mode value from int or label."""
    if isinstance(value, bool):
        raise ValueError(f"{MOWING_PREFERENCE_MODE_FIELD} must be an integer or label.")
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        for mode, label in MOWING_PREFERENCE_MODE_NAMES.items():
            if normalized == label:
                return mode
        if normalized.isdigit():
            value = normalized
    mode = _to_int(value)
    if mode not in MOWING_PREFERENCE_MODE_NAMES:
        supported = ", ".join(MOWING_PREFERENCE_MODE_NAMES.values())
        raise ValueError(
            f"{MOWING_PREFERENCE_MODE_FIELD} supports only {supported}, 0, or 1."
        )
    return mode


def encode_mowing_preference_payload(preference: Mapping[str, Any]) -> list[int]:
    """Encode a decoded mower preference dict back into the app payload shape."""
    obstacle_ai = _obstacle_ai_mask(preference)
    return [
        _required_int(preference, "version"),
        _required_int(preference, "map_index"),
        _required_int(preference, "area_id"),
        _required_int(preference, "efficient_mode"),
        _encode_height_cm(preference.get("mowing_height_cm")),
        _required_int(preference, "mowing_direction_mode"),
        _encode_mowing_direction(preference.get("mowing_direction_degrees")),
        _encode_bool(preference.get("edge_mowing_auto")),
        _required_int(preference, "edge_mowing_walk_mode"),
        _encode_bool(preference.get("edge_mowing_obstacle_avoidance")),
        _required_int(preference, "cutter_position"),
        _required_int(preference, "edge_mowing_num"),
        _encode_bool(preference.get("obstacle_avoidance_enabled")),
        _required_int(preference, "obstacle_avoidance_height_cm"),
        _required_int(preference, "obstacle_avoidance_distance_cm"),
        obstacle_ai,
        _encode_bool(preference.get("edge_mowing_safe"), default=True),
    ]


def apply_mowing_preference_changes(
    preference: Mapping[str, Any],
    changes: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Apply validated preference field changes and report changed field names."""
    updated = dict(preference)
    changed_fields: list[str] = []
    for field, value in changes.items():
        normalized_field, normalized_value = _normalize_preference_change(field, value)
        previous_value = _current_preference_value(updated, normalized_field)
        if previous_value == normalized_value:
            continue

        changed_fields.append(field)
        if normalized_field == "obstacle_avoidance_ai":
            updated["obstacle_avoidance_ai"] = normalized_value
            updated["obstacle_avoidance_ai_classes"] = _ai_class_names(normalized_value)
            continue

        updated[normalized_field] = normalized_value

    _refresh_preference_labels(updated)
    return updated, changed_fields


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


def _normalized_ai_class_names(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise ValueError("obstacle_avoidance_ai_classes must be a list of strings.")

    names: list[str] = []
    for item in value:
        name = str(item).strip().lower()
        if name not in OBSTACLE_AI_CLASS_BITS:
            raise ValueError(
                "obstacle_avoidance_ai_classes supports only people, animals, "
                "and objects."
            )
        if name not in names:
            names.append(name)
    return [name for _, name in OBSTACLE_AI_CLASSES if name in names]


def _mask_from_ai_classes(value: Any) -> int:
    mask = 0
    for name in _normalized_ai_class_names(value):
        mask |= OBSTACLE_AI_CLASS_BITS[name]
    return mask


def _obstacle_ai_mask(preference: Mapping[str, Any]) -> int:
    if "obstacle_avoidance_ai" in preference:
        return _required_int(preference, "obstacle_avoidance_ai")
    return _mask_from_ai_classes(preference.get("obstacle_avoidance_ai_classes", []))


def _normalize_preference_change(field: str, value: Any) -> tuple[str, Any]:
    if field not in MOWING_PREFERENCE_UPDATE_FIELDS:
        raise ValueError(f"Unsupported mowing preference field: {field}")

    if field == "mowing_height_cm":
        return field, _normalize_height_cm(value)
    if field == "mowing_direction_degrees":
        return field, _normalize_direction_degrees(value)
    if field in {
        "edge_mowing_auto",
        "edge_mowing_obstacle_avoidance",
        "obstacle_avoidance_enabled",
        "edge_mowing_safe",
    }:
        return field, _normalize_bool(field, value)
    if field == "obstacle_avoidance_ai_classes":
        return "obstacle_avoidance_ai", _mask_from_ai_classes(value)
    return field, _normalize_int(field, value)


def _current_preference_value(preference: Mapping[str, Any], field: str) -> Any:
    if field == "obstacle_avoidance_ai":
        return _obstacle_ai_mask(preference)
    return preference.get(field)


def _refresh_preference_labels(preference: dict[str, Any]) -> None:
    preference["efficient_mode_name"] = _label(
        MOWING_EFFICIENCY_MODE_NAMES,
        _to_int(preference.get("efficient_mode")),
    )
    preference["mowing_direction_mode_name"] = _label(
        MOWING_DIRECTION_MODE_NAMES,
        _to_int(preference.get("mowing_direction_mode")),
    )
    preference["edge_mowing_walk_mode_name"] = _label(
        EDGE_MOWING_WALK_MODE_NAMES,
        _to_int(preference.get("edge_mowing_walk_mode")),
    )
    preference["cutter_position_name"] = _label(
        CUTTER_POSITION_NAMES,
        _to_int(preference.get("cutter_position")),
    )
    preference["obstacle_avoidance_ai_classes"] = _ai_class_names(
        _to_int(preference.get("obstacle_avoidance_ai"))
    )


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


def _normalize_height_cm(value: Any) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError) as err:
        raise ValueError("mowing_height_cm must be a number.") from err
    if converted <= 0:
        raise ValueError("mowing_height_cm must be greater than zero.")
    return round(converted, 1)


def _normalize_direction_degrees(value: Any) -> int:
    converted = _normalize_int("mowing_direction_degrees", value)
    if converted < 0 or converted > 180:
        raise ValueError("mowing_direction_degrees must be between 0 and 180.")
    return converted


def _normalize_bool(field: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be a boolean.")


def _normalize_int(field: str, value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer.")
    converted = _to_int(value)
    if converted is None:
        raise ValueError(f"{field} must be an integer.")
    return converted


def _required_int(preference: Mapping[str, Any], field: str) -> int:
    value = _to_int(preference.get(field))
    if value is None:
        raise ValueError(f"{field} must be present and convertible to int.")
    return value


def _encode_height_cm(value: Any) -> int:
    return int(round(_normalize_height_cm(value) * 10))


def _encode_mowing_direction(value: Any) -> int:
    return 180 - _normalize_direction_degrees(value)


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


def _encode_bool(value: Any, *, default: bool | None = None) -> int:
    if value is None:
        if default is None:
            raise ValueError("Boolean preference value is required.")
        return 1 if default else 0
    if not isinstance(value, bool):
        raise ValueError("Boolean preference value must be bool.")
    return 1 if value else 0


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _label(labels: dict[int, str], value: int | None) -> str:
    if value is None:
        return "unknown"
    return labels.get(value, f"unknown_{value}")
