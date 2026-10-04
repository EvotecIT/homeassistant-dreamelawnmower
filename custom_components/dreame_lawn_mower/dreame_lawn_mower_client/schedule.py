"""Helpers for mower-native app schedule payloads."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from typing import Any

SCHEDULE_CHUNK_SIZE = 100
EMPTY_SCHEDULE_VERSION = 0xFFFF

SCHEDULE_TASK_TYPE_NAMES = {
    0: "all_area_mowing",
    1: "area_mowing",
    2: "edge_mowing",
    3: "cruise_mowing",
}
SCHEDULE_WEEKDAY_NAMES = {
    0: "sun",
    1: "mon",
    2: "tue",
    3: "wed",
    4: "thu",
    5: "fri",
    6: "sat",
}

_CYCLIC_TASK_OFFSET = 8
_FRAMED_TASK_START = 0xAA
_FRAMED_TASK_END = 0xED
_FRAMED_TASK_MINIMUM_LENGTH = 7


def schedule_write_block_reason(snapshot: Any) -> str | None:
    """Require a known idle mower with no unfinished task before plan edits."""
    if snapshot is None or getattr(snapshot, "available", None) is not True:
        return "Schedule edits require a fresh, available mower state."
    activity = str(getattr(snapshot, "activity", None) or "").casefold()
    state = str(getattr(snapshot, "state", None) or "").casefold()
    session_active = getattr(snapshot, "mowing_session_active", None)
    resumable = getattr(snapshot, "task_resumable", None)
    if (
        session_active is True
        or resumable is True
        or activity in {"mowing", "paused", "returning"}
        or any(
            getattr(snapshot, flag, False)
            for flag in ("started", "mowing", "paused", "returning")
        )
    ):
        return (
            "Finish the current mower task before editing schedules. "
            "A paused or docked task can still be unfinished."
        )
    if session_active is not False or resumable is not False:
        return "Schedule edits are blocked because the current task state is unknown."
    if activity not in {"docked", "idle"} or state == "repositioning":
        return "The mower must be idle or docked before editing schedules."
    return None


def minute_text(value: int | None) -> str | None:
    """Return HH:MM text for a minute-of-day value."""
    if value is None or value < 0:
        return None
    return f"{value // 60:02d}:{value % 60:02d}"


def decode_schedule_payload_text(payload_text: str) -> list[dict[str, Any]]:
    """Decode an app schedule JSON payload into readable schedule plans."""
    payload = json.loads(payload_text)
    raw_plans = payload.get("d") if isinstance(payload, dict) else None
    if not isinstance(raw_plans, list):
        return []
    return decode_schedule_plans(raw_plans)


def encode_schedule_payload_text(plans: list[Mapping[str, Any]]) -> str:
    """Encode readable schedule plans back into app schedule JSON text."""
    return json.dumps(
        {"d": encode_schedule_plans(plans)},
        ensure_ascii=True,
        separators=(",", ":"),
    )


def decode_schedule_plans(raw_plans: list[Any]) -> list[dict[str, Any]]:
    """Decode raw schedule plans from the Dreame app action protocol."""
    plans: list[dict[str, Any]] = []
    for raw_plan in raw_plans:
        if not isinstance(raw_plan, list):
            continue
        plan: dict[str, Any] = {
            "plan_id": raw_plan[0] if len(raw_plan) > 0 else None,
            "enabled": raw_plan[1] == 1 if len(raw_plan) > 1 else None,
            "name": raw_plan[2] if len(raw_plan) > 2 else None,
            "weeks": [],
        }
        if len(raw_plan) > 3 and raw_plan[3]:
            data = _schedule_task_bytes(str(raw_plan[3]))
            plan["weeks"] = _decode_schedule_task_data(data)
            if data and data[0] == _FRAMED_TASK_START:
                plan["task_payload_format"] = "framed"
        plans.append(plan)
    return plans


def encode_schedule_plans(plans: list[Mapping[str, Any]]) -> list[list[Any]]:
    """Encode readable schedule plans into the app action protocol shape."""
    raw_plans: list[list[Any]] = []
    for plan in plans:
        if plan.get("task_payload_format") == "framed":
            raise ValueError("Full plan upload is unavailable for framed schedules.")
        enabled = 1 if plan.get("enabled") else 0
        raw_plan = [
            _required_int(plan.get("plan_id"), "plan_id"),
            enabled,
            str(plan.get("name") or ""),
        ]
        weeks = plan.get("weeks") or []
        if weeks:
            raw_plan.append(encode_schedule_week_payload(weeks))
        raw_plans.append(raw_plan)
    return raw_plans


def decode_schedule_week_payload(payload: str) -> list[dict[str, Any]]:
    """Decode the base64 per-week schedule task payload used by the app."""
    return _decode_schedule_task_data(_schedule_task_bytes(payload))


def _schedule_task_bytes(payload: str) -> list[int]:
    try:
        return list(base64.b64decode(payload, validate=True))
    except (binascii.Error, ValueError) as err:
        raise ValueError("Schedule task payload is not valid base64.") from err


def _decode_schedule_task_data(data: list[int]) -> list[dict[str, Any]]:
    if data and data[0] == _FRAMED_TASK_START:
        return _decode_framed_schedule_tasks(data)
    index = 0
    tasks_by_week: dict[int, list[dict[str, Any]]] = {}

    while index < len(data):
        if index + 7 > len(data):
            raise ValueError("Schedule task header is truncated.")
        first = data[index]
        encoded_task_type = first & 0x0F
        week_day = first >> 4
        if week_day not in SCHEDULE_WEEKDAY_NAMES:
            raise ValueError("Unsupported schedule task weekday.")
        start = _read_12_bit(data[index + 1], data[index + 2], low_first=True)
        end = _read_12_bit(data[index + 2], data[index + 3], low_first=False)
        _validate_schedule_minute(start, "start")
        _validate_schedule_minute(end, "end")
        real_end = _read_12_bit(data[index + 4], data[index + 5], low_first=True)
        if real_end == 0x0FFF:
            real_end = -1
        element_count = _read_12_bit(data[index + 5], data[index + 6], low_first=False)
        element_start = index + 7
        element_end = element_start + element_count
        if element_end > len(data):
            raise ValueError("Schedule task regions are truncated.")
        raw_elements = data[element_start:element_end]
        index = element_end

        task_type = encoded_task_type % _CYCLIC_TASK_OFFSET
        if task_type in (2, 3) and element_count % 2:
            raise ValueError("Edge/cruise schedule regions must contain pairs.")
        tasks_by_week.setdefault(week_day, []).append(
            {
                "type": task_type,
                "type_name": SCHEDULE_TASK_TYPE_NAMES.get(
                    task_type,
                    f"unknown_{task_type}",
                ),
                "cyclic": encoded_task_type >= _CYCLIC_TASK_OFFSET,
                "start": start,
                "start_time": minute_text(start),
                "end": end,
                "end_time": minute_text(end),
                "real_end": real_end,
                "real_end_time": minute_text(real_end),
                "regions": _decode_regions(task_type, raw_elements),
            }
        )

    return [
        {
            "week_day": week_day,
            "week_day_name": SCHEDULE_WEEKDAY_NAMES.get(week_day, str(week_day)),
            "tasks": tasks,
        }
        for week_day, tasks in sorted(tasks_by_week.items())
    ]


def _decode_framed_schedule_tasks(data: list[int]) -> list[dict[str, Any]]:
    """Read length-delimited app tasks without inferring an unreported end time."""
    weeks: dict[int, list[dict[str, Any]]] = {}
    offset = 0
    while offset < len(data):
        if len(data) - offset < _FRAMED_TASK_MINIMUM_LENGTH:
            raise ValueError("Framed schedule task header is truncated.")
        if data[offset] != _FRAMED_TASK_START:
            raise ValueError("Framed schedule task start marker is invalid.")
        length = data[offset + 1]
        end = offset + length
        if length < _FRAMED_TASK_MINIMUM_LENGTH or end > len(data):
            raise ValueError("Framed schedule task length is invalid.")
        if data[end - 1] != _FRAMED_TASK_END:
            raise ValueError("Framed schedule task end marker is invalid.")
        day_and_type = data[offset + 2]
        day = day_and_type >> 4
        if day not in SCHEDULE_WEEKDAY_NAMES:
            raise ValueError("Unsupported schedule task weekday.")
        encoded_type = day_and_type & 0x0F
        task_type = encoded_type % _CYCLIC_TASK_OFFSET
        start = _read_12_bit(data[offset + 3], data[offset + 4], low_first=True)
        _validate_schedule_minute(start, "start")
        elements = data[offset + 6 : end - 1]
        if task_type in (2, 3) and len(elements) % 2:
            raise ValueError("Edge/cruise schedule regions must contain pairs.")
        weeks.setdefault(day, []).append(
            {
                "type": task_type,
                "type_name": SCHEDULE_TASK_TYPE_NAMES.get(
                    task_type, f"unknown_{task_type}"
                ),
                "cyclic": encoded_type >= _CYCLIC_TASK_OFFSET,
                "start": start,
                "start_time": minute_text(start),
                "end": None,
                "end_time": None,
                "real_end": None,
                "real_end_time": None,
                "timing": "start_only",
                "regions": _decode_regions(task_type, elements),
            }
        )
        offset = end
    return [
        {"week_day": day, "week_day_name": SCHEDULE_WEEKDAY_NAMES[day], "tasks": tasks}
        for day, tasks in sorted(weeks.items())
    ]


def encode_schedule_week_payload(weeks: Sequence[Mapping[str, Any]]) -> str:
    """Encode readable per-week schedule entries to the app base64 block."""
    encoded = bytearray()
    for week in weeks:
        week_day = _validate_range(
            _required_int(week.get("week_day"), "week_day"),
            "week_day",
            0,
            max(SCHEDULE_WEEKDAY_NAMES),
        )
        tasks = week.get("tasks")
        if not isinstance(tasks, Sequence) or isinstance(tasks, str | bytes):
            raise ValueError("tasks must be a sequence.")
        for task in tasks:
            if not isinstance(task, Mapping):
                raise ValueError("task entries must be mappings.")
            task_type = _validate_range(
                _required_int(task.get("type"), "type"),
                "type",
                0,
                0x07,
            )
            encoded_task_type = task_type + (
                _CYCLIC_TASK_OFFSET if task.get("cyclic") else 0
            )
            start = _validate_schedule_minute(task.get("start"), "start")
            end = _validate_schedule_minute(task.get("end"), "end")
            real_end = _schedule_real_end(task.get("real_end"))
            regions = _encode_regions(task_type, task.get("regions") or [])
            element_count = _validate_range(len(regions), "regions", 0, 0x0FFF)
            encoded.extend(
                [
                    (week_day << 4) | encoded_task_type,
                    start & 0xFF,
                    ((start >> 8) & 0x0F) | ((end & 0x0F) << 4),
                    (end >> 4) & 0xFF,
                    real_end & 0xFF,
                    ((real_end >> 8) & 0x0F) | ((element_count & 0x0F) << 4),
                    (element_count >> 4) & 0xFF,
                ]
            )
            encoded.extend(regions)
    return base64.b64encode(bytes(encoded)).decode("ascii")


def schedule_task_summary(value: Any) -> dict[str, Any] | None:
    """Return a readable current scheduled task summary."""
    if not isinstance(value, list) or len(value) < 4:
        return None
    start = _as_int(value[0])
    end = _as_int(value[1])
    return {
        "start": start,
        "start_time": minute_text(start),
        "end": end,
        "end_time": minute_text(end),
        "plan_id": _as_int(value[2]),
        "version": _as_int(value[3]),
    }


def build_schedule_enable_status_request(
    *,
    map_index: int,
    version: int,
    plans: Sequence[Mapping[str, Any]],
    document_version: int = 2,
) -> dict[str, Any]:
    """Build the qualified status command for a supported document generation."""
    if type(document_version) is not int or document_version not in (2, 3):
        raise ValueError("Schedule status writes require document version 2 or 3.")
    return {
        "m": "s",
        "t": f"SCHDSV{document_version}",
        "d": {
            "i": int(map_index),
            "v": int(version),
            "s": [1 if plan.get("enabled") else 0 for plan in plans],
        },
    }


def build_schedule_upload_requests(
    *,
    map_index: int,
    payload_text: str,
    version: int,
    chunk_size: int = SCHEDULE_CHUNK_SIZE,
    document_version: int = 2,
) -> list[dict[str, Any]]:
    """Build app action requests for a full schedule payload upload."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero.")
    if type(document_version) is not int or document_version not in (2, 3):
        raise ValueError("Schedule uploads require document version 2 or 3.")
    payload_bytes = payload_text.encode("utf-8")
    requests = [
        {
            "m": "s",
            "t": f"SCHDIV{document_version}",
            "d": {"i": int(map_index), "l": len(payload_bytes), "v": int(version)},
        }
    ]
    for offset, chunk, chunk_byte_size in _iter_utf8_chunks(payload_text, chunk_size):
        requests.append(
            {
                "m": "s",
                "t": f"SCHDDV{document_version}",
                "d": {
                    "s": offset,
                    "l": chunk_byte_size,
                    "d": chunk,
                    "v": int(version),
                },
            }
        )
    return requests


