"""Native schedule writes under the shared transaction and device owners."""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

from .client_app_reads import async_command_app_action, async_run_app_read
from .client_schedule_async import async_schedule_operation
from .client_schedule_write_transport import capture_schedule_write
from .exceptions import DreameLawnMowerCommandRejectedError
from .schedule import schedule_write_block_reason
from .schedule_read_plan import read_schedules, read_tables
from .schedule_write_plan import (
    ReadSchedules,
    ReadTableFlags,
    RequireWriteAllowed,
    ScheduleCommand,
    ScheduleWriteRequest,
)

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient


async def async_run_schedule_write(
    client: DreameLawnMowerClient,
    plan: Generator[ScheduleWriteRequest, Any, dict[str, Any]],
) -> dict[str, Any]:
    """Keep all preparation and command legs inside the existing schedule lock."""

    async def run() -> dict[str, Any]:
        # Lock acquisition has its own bounded budget; a multi-chunk upload has
        # one finite transaction budget after it obtains ownership.
        deadline = time.monotonic() + 120
        result: list[dict[str, Any]] = []
        driver = capture_schedule_write(plan, result)

        async def dispatch(request: ScheduleWriteRequest) -> Any:
            match request:
                case ReadSchedules():
                    return await async_run_app_read(
                        client,
                        read_schedules(
                            client,
                            map_indices=[request.map_index],
                            include_raw=request.include_raw,
                            include_current_task=False,
                        ),
                        deadline=deadline,
                    )
                case RequireWriteAllowed():
                    snapshot = await client._async_refresh_authoritative_snapshot(
                        deadline=deadline,
                    )
                    reason = schedule_write_block_reason(snapshot)
                    if reason is not None:
                        raise DreameLawnMowerCommandRejectedError(reason)
                    return None
                case ScheduleCommand():
                    return await async_command_app_action(
                        client,
                        request.action,
                        deadline=min(
                            deadline, time.monotonic() + (request.timeout or 20),
                        ),
                    )
                case ReadTableFlags():
                    return await async_run_app_read(
                        client,
                        read_tables(
                            map_index=request.map_index,
                            deadline=min(deadline, request.deadline),
                            include_tasks=False,
                        ),
                        deadline=deadline,
                    )

        try:
            request = next(driver)
            while True:
                try:
                    response = await dispatch(request)
                except Exception as error:
                    request = driver.throw(error)
                else:
                    request = driver.send(response)
        except StopIteration:
            return result[0]
        finally:
            driver.close()

    try:
        return await async_schedule_operation(client, run)
    finally:
        plan.close()
