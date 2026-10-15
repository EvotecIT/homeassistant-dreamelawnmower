"""Serialize complete native task controls while retaining client task ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import TYPE_CHECKING, Any, Concatenate

from .exceptions import (
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
)

_WAIT_TIMEOUT_SECONDS = 20

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


def require_task_map_index(
    expected_map_index: int | None,
    current_map_index: int | None,
    *,
    task_name: str,
) -> None:
    """Reject a map-scoped command when its validated map is no longer active."""
    if type(expected_map_index) is not int or expected_map_index < 0:
        raise DreameLawnMowerCommandRejectedError(
            "The expected mower map index must be a nonnegative integer."
        )
    if type(current_map_index) is not int or current_map_index != expected_map_index:
        raise DreameLawnMowerCommandRejectedError(
            "The active mower map changed or could not be verified "
            f"while preparing {task_name}."
        )


def serialized_task_control[**Parameters, Result](
    operation: Callable[
        Concatenate[DreameLawnMowerClient, Parameters], Coroutine[Any, Any, Result]
    ],
) -> Callable[
    Concatenate[DreameLawnMowerClient, Parameters], Coroutine[Any, Any, Result]
]:
    """Own queued controls and keep their preflight, dispatch and readback ordered.

    Apply only to public control entrypoints. Internal command legs execute in
    the already-owned operation so a Stop-then-Dock sequence holds one slot.
    Waiting has a finite budget and client close cancels queued operations.
    """

    @wraps(operation)
    async def ordered(
        client: DreameLawnMowerClient,
        /,
        *args: Parameters.args,
        **kwargs: Parameters.kwargs,
    ) -> Result:
        async def run(_cloud: DreameCloudSession) -> Result:
            try:
                async with asyncio.timeout(_WAIT_TIMEOUT_SECONDS):
                    await client._task_control_gate.acquire()
            except TimeoutError as error:
                raise DreameLawnMowerConnectionError(
                    "Task control timed out waiting for another operation."
                ) from error
            try:
                if client._closing:
                    raise DreameLawnMowerConnectionError("Client is closing")
                return await operation(client, *args, **kwargs)
            finally:
                client._task_control_gate.release()

        return await client._async_cloud_read(run)

    return ordered
