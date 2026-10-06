"""Synchronous adapter for the shared schedule write policy."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from .schedule_write_plan import (
    ReadSchedules,
    ReadTableFlags,
    RequireWriteAllowed,
    ScheduleCommand,
    ScheduleWriteRequest,
)


def capture_schedule_write(
    plan: Generator[ScheduleWriteRequest, Any, dict[str, Any]],
    result: list[dict[str, Any]],
) -> Generator[ScheduleWriteRequest, Any]:
    result.append((yield from plan))


def run_schedule_write(
    client: Any, plan: Generator[ScheduleWriteRequest, Any, dict[str, Any]]
) -> dict[str, Any]:
    """Preserve legacy entrypoints, lock ownership and request options."""
    result: list[dict[str, Any]] = []
    driver = capture_schedule_write(plan, result)

    def dispatch(request: ScheduleWriteRequest) -> Any:
        match request:
            case ReadSchedules():
                return client._sync_get_app_schedules(
                    map_indices=[request.map_index], include_current_task=False
                )
            case RequireWriteAllowed():
                return client._sync_require_schedule_write_allowed()
            case ScheduleCommand():
                if request.retry_count is None:
                    return client._sync_call_app_action(request.action)
                return client._sync_call_app_action(
                    request.action, retry_count=request.retry_count
                )
            case ReadTableFlags():
                return client._sync_read_schedule_tables(
                    map_index=request.map_index,
                    deadline=request.deadline,
                    include_tasks=False,
                )

    try:
        request = next(driver)
        while True:
            try:
                response = dispatch(request)
            except Exception as error:
                request = driver.throw(error)
            else:
                request = driver.send(response)
    except StopIteration:
        return result[0]
    finally:
        driver.close()
