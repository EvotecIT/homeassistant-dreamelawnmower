"""Native polling with owned legacy state callbacks during the transport migration."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable, Generator
from threading import Event, Lock
from typing import TYPE_CHECKING, Any

from .client_startup import async_start_device
from .device_action_plan import DevicePlanEffect
from .device_property_read import (
    apply_device_property_response_plan,
    build_device_property_request,
)
from .device_types import DreameMowerProperty
from .exceptions import (
    DeviceException,
    DeviceUpdateFailedException,
    DreameLawnMowerConnectionError,
)

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .client_cleanup import OwnedCleanup
    from .cloud_session import DreameCloudSession
    from .device import DreameMowerDevice


async def _run_state_worker[T](operation: Callable[[], T], cancelled: Event) -> T:
    """Keep an already-started callback owned until it finishes on cancellation."""
    start_lock = Lock()
    started = False

    def run() -> T:
        nonlocal started
        with start_lock:
            if cancelled.is_set():
                raise asyncio.CancelledError
            started = True
        return operation()

    worker = asyncio.create_task(asyncio.to_thread(run))
    interrupted = False
    while not worker.done():
        try:
            # Waiting does not cancel the worker or create a shield future
            # that reports its expected cancellation failure independently.
            # This owner retrieves the result after the worker has drained.
            await asyncio.wait({worker})
        except asyncio.CancelledError:
            with start_lock:
                cancelled.set()
                if not started:
                    # A queued operation can be cancelled without waiting for
                    # an unrelated occupied executor. The gate also prevents
                    # its body starting if the executor races cancellation.
                    worker.cancel()
            interrupted = True
    if interrupted:
        # Retrieve any worker failure while preserving the caller's cancellation.
        if not worker.cancelled():
            worker.exception()
        raise asyncio.CancelledError
    return worker.result()


async def async_update_device(
    client: DreameLawnMowerClient,
    *,
    force_request_properties: bool = False,
    deadline: float | None = None,
    _cleanup: OwnedCleanup | None = None,
) -> DreameMowerDevice:
    """Use native startup metadata and RPC with owned legacy state callbacks."""
    if _cleanup is not None:
        _cleanup.require_active(client)
        deadline = min(deadline, _cleanup.deadline) if deadline else _cleanup.deadline
    cancelled = Event()
    supplied_deadline = deadline
    if deadline is not None and not math.isfinite(deadline):
        raise ValueError("Device refresh deadline must be finite")
    if force_request_properties and deadline is None:
        deadline = time.monotonic() + 20

    def ensure_active() -> None:
        if _cleanup is not None:
            _cleanup.require_active(client)
        if cancelled.is_set() or (_cleanup is None and client._closing):
            raise DreameLawnMowerConnectionError("Device refresh was cancelled")
        if deadline is not None and time.monotonic() >= deadline:
            raise DreameLawnMowerConnectionError("Device refresh timed out")

    def prepare() -> tuple[DreameMowerDevice, list[DreameMowerProperty] | None]:
        ensure_active()
        device = (
            _cleanup.device
            if _cleanup is not None
            else client._ensure_device(deadline=deadline, cancelled=cancelled)
        )
        if device._update_running:
            if force_request_properties:
                raise DeviceUpdateFailedException(
                    "Fresh mower task state is unavailable "
                    "while another update is running."
                )
            return device, None
        if _cleanup is None and (
            not device._ready
            or (supplied_deadline is None and not device.cloud_connected)
        ):
            return device, []
        return device, select_properties(device)

    def select_properties(device: DreameMowerDevice) -> list[DreameMowerProperty]:
        ensure_active()
        properties = device._select_update_properties()
        if (
            force_request_properties
            or not device._protocol.dreame_cloud
            or not device.device_connected
        ):
            return properties
        if device.status.map_backup_status:
            return [DreameMowerProperty.MAP_BACKUP_STATUS]
        if device.status.map_recovery_status:
            return [DreameMowerProperty.MAP_RECOVERY_STATUS]
        return []

    async def refresh(cloud: DreameCloudSession) -> DreameMowerDevice:
        async with client._refresh_lock:
            try:
                device, properties = await _run_state_worker(prepare, cancelled)
                ensure_active()
                if properties is None:
                    return device
                rpc_deadline = (
                    deadline if deadline is not None else time.monotonic() + 20
                )
                if _cleanup is None and (
                    not device._ready
                    or (supplied_deadline is None and not device.cloud_connected)
                ):
                    await async_start_device(
                        client,
                        device,
                        cloud,
                        deadline=rpc_deadline,
                        cancelled=cancelled,
                    )
                    properties = await _run_state_worker(
                        lambda: select_properties(device),
                        cancelled,
                    )
                protocol = device._protocol.cloud
                results: object = None
                if properties:
                    requests = build_device_property_request(
                        properties,
                        device.property_mapping,
                        device.data,
                        ready=device._ready,
                        require_fresh_state=force_request_properties,
                    )
                    if requests:
                        async with protocol.async_rpc_operation(
                            deadline=rpc_deadline,
                        ) as request_id:
                            results = await cloud.async_read_device_properties(
                                client._descriptor.did,
                                protocol._host,
                                request_id,
                                requests,
                                deadline=rpc_deadline,
                            )

                from .client_device_actions import async_run_device_plan

                def require_current(current: DreameMowerDevice) -> None:
                    ensure_active()
                    if current is not device:
                        raise DreameLawnMowerConnectionError(
                            "Device changed during refresh"
                        )

                def apply(
                    current: DreameMowerDevice,
                ) -> Generator[DevicePlanEffect, Any]:
                    if properties:
                        try:
                            yield from apply_device_property_response_plan(
                                current,
                                results,
                                require_fresh_state=force_request_properties,
                            )
                        except Exception as err:
                            raise DeviceUpdateFailedException(err) from None
                    yield from current._finish_update_plan()

                await async_run_device_plan(
                    client,
                    apply,
                    deadline=rpc_deadline,
                    _cleanup=_cleanup,
                    require_device=require_current,
                )
                ensure_active()
                return device
            except DeviceException as err:
                raise DreameLawnMowerConnectionError(str(err)) from err
            finally:
                cancelled.set()

    async def bounded_refresh(cloud: DreameCloudSession) -> DreameMowerDevice:
        try:
            async with asyncio.timeout(
                None if deadline is None else max(0, deadline - time.monotonic())
            ):
                return await refresh(cloud)
        except TimeoutError as err:
            raise DreameLawnMowerConnectionError("Device refresh timed out") from err

    if _cleanup is not None:
        return await bounded_refresh(_cleanup.cloud)
    return await client._async_cloud_read(bounded_refresh)
