"""Serialize complete native task controls while retaining client task ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import TYPE_CHECKING, Any, Concatenate

from .exceptions import DreameLawnMowerConnectionError

_WAIT_TIMEOUT_SECONDS = 20

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


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
