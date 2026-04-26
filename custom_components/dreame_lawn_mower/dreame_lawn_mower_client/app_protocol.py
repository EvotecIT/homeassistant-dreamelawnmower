"""Small helpers derived from Dreamehome app protocol assets."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Final

from .models import DreameLawnMowerStatusBlob

MOWER_RAW_STATUS_PROPERTY_KEY: Final[str] = "1.1"
MOWER_RUNTIME_STATUS_PROPERTY_KEY: Final[str] = "1.4"
MOWER_BLUETOOTH_PROPERTY_KEY: Final[str] = "1.53"
MOWER_STATE_PROPERTY_KEY: Final[str] = "2.1"
MOWER_ERROR_PROPERTY_KEY: Final[str] = "2.2"
MOWER_TASK_PROPERTY_KEY: Final[str] = "2.50"
MOWER_TIME_PROPERTY_KEY: Final[str] = "2.51"
MOWER_BATTERY_PROPERTY_KEY: Final[str] = "3.1"
MOWER_PROPERTY_HINTS: Final[dict[str, str]] = {
    MOWER_RAW_STATUS_PROPERTY_KEY: "raw_status_blob",
    MOWER_RUNTIME_STATUS_PROPERTY_KEY: "runtime_status_blob",
    MOWER_BLUETOOTH_PROPERTY_KEY: "bluetooth_connected",
    MOWER_STATE_PROPERTY_KEY: "mower_state",
    MOWER_ERROR_PROPERTY_KEY: "mower_error",
    MOWER_TASK_PROPERTY_KEY: "task_status",
    MOWER_TIME_PROPERTY_KEY: "device_time",
    MOWER_BATTERY_PROPERTY_KEY: "battery_level",
}
MOWER_STATE_LABELS: Final[dict[str, dict[str, str]]] = {
    "en": {
        "1": "Mowing",
        "2": "Standby",
        "3": "Paused",
        "4": "Paused due to errors",
        "5": "Returning Charge",
        "6": "Charging",
        "11": "Mapping",
        "13": "Charging Completed",
        "14": "Upgrading",
        "15": "Charging paused: battery temperature is too high",
        "16": "Charging paused: battery temperature is too low",
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
MOWER_STATE_KEYS: Final[dict[str, str]] = {
    "1": "mowing",
    "2": "standby",
    "3": "paused",
    "4": "paused",
    "5": "returning",
    "6": "charging",
    "11": "mapping",
    "13": "charging_completed",
    "14": "upgrading",
    "15": "charging_paused_high_temperature",
    "16": "charging_paused_low_temperature",
}


def mower_property_hint(key: object) -> str | None:
    """Return the app-derived property hint for a raw siid/piid key."""
    return MOWER_PROPERTY_HINTS.get(str(key))


def mower_realtime_property_name(key: object, property_name: object = None) -> str:
    """Return a stable realtime property name, preferring known app hints."""
    key_text = str(key)
    hint = mower_property_hint(key_text)
    text = str(property_name).strip() if property_name is not None else ""
    if hint and (not text or text.startswith("UNKNOWN_REALTIME_")):
        return hint
    return text or f"UNKNOWN_REALTIME_{key_text}"


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


def mower_state_key(value: object) -> str | None:
    """Return a stable app-derived mower state key for a raw `2.1` value."""
    if value is None:
        return None

    return MOWER_STATE_KEYS.get(str(value))


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


def decode_mower_task_status(value: object) -> dict[str, object] | None:
    """Return a compact structure for app task-status property `2.50`."""
    raw = value
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, Mapping):
        return None

    data = raw.get("d")
    if not isinstance(data, Mapping):
        data = {}

    result: dict[str, object] = {}
    task_type = raw.get("t")
    if task_type is not None:
        result["type"] = str(task_type)
    if "exe" in data:
        result["executing"] = bool(data.get("exe"))
    if "status" in data:
        result["status"] = bool(data.get("status"))
    if "o" in data:
        result["operation"] = data.get("o")
    return result or None


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


def _first_mapping_value(
    value: Mapping[object, object],
) -> Mapping[object, object] | None:
    for item in value.values():
        if isinstance(item, Mapping):
            return item
    return None


def decode_mower_status_blob(
    value: object,
    *,
    source: str | None = None,
) -> DreameLawnMowerStatusBlob | None:
    """Return a conservative structure for app realtime/status byte blobs.

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

    runtime_telemetry = _decode_runtime_blob_candidates(raw, frame_valid=frame_valid)
    notes.extend(runtime_telemetry.pop("notes", ()))

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
        candidate_runtime_region_id=runtime_telemetry.get("region_id"),
        candidate_runtime_task_id=runtime_telemetry.get("task_id"),
        candidate_runtime_progress_percent=runtime_telemetry.get("progress_percent"),
        candidate_runtime_area_progress_percent=runtime_telemetry.get(
            "area_progress_percent"
        ),
        candidate_runtime_current_area_sqm=runtime_telemetry.get(
            "current_area_sqm"
        ),
        candidate_runtime_total_area_sqm=runtime_telemetry.get("total_area_sqm"),
        candidate_runtime_pose_x=runtime_telemetry.get("pose_x"),
        candidate_runtime_pose_y=runtime_telemetry.get("pose_y"),
        candidate_runtime_heading_deg=runtime_telemetry.get("heading_deg"),
        candidate_runtime_track_segments=runtime_telemetry.get("track_segments", ()),
        notes=tuple(notes),
    )