def _read_12_bit(first: int, second: int, *, low_first: bool) -> int:
    if low_first:
        return ((second & 0x0F) << 8) | first
    return (second << 4) | (first >> 4)


def _decode_regions(task_type: int, values: list[int]) -> list[Any]:
    if task_type in (2, 3):
        return [values[index : index + 2] for index in range(0, len(values), 2)]
    return values


def _encode_regions(task_type: int, regions: Any) -> list[int]:
    if task_type in (2, 3):
        values: list[int] = []
        if not isinstance(regions, Sequence) or isinstance(regions, str | bytes):
            raise ValueError("regions must be a sequence.")
        for pair in regions:
            if not isinstance(pair, Sequence) or isinstance(pair, str | bytes):
                raise ValueError("edge/cruise regions must be two-value sequences.")
            if len(pair) != 2:
                raise ValueError("edge/cruise regions must have two values.")
            values.extend(_validate_byte(value, "regions") for value in pair)
        return values
    if not isinstance(regions, Sequence) or isinstance(regions, str | bytes):
        raise ValueError("regions must be a sequence.")
    return [_validate_byte(value, "regions") for value in regions]


def _iter_utf8_chunks(
    text: str,
    chunk_size: int,
) -> list[tuple[int, str, int]]:
    chunks: list[tuple[int, str, int]] = []
    offset = 0
    current: list[str] = []
    current_size = 0
    for char in text:
        char_size = len(char.encode("utf-8"))
        if current and current_size + char_size > chunk_size:
            chunks.append((offset, "".join(current), current_size))
            offset += current_size
            current = []
            current_size = 0
        if char_size > chunk_size:
            raise ValueError("chunk_size is too small for the payload text.")
        current.append(char)
        current_size += char_size
    if current:
        chunks.append((offset, "".join(current), current_size))
    return chunks


def _schedule_real_end(value: Any) -> int:
    if value is None:
        return 0x0FFF
    real_end = _required_int(value, "real_end")
    if real_end < 0:
        return 0x0FFF
    return _validate_range(real_end, "real_end", 0, 0x0FFF)


def _validate_schedule_minute(value: Any, name: str) -> int:
    return _validate_range(_required_int(value, name), name, 0, 24 * 60 - 1)


def _validate_byte(value: Any, name: str) -> int:
    return _validate_range(_required_int(value, name), name, 0, 0xFF)


def _validate_range(value: int, name: str, minimum: int, maximum: int) -> int:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


def _required_int(value: Any, name: str) -> int:
    integer = _as_int(value)
    if integer is None:
        raise ValueError(f"{name} must be an integer.")
    return integer


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
