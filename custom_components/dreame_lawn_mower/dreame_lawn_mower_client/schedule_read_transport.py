"""Read-only requests and synchronous driver for the shared schedule plan."""

from __future__ import annotations

from collections.abc import Generator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ScheduleReadRequest:
    action: Mapping[str, Any]
    retry_count: int = 0
    timeout: float = 5.0
    deadline: float | None = None


class ScheduleReadDispatch(Protocol):
    def __call__(
        self,
        action: Mapping[str, Any],
        /,
        *,
        retry_count: int,
        timeout: float,
        deadline: float | None,
    ) -> Any: ...


def capture_schedule_result[T](
    plan: Generator[ScheduleReadRequest, Any, T], result: list[T],
) -> Generator[ScheduleReadRequest, Any]:
    """Retain the typed return value instead of erasing it in StopIteration."""
    result.append((yield from plan))


def run_schedule_read[T](
    plan: Generator[ScheduleReadRequest, Any, T],
    dispatch: ScheduleReadDispatch,
) -> T:
    """Feed transport results or errors back into the shared protocol plan."""
    result: list[T] = []
    plan_with_result = capture_schedule_result(plan, result)
    try:
        request = next(plan_with_result)
        while True:
            try:
                response = dispatch(
                    request.action,
                    retry_count=request.retry_count,
                    timeout=request.timeout,
                    deadline=request.deadline,
                )
            except Exception as error:
                request = plan_with_result.throw(error)
            else:
                request = plan_with_result.send(response)
    except StopIteration:
        return result[0]
    finally:
        plan_with_result.close()
