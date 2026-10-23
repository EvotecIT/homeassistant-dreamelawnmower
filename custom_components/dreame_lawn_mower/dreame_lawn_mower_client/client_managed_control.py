"""Serialized commands restricted to the task owned by a managed automation."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .client_start_control import async_start_with_session_identity
from .client_task_control import require_task_map_index
from .exceptions import DreameLawnMowerCommandRejectedError

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient


async def async_control_owned_task(
    client: DreameLawnMowerClient,
    command: str,
    *,
    expected_task_id: int,
    expected_map_index: int,
    admission: Callable[[], bool],
) -> str:
    """Check task/map identity inside task admission; never start a fresh task.

    The caller holds the client's task-control gate. A return/charge transition
    cannot provide an enforced pause hold, so pause requests there end the task
    and dock instead. They never acquire automatic resume authority.
    """
    if command not in {"pause", "dock", "resume"}:
        raise ValueError("Unknown managed mowing command")
    snapshot = await client.async_refresh_authoritative_snapshot()
    if (
        type(expected_task_id) is not int
        or snapshot.mission_task_id != expected_task_id
        or snapshot.online is not True
        or snapshot.task_resumable is not True
    ):
        raise DreameLawnMowerCommandRejectedError(
            "The owned mower task is no longer confirmed"
        )
    require_task_map_index(
        expected_map_index,
        await client.async_get_current_app_map_index(),
        task_name="managed task control",
    )
    if not admission():
        raise DreameLawnMowerCommandRejectedError("Managed task permission was revoked")
    if command == "resume":
        if snapshot.paused is not True or snapshot.error_code != 0:
            raise DreameLawnMowerCommandRejectedError(
                "Only the confirmed paused task may resume"
            )
        await async_start_with_session_identity(
            client,
            require_new_session=False,
            require_resumed_session=True,
            admission=admission,
        )
        return "resumed"
    if command == "dock" or snapshot.returning is True or snapshot.docked is True:
        await client._async_stop_then_dock()
        return "docked"
    await client._async_pause_mowing(admission=admission)
    return "paused"
