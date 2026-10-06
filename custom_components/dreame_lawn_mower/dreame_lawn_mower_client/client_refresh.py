"""Native polling with owned legacy state callbacks during the transport migration."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from threading import Event
from typing import TYPE_CHECKING

from .device_property_read import (
    apply_device_property_response,
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
    from .cloud_session import DreameCloudSession
    from .device import DreameMowerDevice


async def _run_state_worker[T](operation: Callable[[], T], cancelled: Event) -> T:
    """Keep an already-started callback owned until it finishes on cancellation."""
    worker = asyncio.create_task(asyncio.to_thread(operation))
    interrupted = False
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled.set()
            interrupted = True
        except Exception:
            if not interrupted:
                raise
    if interrupted:
        # Retrieve any worker failure while preserving the caller's cancellation.
        if not worker.cancelled():
            worker.exception()
        raise asyncio.CancelledError
    return worker.result()


async def async_update_device(client: DreameLawnMowerClient) -> DreameMowerDevice:
    """Poll native RPC once connected, retaining synchronous startup ownership."""
    cancelled = Event()

    def ensure_active() -> None:
        if cancelled.is_set() or client._closing:
            raise DreameLawnMowerConnectionError("Device refresh was cancelled")

    def prepare() -> tuple[DreameMowerDevice, list[DreameMowerProperty] | None]:
        ensure_active()
        device = client._ensure_device(cancelled=cancelled)
        if device._update_running:
            return device, None
        # Startup still owns MQTT initialization, initial capabilities and maps.
        # Keep that existing path tracked until its remaining HTTP is migrated.
        if not device.cloud_connected or not device._ready:
            ensure_active()
            client._sync_update_device()
            return device, None
        ensure_active()
        properties = device._select_update_properties()
        if not device._protocol.dreame_cloud or not device.device_connected:
            return device, properties
        if device.status.map_backup_status:
            return device, [DreameMowerProperty.MAP_BACKUP_STATUS]
        if device.status.map_recovery_status:
            return device, [DreameMowerProperty.MAP_RECOVERY_STATUS]
        return device, []

    async def refresh(cloud: DreameCloudSession) -> DreameMowerDevice:
        async with client._refresh_lock:
            try:
                device, properties = await _run_state_worker(prepare, cancelled)
                ensure_active()
                if properties is None:
                    return device
                deadline = time.monotonic() + 20
                protocol = device._protocol.cloud
                results: object = None
                if properties:
                    requests = build_device_property_request(
                        properties, device.property_mapping, device.data,
                        ready=device._ready, require_fresh_state=False,
                    )
                    if requests:
                        async with protocol.async_rpc_operation(
                            deadline=deadline,
                        ) as request_id:
                            results = await cloud.async_read_device_properties(
                                client._descriptor.did, protocol._host,
                                request_id, requests, deadline=deadline,
                            )

                def apply() -> None:
                    # Acquire on this worker: callbacks can re-enter legacy RPC.
                    lock = protocol._operation_lock()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not lock.acquire(timeout=remaining):
                        raise DreameLawnMowerConnectionError(
                            "Device refresh timed out waiting to apply state"
                        )
                    try:
                        ensure_active()
                        if client._device is not device:
                            raise DreameLawnMowerConnectionError(
                                "Device changed during refresh"
                            )
                        if properties:
                            try:
                                apply_device_property_response(
                                    device, results, require_fresh_state=False,
                                )
                            except Exception as err:
                                # Match device.update's request/application
                                # boundary; cancellation remains a BaseException.
                                raise DeviceUpdateFailedException(err) from None
                        device._finish_update()
                    finally:
                        lock.release()

                await _run_state_worker(apply, cancelled)
                ensure_active()
                return device
            except DeviceException as err:
                raise DreameLawnMowerConnectionError(str(err)) from err
            finally:
                cancelled.set()

    return await client._async_cloud_read(refresh)
