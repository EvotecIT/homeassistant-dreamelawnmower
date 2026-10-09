"""Preserve qualified native schedule frames through a shared edit policy."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Generator
from copy import deepcopy
from typing import Any

from .client_shared_helpers import _ensure_app_write_succeeded
from .exceptions import DreameLawnMowerConnectionError, mark_write_attempted
from .schedule import build_schedule_upload_requests, decode_schedule_payload_text
from .schedule_write_plan import (
    ReadSchedules,
    RequireWriteAllowed,
    ScheduleCommand,
    ScheduleWriteRequest,
)


def plan_schedule_start_time(
    model: str,
    map_index: int,
    plan_id: int,
    week_day: int,
    task_index: int,
    start: int,
    execute: bool,
    confirm_write: bool,
) -> Generator[ScheduleWriteRequest, Any, dict[str, Any]]:
    """Edit only the selected start bits and confirm every other native field."""
    if execute and not confirm_write:
        raise ValueError("Schedule writes require confirm_write=True.")
    if model != "dreame.mower.g2408":
        raise ValueError("Native start-time editing is qualified for Dreame A2.")
    # Transfer index semantics were proved for this document and season only.
    if type(map_index) is not int or map_index != 0:
        raise ValueError("Native start-time editing is qualified for map 0.")
    if type(plan_id) is not int or plan_id != 0:
        raise ValueError("Native start-time editing is qualified for plan 0.")
    if type(week_day) is not int or not 0 <= week_day <= 6:
        raise ValueError("week_day must be 0 (Sunday) through 6 (Saturday).")
    if type(task_index) is not int or task_index < 0:
        raise ValueError("task_index must be a nonnegative integer.")
    if type(start) is not int or not 0 <= start < 1440:
        raise ValueError("start must be minutes since midnight from 0 to 1439.")
    before, native = decode_edit_document((yield ReadSchedules(0, include_raw=True)))
    if before["plans"][0].get("task_payload_format") != "framed":
        raise ValueError("Start-time editing requires framed native tasks.")
    row = deepcopy(native["d"][0])
    blob = bytearray(base64.b64decode(row[3], validate=True))
    # The normal decoder validates every frame before any physical setter.
    decode_schedule_payload_text(before["raw_text"])
    offset = 0
    day_task_index = 0
    selected_offset = None
    while offset < len(blob):
        length = blob[offset + 1]
        if blob[offset + 2] >> 4 == week_day:
            if day_task_index == task_index:
                selected_offset = offset
                break
            day_task_index += 1
        offset += length
    if selected_offset is None:
        raise ValueError("The selected existing schedule task was not found.")
    offset = selected_offset
    if blob[offset + 1] != 7 or blob[offset + 2] & 15:
        raise ValueError("Start edits are qualified for all-area mowing tasks.")
    previous_start = blob[offset + 3] | ((blob[offset + 4] & 15) << 8)
    blob[offset + 3] = start & 255
    blob[offset + 4] = (blob[offset + 4] & 240) | (start >> 8)
    row[3] = base64.b64encode(blob).decode("ascii")
    target = deepcopy(native)
    target["d"][0] = row
    text = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    transaction = time.time_ns() // 1_000_000
    requests = build_schedule_upload_requests(
        map_index=map_index,
        payload_text=text,
        version=transaction,
        chunk_size=50,
        document_version=3,
    )
    result: dict[str, Any] = {
        "source": "app_action_schedule_write",
        "action": "set_schedule_task_start_time",
        "dry_run": not execute,
        "executed": False,
        "confirmed": False,
        "changed": previous_start != start,
        "map_index": map_index,
        "plan_id": plan_id,
        "week_day": week_day,
        "task_index": task_index,
        "previous_start": previous_start,
        "start": start,
        "start_time": f"{start // 60:02}:{start % 60:02}",
        "target_plan": before["plans"][0],
        "version": native["v"],
        "schedule": before,
        "request": {"sequence": requests},
    }
    if not execute:
        return result
    yield RequireWriteAllowed()
    if previous_start == start:
        result.update(confirmed=True, confirmed_schedule=before)
        return result
    # Check external edits again after the forced task-property preflight.
    _, rechecked = decode_edit_document((yield ReadSchedules(0, include_raw=True)))
    if rechecked != native:
        raise DreameLawnMowerConnectionError(
            "The native schedule changed during preparation; refresh and retry."
        )
    try:
        for request in requests:
            response = yield ScheduleCommand(request, retry_count=0, timeout=5.0)
            _ensure_app_write_succeeded(
                response,
                operation="Schedule time edit",
                require_inner_ack=request["t"] != "SCHDIV3",
            )
        _, transferred_native = decode_edit_document(
            (yield ReadSchedules(0, include_raw=True))
        )
        require_exact_schedule_edit(transferred_native, target)
        # Use the returned document token for the status leg.
        yield RequireWriteAllowed()
        status = {
            "m": "s",
            "t": "SCHDSV3",
            "d": {
                "i": map_index,
                "v": transferred_native["v"],
                "s": [row[1] for row in native["d"]],
            },
        }
        response = yield ScheduleCommand(status, retry_count=0, timeout=5.0)
        _ensure_app_write_succeeded(
            response, operation="Schedule time edit", require_inner_ack=True
        )
        confirmed, confirmed_native = decode_edit_document(
            (yield ReadSchedules(0, include_raw=True))
        )
        require_exact_schedule_edit(confirmed_native, target)
    except (Exception, asyncio.CancelledError) as err:
        # Validation can also fail after the mower accepted a row.
        mark_write_attempted(err, fields=["schedule"])
        raise
    result.update(
        executed=True,
        confirmed=True,
        version=confirmed_native["v"],
        confirmed_schedule=confirmed,
    )
    # Expose a diagnostic preview without persisting the native raw payload.
    result["schedule"] = {k: v for k, v in before.items() if k != "raw_text"}
    result["confirmed_schedule"] = {
        k: v for k, v in confirmed.items() if k != "raw_text"
    }
    return result


def decode_edit_document(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    schedules = payload.get("schedules", [])
    if payload.get("errors") or len(schedules) != 1:
        raise DreameLawnMowerConnectionError("Native schedule read is incomplete.")
    schedule = schedules[0]
    if schedule.get("document_version") != 3 or not schedule.get("available"):
        raise ValueError("Start-time editing requires a readable V3 schedule.")
    native = json.loads(schedule["raw_text"])
    rows = native.get("d")
    if (
        not isinstance(rows, list)
        or len(rows) != 2
        or not isinstance(rows[0], list)
        or len(rows[0]) != 4
        or not isinstance(rows[1], list)
        or len(rows[1]) not in (3, 4)
        or type(rows[0][0]) is not int
        or rows[0][0] != 0
        or type(rows[1][0]) is not int
        or rows[1][0] != 1
        or any(type(row[1]) is not int or row[1] not in (0, 1) for row in rows)
        or sum(row[1] for row in rows) > 1
        or type(native.get("v")) is not int
        or native["v"] != schedule["version"]
    ):
        raise ValueError("Native seasonal plan identity or version is unsupported.")
    return schedule, native


def require_exact_schedule_edit(
    actual: dict[str, Any],
    target: dict[str, Any],
) -> None:
    # Document v is a regenerated token; every other native field must match.
    if {k: v for k, v in actual.items() if k != "v"} != {
        k: v for k, v in target.items() if k != "v"
    }:
        raise DreameLawnMowerConnectionError(
            "The mower did not confirm the exact schedule edit. "
            "Read the native schedule before attempting another write."
        )
