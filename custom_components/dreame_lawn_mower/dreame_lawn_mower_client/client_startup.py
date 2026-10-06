"""Native startup HTTP with owned MQTT and legacy device initialization."""

from __future__ import annotations

import asyncio
import time
from threading import Event
from typing import TYPE_CHECKING

from .device_property_read import (
    apply_device_property_response,
    build_device_property_request,
)
from .exceptions import DreameLawnMowerConnectionError

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession
    from .device import DreameMowerDevice


async def async_start_device(
    client: DreameLawnMowerClient, device: DreameMowerDevice,
    cloud: DreameCloudSession, *, deadline: float, cancelled: Event,
) -> None:
    """Reuse native login and metadata before starting the existing MQTT owner."""
    from .client_refresh import _run_state_worker

    def ensure_active() -> None:
        if cancelled.is_set() or client._closing or client._device is not device:
            raise DreameLawnMowerConnectionError("Device startup was cancelled")
        if time.monotonic() >= deadline:
            raise DreameLawnMowerConnectionError("Device startup timed out")

    try:
        async with asyncio.timeout(max(0, deadline - time.monotonic())):
            ensure_active()
            info = await cloud.async_get_connection_info(
                client._descriptor.did, deadline=deadline,
            )
            if not info:
                raise DreameLawnMowerConnectionError(
                    "Cloud device information is unavailable"
                )
            authentication = cloud.authentication

            def initialize() -> None:
                protocol = device._protocol.cloud
                lock = protocol._operation_lock()
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not lock.acquire(timeout=remaining):
                    raise DreameLawnMowerConnectionError(
                        "Device startup timed out waiting for connection ownership"
                    )
                try:
                    ensure_active()
                    protocol._handle_device_info(info)
                    protocol._apply_authentication(authentication)
                    connected_info = protocol._connect_device_info_unlocked(
                        info, device._message_callback, device._connected_callback,
                    )
                    if connected_info is None:
                        raise DreameLawnMowerConnectionError(
                            "Device connection ended during startup"
                        )
                    device._protocol._connected = True
                    device.token, device.host = " ", protocol._host
                    if device.two_factor_url:
                        device.two_factor_url = None
                        device._property_changed()
                    ensure_active()
                    device._prepare_device_initialization(connected_info)
                finally:
                    lock.release()

            await _run_state_worker(initialize, cancelled)
            ensure_active()
            protocol = device._protocol.cloud
            requests = build_device_property_request(
                device._default_properties, device.property_mapping, device.data,
                ready=device._ready, require_fresh_state=False,
            )
            async with protocol.async_rpc_operation(deadline=deadline) as request_id:
                results = await cloud.async_read_device_properties(
                    client._descriptor.did, protocol._host,
                    request_id, requests, deadline=deadline,
                )

            if not isinstance(results, (list, tuple)) or not results:
                raise DreameLawnMowerConnectionError(
                    "Initial device properties are unavailable"
                )

            def finish() -> None:
                lock = protocol._operation_lock()
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not lock.acquire(timeout=remaining):
                    raise DreameLawnMowerConnectionError(
                        "Device startup timed out waiting to apply properties"
                    )
                try:
                    ensure_active()
                    try:
                        apply_device_property_response(
                            device, results, require_fresh_state=False,
                        )
                    except Exception as err:
                        raise DreameLawnMowerConnectionError(
                            "Initial device properties are invalid"
                        ) from err
                    ensure_active()
                    # Map maintenance and privacy metadata remain legacy work.
                    # Keep these callbacks owned until they finish on shutdown.
                    device._finish_device_initialization()
                    ensure_active()
                finally:
                    lock.release()

            await _run_state_worker(finish, cancelled)
            ensure_active()
    except TimeoutError as err:
        raise DreameLawnMowerConnectionError("Device startup timed out") from err
