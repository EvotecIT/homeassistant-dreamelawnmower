"""Helpers for mower-native app schedule payloads."""

from __future__ import annotations

import base64
import json
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
            plan["weeks"] = decode_schedule_week_payload(str(raw_plan[3]))
        plans.append(plan)
    return plans


def decode_schedule_week_payload(payload: str) -> list[dict[str, Any]]:
    """Decode the base64 per-week schedule task payload used by the app."""
    data = list(base64.b64decode(payload))
    index = 0
    tasks_by_week: dict[int, list[dict[str, Any]]] = {}

    while index < len(data):
        if index + 7 > len(data):
            break
        first = data[index]
        encoded_task_type = first & 0x0F
        week_day = first >> 4
        start = _read_12_bit(data[index + 1], data[index + 2], low_first=True)
        end = _read_12_bit(data[index + 2], data[index + 3], low_first=False)
        real_end = _read_12_bit(data[index + 4], data[index + 5], low_first=True)
        if real_end == 0x0FFF:
            real_end = -1
        element_count = _read_12_bit(data[index + 5], data[index + 6], low_first=False)
        element_start = index + 7
        element_end = element_start + element_count
        raw_elements = data[element_start:element_end]
        index = element_end

        task_type = encoded_task_type % _CYCLIC_TASK_OFFSET
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


def _read_12_bit(first: int, second: int, *, low_first: bool) -> int:
    if low_first:
        return ((second & 0x0F) << 8) | first
    return (second << 4) | (first >> 4)


def _decode_regions(task_type: int, values: list[int]) -> list[Any]:
    if task_type in (2, 3):
        return [values[index : index + 2] for index in range(0, len(values), 2)]
    return values


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
