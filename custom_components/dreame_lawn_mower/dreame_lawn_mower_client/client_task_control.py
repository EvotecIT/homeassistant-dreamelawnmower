"""Serialize complete native task controls while retaining client task ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import TYPE_CHECKING, Any, Concatenate, cast

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
    """Keep complete public controls in admission, dispatch and readback order."""
    return _ordered_task_control(operation, map_scoped=False)


def map_scoped_task_control[**Parameters, Result](
    operation: Callable[
        Concatenate[DreameLawnMowerClient, Parameters], Coroutine[Any, Any, Result]
    ],
) -> Callable[
    Concatenate[DreameLawnMowerClient, Parameters], Coroutine[Any, Any, Result]
]:
    """Bind direct calls before queueing without letting later controls overtake.

    HA supplies its validated map. Unqueued legacy direct callers may omit it;
    capture their current map while reserving admission ahead of later controls.
    Queued callers must supply their map, and every operation rechecks it.
    """
    return _ordered_task_control(operation, map_scoped=True)


def _ordered_task_control[**Parameters, Result](
    operation: Callable[
        Concatenate[DreameLawnMowerClient, Parameters], Coroutine[Any, Any, Result]
    ],
    *,
    map_scoped: bool,
) -> Callable[
    Concatenate[DreameLawnMowerClient, Parameters], Coroutine[Any, Any, Result]
]:
    @wraps(operation)
    async def ordered(
        client: DreameLawnMowerClient,
        /,
        *args: Parameters.args,
        **kwargs: Parameters.kwargs,
    ) -> Result:
        unbound_map = map_scoped and kwargs.get("expected_map_index") is None
        queued_without_map = unbound_map and (
            client._task_control_admission_gate.locked()
            or client._task_control_gate.locked()
        )

        async def run(_cloud: DreameCloudSession) -> Result:
            if unbound_map and (
                queued_without_map
                or client._task_control_admission_gate.locked()
                or client._task_control_gate.locked()
            ):
                raise DreameLawnMowerCommandRejectedError(
                    "Pass expected_map_index to queue movement while another "
                    "mower control is pending."
                )
            arguments: dict[str, Any] = dict(kwargs)
            acquired = False
            try:
                try:
                    async with asyncio.timeout(_WAIT_TIMEOUT_SECONDS):
                        async with client._task_control_admission_gate:
                            if map_scoped:
                                expected = arguments.get("expected_map_index")
                                if expected is None:
                                    expected = (
                                        await client.async_get_current_app_map_index()
                                    )
                                require_task_map_index(
                                    expected, expected, task_name="map-scoped movement",
                                )
                                arguments["expected_map_index"] = expected
                            await client._task_control_gate.acquire()
                            acquired = True
                except TimeoutError as error:
                    raise DreameLawnMowerConnectionError(
                        "Task control timed out preparing or waiting for "
                        "another operation."
                    ) from error
                if client._closing:
                    raise DreameLawnMowerConnectionError("Client is closing")
                return await operation(client, *args, **cast(Any, arguments))
            finally:
                if acquired:
                    client._task_control_gate.release()

        return await client._async_cloud_read(run)

    return ordered