def _decode_runtime_blob_candidates(
    raw: Sequence[int],
    *,
    frame_valid: bool,
) -> dict[str, object]:
    """Return conservative pose/progress hints from runtime-status payloads."""
    result: dict[str, object] = {"notes": []}
    if not frame_valid:
        return result

    pose = _decode_runtime_pose(raw)
    if pose is not None:
        result.update(pose)

    track = _decode_runtime_track_segments(raw, pose)
    if track is not None:
        result.update(track)

    task = _decode_runtime_task_block(raw)
    if task is not None:
        result.update(task)

    return result


def _decode_runtime_pose(raw: Sequence[int]) -> dict[str, object] | None:
    """Decode the 6-byte overlapping 20-bit pose used in app runtime payloads."""
    if len(raw) not in (13, 22, 33, 44) or raw[0] != 0xCE:
        return None

    b0 = raw[1]
    b1 = raw[2]
    b2 = raw[3]
    b3 = raw[4]
    b4 = raw[5]
    b5 = raw[6]

    raw_x = ((b2 << 28) | (b1 << 20) | (b0 << 12)) & 0xFFFFFFFF
    if raw_x & 0x80000000:
        raw_x -= 0x100000000
    pose_x = raw_x >> 12

    raw_y = ((b4 << 24) | (b3 << 16) | (b2 << 8)) & 0xFFFFFFFF
    if raw_y & 0x80000000:
        raw_y -= 0x100000000
    pose_y = raw_y >> 12

    return {
        "pose_x": int(pose_x * 10),
        "pose_y": int(pose_y * 10),
        "heading_deg": round((b5 / 255.0) * 360.0, 1),
    }


def _decode_runtime_task_block(raw: Sequence[int]) -> dict[str, object] | None:
    """Decode the 10-byte mission/progress block from framed runtime payloads."""
    if len(raw) not in (33, 44):
        return None
    if raw[0] != 0xCE or raw[-1] != 0xCE:
        return None

    offset = 22
    region_id = raw[offset]
    task_id = raw[offset + 1]
    raw_percent = raw[offset + 2] | (raw[offset + 3] << 8)
    total_area_raw = raw[offset + 4] | (raw[offset + 5] << 8) | (raw[offset + 6] << 16)
    current_area_raw = raw[offset + 7] | (raw[offset + 8] << 8) | (raw[offset + 9] << 16)

    total_area_sqm = round(total_area_raw / 100.0, 2) if total_area_raw else 0.0
    current_area_sqm = round(current_area_raw / 100.0, 2) if current_area_raw else 0.0

    progress_percent = None
    notes: list[str] = []
    if 0 <= raw_percent <= 1000:
        progress_percent = round(raw_percent / 10.0, 1)
    elif raw_percent:
        notes.append("unexpected_runtime_progress_value")

    area_progress_percent = None
    if total_area_sqm > 0 and 0.0 <= current_area_sqm <= total_area_sqm:
        area_progress_percent = round((current_area_sqm / total_area_sqm) * 100.0, 1)

    return {
        "region_id": region_id,
        "task_id": task_id,
        "progress_percent": progress_percent,
        "area_progress_percent": area_progress_percent,
        "current_area_sqm": current_area_sqm,
        "total_area_sqm": total_area_sqm,
        "notes": tuple(notes),
    }


def _decode_runtime_track_segments(
    raw: Sequence[int],
    pose: Mapping[str, object] | None,
) -> dict[str, object] | None:
    """Decode runtime trace chunks into map-unit coordinate segments."""
    if not pose:
        return None

    base_x = pose.get("pose_x")
    base_y = pose.get("pose_y")
    if not isinstance(base_x, int) or not isinstance(base_y, int):
        return None

    chunks: list[tuple[int, int]] = []
    if len(raw) == 22 and raw[0] == 0xCE and raw[-1] == 0xCE:
        chunks.append((7, 15))
    elif len(raw) == 33 and raw[0] == 0xCE and raw[-1] == 0xCE:
        chunks.append((7, 15))
    elif len(raw) == 44 and raw[0] == 0xCE and raw[-1] == 0xCE:
        chunks.append((7, 15))
        chunks.append((32, 11))
    else:
        return None

    segments: list[tuple[tuple[int, int], ...]] = []
    for offset, length in chunks:
        segments.extend(_decode_runtime_track_chunk(raw, offset, length, base_x, base_y))

    if not segments:
        return None
    return {"track_segments": tuple(segments)}


def _decode_runtime_track_chunk(
    raw: Sequence[int],
    offset: int,
    length: int,
    base_x: int,
    base_y: int,
) -> list[tuple[tuple[int, int], ...]]:
    """Decode one runtime trace chunk into contiguous point segments."""
    if len(raw) < offset + length or length < 7:
        return []

    segments: list[list[tuple[int, int]]] = []
    current_segment: list[tuple[int, int]] = []
    pair_count = (length - 3) // 4
    pair_offset = offset + 3

    for _ in range(pair_count):
        if pair_offset + 4 > len(raw):
            break
        dx = _signed_int16(raw[pair_offset], raw[pair_offset + 1])
        dy = _signed_int16(raw[pair_offset + 2], raw[pair_offset + 3])
        pair_offset += 4

        if abs(dx) > 32766 and abs(dy) > 32766:
            if current_segment:
                segments.append(current_segment)
                current_segment = []
            continue

        current_segment.append((base_x + dx * 10, base_y + dy * 10))

    if current_segment:
        segments.append(current_segment)

    return [tuple(segment) for segment in segments if segment]


def _signed_int16(low: int, high: int) -> int:
    value = low | (high << 8)
    if value & 0x8000:
        value -= 0x10000
    return value


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
