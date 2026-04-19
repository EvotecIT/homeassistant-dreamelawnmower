"""Small helpers derived from Dreamehome app protocol assets."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Final

from .models import DreameLawnMowerStatusBlob

MOWER_RAW_STATUS_PROPERTY_KEY: Final[str] = "1.1"
MOWER_RUNTIME_STATUS_PROPERTY_KEY: Final[str] = "1.4"
MOWER_STATE_PROPERTY_KEY: Final[str] = "2.1"
MOWER_ERROR_PROPERTY_KEY: Final[str] = "2.2"
MOWER_PROPERTY_HINTS: Final[dict[str, str]] = {
    MOWER_RAW_STATUS_PROPERTY_KEY: "raw_status_blob",
    MOWER_RUNTIME_STATUS_PROPERTY_KEY: "runtime_status_blob",
    MOWER_STATE_PROPERTY_KEY: "mower_state",
    MOWER_ERROR_PROPERTY_KEY: "mower_error",
}
MOWER_STATE_LABELS: Final[dict[str, dict[str, str]]] = {
    "en": {
        "1": "Working",
        "2": "Standby",
        "3": "Working",
        "4": "Paused",
        "5": "Returning Charge",
        "6": "Charging",
        "11": "Mapping",
        "13": "Charging Completed",
        "14": "Upgrading",
    },
    "zh": {
        "1": "割草中",
        "2": "待机中",
        "3": "暂停",
        "4": "暂停",
        "5": "正在回充",
        "6": "正在充电",
        "11": "正在建图",
        "13": "充电完成",
        "14": "正在升级",
    },
}


def _clean_label(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    text = text.replace("_", " ").replace("wheell", "wheel")
    return text.capitalize()


def mower_state_label(value: object, language: str = "en") -> str | None:
    """Return the app-derived mower state label for a raw `2.1` value."""
    if value is None:
        return None

    label_map = MOWER_STATE_LABELS.get(language) or MOWER_STATE_LABELS["en"]
    return label_map.get(str(value))


def mower_error_label(value: object) -> str | None:
    """Return a cleaned mower error label for a raw `2.2` value."""
    if value is None:
        return None

    try:
        code = int(value)
    except (TypeError, ValueError):
        return None
    if code in (-1, 0):
        return "No error"

    try:
        from .const import ERROR_CODE_TO_ERROR_NAME
        from .types import DreameMowerErrorCode

        enum_value = DreameMowerErrorCode(code)
        return _clean_label(ERROR_CODE_TO_ERROR_NAME.get(enum_value))
    except (ImportError, ValueError):
        return None


def key_definition_label(
    key_definition: Mapping[str, object] | None,
    key: object,
    value: object,
    *,
    language: str = "en",
) -> str | None:
    """Return a label from Dreame's public `keyDefine` JSON, if present."""
    if not isinstance(key_definition, Mapping) or value is None:
        return None

    definition = key_definition.get("payload")
    if not isinstance(definition, Mapping):
        definition = key_definition

    key_define = definition.get("keyDefine")
    if not isinstance(key_define, Mapping):
        return None

    key_labels = key_define.get(str(key))
    if not isinstance(key_labels, Mapping):
        return None

    language_labels = key_labels.get(language) or key_labels.get("en")
    if not isinstance(language_labels, Mapping):
        language_labels = _first_mapping_value(key_labels)
    if not isinstance(language_labels, Mapping):
        return None

    label = language_labels.get(str(value))
    return str(label).strip() if label is not None and str(label).strip() else None


def _first_mapping_value(value: Mapping[object, object]) -> Mapping[object, object] | None:
    for item in value.values():
        if isinstance(item, Mapping):
            return item
    return None


def decode_mower_status_blob(
    value: object,
    *,
    source: str | None = None,
) -> DreameLawnMowerStatusBlob | None:
    """Return a conservative structure for the app realtime `1.1` byte blob.

    The Dreamehome app exposes this as a framed byte array. We preserve indexed
    bytes for cross-device comparison, but intentionally avoid assigning
    semantics until we can prove them from multiple mower states/models.
    """
    raw = _normalize_byte_array(value)
    if raw is None:
        return None

    notes: list[str] = []
    if len(raw) != 20:
        notes.append("unexpected_length")

    frame_start = raw[0] if raw else None
    frame_end = raw[-1] if raw else None
    frame_valid = bool(len(raw) >= 2 and frame_start == 0xCE and frame_end == 0xCE)
    if not frame_valid:
        notes.append("invalid_frame_markers")

    candidate_battery_level = None
    if frame_valid and len(raw) > 11 and raw[11] <= 100:
        candidate_battery_level = raw[11]

    return DreameLawnMowerStatusBlob(
        supported=True,
        source=source,
        raw=raw,
        length=len(raw),
        hex=bytes(raw).hex(),
        frame_start=frame_start,
        frame_end=frame_end,
        frame_valid=frame_valid,
        payload=raw[1:-1] if len(raw) >= 2 else (),
        bytes_by_index={str(index): item for index, item in enumerate(raw)},
        candidate_battery_level=candidate_battery_level,
        notes=tuple(notes),
    )


def _normalize_byte_array(value: object) -> tuple[int, ...] | None:
    raw = value
    if isinstance(raw, str):
        text = raw.strip()
        if not (text.startswith("[") and text.endswith("]")):
            return None
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return None

    if isinstance(raw, bytes | bytearray):
        return tuple(raw)

    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes | bytearray):
        return None
    if not raw:
        return None
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in raw):
        return None
    if not all(0 <= item <= 255 for item in raw):
        return None
    return tuple(raw)
