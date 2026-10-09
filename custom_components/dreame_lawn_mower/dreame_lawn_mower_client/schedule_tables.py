"""Decode table-based schedules into the shared mower schedule model."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .exceptions import DreameLawnMowerConnectionError
from .schedule import SCHEDULE_TASK_TYPE_NAMES, SCHEDULE_WEEKDAY_NAMES, minute_text

SCHEDULE_TABLE_SLOTS = 2


def schedule_table_ids(map_index: int) -> tuple[int, int]:
    """Return the two table identifiers belonging to a nonnegative map index."""
    index = _integer(map_index, "map index")
    if index < 0:
        raise ValueError("Schedule tables require a nonnegative map index.")
    first = index * SCHEDULE_TABLE_SLOTS
    return first, first + 1


def decode_schedule_tables(value: Any, *, map_index: int) -> list[dict[str, Any]]:
    """Validate table identity and retain task references for subsequent reads.

    A valid empty list is an empty inventory. Missing tables are not synthesized:
    a caller can distinguish them from an explicitly reported empty table.
    """
    expected_ids = schedule_table_ids(map_index)
    if not isinstance(value, list):
        raise ValueError("Schedule table response is not a list.")
    plans: dict[int, dict[str, Any]] = {}
    for record in value:
        if not isinstance(record, list) or len(record) < 5:
            raise ValueError("Schedule table record is incomplete.")
        table_id = _integer(record[0], "table id")
        if table_id not in expected_ids or table_id in plans:
            raise ValueError(
                "Schedule table identity is duplicated or outside its map."
            )
        enabled = _flag(record[1], "table enabled")
        name = record[3]
        if not isinstance(name, str) or not isinstance(record[4], list):
            raise ValueError("Schedule table name or task references are invalid.")
        references: dict[int, int] = {}
        for reference in record[4]:
            if not isinstance(reference, list) or len(reference) < 2:
                raise ValueError("Schedule task reference is incomplete.")
            task_id = _integer(reference[0], "task id")
            version = _integer(reference[1], "task version")
            if task_id < 0 or version < 0 or task_id in references:
                raise ValueError("Schedule task identity or version is invalid.")
            references[task_id] = version
        plans[table_id] = {
            "plan_id": table_id - expected_ids[0],
            "table_id": table_id,
            "enabled": enabled,
            "name": name,
            "weeks": [],
            "task_references": references,
            "tasks_complete": True,
        }
    return [plans[table_id] for table_id in sorted(plans)]


def decode_schedule_table_task(
    value: Any,
    *,
    task_id: int,
) -> list[dict[str, Any]]:
    """Decode a task without inventing a duration absent from table telemetry."""
    if not isinstance(value, list) or len(value) < 6:
        raise ValueError("Schedule table task is incomplete.")
    if _integer(value[0], "task id") != task_id:
        raise ValueError("Schedule table task belongs to another request.")
    enabled = _flag(value[1], "task enabled")
    encoded_type = _integer(value[2], "task type")
    start = _integer(value[3], "task start")
    if not 0 <= start < 1440 or not 0 <= encoded_type < 16:
        raise ValueError("Schedule table task start or type is invalid.")
    if not isinstance(value[4], list) or not isinstance(value[5], list):
        raise ValueError("Schedule table task weekdays or regions are invalid.")
    days = {_integer(day, "weekday") for day in value[4]}
    if any(day not in SCHEDULE_WEEKDAY_NAMES for day in days):
        raise ValueError("Schedule table task weekday is invalid.")
    task_type = encoded_type % 8
    regions: list[Any] = []
    for region in value[5]:
        if task_type == 2:
            if not isinstance(region, list) or len(region) != 2:
                raise ValueError("Schedule edge target must be a contour pair.")
            pair = [_integer(part, "edge target") for part in region]
            if any(part < 0 for part in pair):
                raise ValueError("Schedule edge target is invalid.")
            regions.append(pair)
        else:
            target = _integer(region, "region target")
            if target < 0:
                raise ValueError("Schedule region target is invalid.")
            regions.append(target)
    task = {
        "task_id": task_id,
        "type": task_type,
        "type_name": SCHEDULE_TASK_TYPE_NAMES.get(task_type, f"unknown_{task_type}"),
        "cyclic": encoded_type >= 8,
        "start": start,
        "start_time": minute_text(start),
        "end": None,
        "end_time": None,
        "timing": "start_only",
        "regions": regions,
    }
    return (
        [
            {
                "week_day": day,
                "week_day_name": SCHEDULE_WEEKDAY_NAMES[day],
                "tasks": [dict(task)],
            }
            for day in sorted(days)
        ]
        if enabled
        else []
    )


def combine_schedule_table_weeks(
    tasks: Sequence[Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Combine task reads into the existing calendar's per-weekday structure."""
    weeks: dict[int, dict[str, Any]] = {}
    for task_weeks in tasks:
        for week in task_weeks:
            day = week["week_day"]
            entry = weeks.setdefault(
                day,
                {
                    "week_day": day,
                    "week_day_name": week["week_day_name"],
                    "tasks": [],
                },
            )
            entry["tasks"].extend(week["tasks"])
    for week in weeks.values():
        week["tasks"].sort(key=lambda task: (task["start"], task["task_id"]))
    return [weeks[day] for day in sorted(weeks)]


def _integer(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"Schedule {field} must be an integer.")
    return value


def _flag(value: Any, field: str) -> bool:
    parsed = _integer(value, field)
    if parsed not in (0, 1):
        raise ValueError(f"Schedule {field} must be 0 or 1.")
    return parsed == 1


def schedule_table_data(response: Any) -> Any:
    """Require an explicit success code; a failure value is not an empty list."""
    if (
        not isinstance(response, Mapping)
        or response.get("r") != 0
        or isinstance(response.get("r"), bool)
    ):
        raise DreameLawnMowerConnectionError(
            "Schedule table read was not acknowledged."
        )
    return response.get("d")
