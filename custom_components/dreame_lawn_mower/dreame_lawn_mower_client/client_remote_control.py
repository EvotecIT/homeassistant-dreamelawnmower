"""Native remote-control writes sharing existing safety and payload policy."""
from __future__ import annotations

import time
from threading import Event
from typing import TYPE_CHECKING, Any

from .client_refresh import _run_state_worker
from .client_rpc import async_device_rpc
from .client_state_reads import read_locked_device_state
from .exceptions import (
    DeviceException,
    DreameLawnMowerConnectionError,
    InvalidActionException,
)

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_remote_control_step(
    client: DreameLawnMowerClient, rotation: int, velocity: int,
    prompt: bool | None,
) -> Any:
    """Require fresh state for movement; allow stops without a fresh read."""
    deadline = time.monotonic() + 20

    async def send(_cloud: DreameCloudSession) -> Any:
        refreshed_device = None
        if rotation or velocity:
            refreshed_device = await client._async_update_device(
                force_request_properties=True, deadline=deadline,
            )

        prepared: tuple[int, int, str] | None = None
        prepared_device: Any = None

        async def prepare_device(device: Any) -> None:
            nonlocal prepared, prepared_device
            cancelled = Event()

            def require_current() -> None:
                if (cancelled.is_set() or client._closing
                        or client._device is not device
                        or time.monotonic() >= deadline):
                    raise DreameLawnMowerConnectionError(
                        "Remote-control preparation expired or was cancelled"
                    )
                if refreshed_device is not None and device is not refreshed_device:
                    raise DreameLawnMowerConnectionError(
                        "Device changed after remote-control safety refresh"
                    )

            def prepare(device: Any) -> tuple[int, int, str]:
                require_current()
                support = client._remote_control_support_from_device(device)
                require_current()
                if not support.supported:
                    raise DreameLawnMowerConnectionError(
                        support.reason or "Remote control is not supported."
                    )
                if (rotation or velocity) and support.state_block_reason:
                    raise DreameLawnMowerConnectionError(support.state_block_reason)
                siid, piid, payload = device._prepare_remote_control_step(
                    rotation, velocity, prompt,
                )
                return siid, piid, payload

            try:
                prepared = await _run_state_worker(
                    lambda: read_locked_device_state(device, prepare, require_current),
                    cancelled,
                )
                prepared_device = device
            finally:
                cancelled.set()

        async def command(device: Any, cloud: DreameCloudSession,
                          protocol: Any, request_id: int) -> Any:
            if prepared is None or device is not prepared_device:
                raise DreameLawnMowerConnectionError(
                    "Device changed after remote-control preparation"
                )
            siid, piid, payload = prepared
            return await cloud.async_command_device_property(
                client._descriptor.did, protocol._host, request_id,
                siid, piid, payload, deadline=deadline,
            )

        try:
            return await async_device_rpc(
                client, command, deadline=deadline, prepare=prepare_device,
            )
        except (DeviceException, InvalidActionException) as error:
            raise DreameLawnMowerConnectionError(str(error)) from error

    return await client._async_cloud_read(send)
