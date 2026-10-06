"""Native start ownership and the device's guarded session decision."""
from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING, Any

from .client_core import _device_start_session_identity
from .client_device_actions import async_run_device_plan
from .device_action_plan import ActionDelay, ActionRequest
from .exceptions import (
    DeviceCommandRejectedException,
    DeviceException,
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
)

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_start_with_session_identity(
    client: DreameLawnMowerClient, *, require_new_session: bool,
) -> bool | None:
    """Read session identity and select the start branch in one locked plan step."""
    new_session: bool | None = None

    def plan(device: Any) -> Generator[ActionDelay | ActionRequest, Any, Any]:
        nonlocal new_session
        new_session = _device_start_session_identity(device)
        if require_new_session and new_session is not True:
            raise DreameLawnMowerCommandRejectedError(
                "Scheduled mowing cannot resume or replace an existing task."
            )
        return (yield from device._start_mowing_plan())

    async def start(_cloud: DreameCloudSession) -> bool | None:
        try:
            await async_run_device_plan(client, plan)
        except DeviceCommandRejectedException as error:
            raise DreameLawnMowerCommandRejectedError(str(error)) from error
        except DeviceException as error:
            await client._async_reconcile_ambiguous_mutation(
                "start mowing", DreameLawnMowerConnectionError(str(error)),
                lambda snapshot: bool(
                    snapshot.started or snapshot.mowing
                    or snapshot.mowing_session_active is True
                ),
            )
        return new_session

    return await client._async_cloud_read(start)
