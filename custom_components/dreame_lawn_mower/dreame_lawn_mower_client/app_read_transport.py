"""Read-only requests and synchronous driver for the shared app read plans."""

from __future__ import annotations

from collections.abc import Generator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class AppReadRequest:
    action: Mapping[str, Any]
    retry_count: int = 0
    timeout: float = 5.0
    deadline: float | None = None


class AppReadDispatch(Protocol):
    def __call__(
        self,
        action: Mapping[str, Any],
        /,
        *,
        retry_count: int,
        timeout: float,
        deadline: float | None,
    ) -> Any: ...


def capture_app_result[T](
    plan: Generator[AppReadRequest, Any, T], result: list[T],
) -> Generator[AppReadRequest, Any]:
    """Retain the typed return value instead of erasing it in StopIteration."""
    result.append((yield from plan))


def run_app_read[T](
    plan: Generator[AppReadRequest, Any, T],
    dispatch: AppReadDispatch,
) -> T:
    """Feed transport results or errors back into the shared protocol plan."""
    result: list[T] = []
    plan_with_result = capture_app_result(plan, result)
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
