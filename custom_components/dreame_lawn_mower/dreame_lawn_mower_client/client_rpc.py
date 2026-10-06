"""Shared native RPC routing, device ownership and lifetime."""
from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from threading import Event
from typing import TYPE_CHECKING, Any

from .client_refresh import _run_state_worker
from .cloud_wire import cloud_strings
from .exceptions import DreameLawnMowerConnectionError

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_device_rpc(
    client: DreameLawnMowerClient,
    operation: Callable[[Any, DreameCloudSession, Any, int], Awaitable[Any]],
    *, deadline: float,
    prepare: Callable[[Any], Awaitable[None]] | None = None,
) -> Any:
    """Run one RPC under existing device routing and request-ID ownership."""
    if not math.isfinite(deadline):
        raise ValueError("Device RPC deadline must be finite")
    cancelled = Event()

    async def read(cloud: DreameCloudSession) -> Any:
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                device = await _run_state_worker(
                    lambda: client._ensure_device(
                        deadline=deadline, cancelled=cancelled
                    ),
                    cancelled,
                )
                if prepare is not None:
                    await prepare(device)
                protocol = device._protocol.cloud
                if protocol is None:
                    raise DreameLawnMowerConnectionError(
                        "Cloud protocol is unavailable"
                    )
                async with protocol.async_rpc_operation(
                    deadline=deadline
                ) as request_id:
                    if client._closing or client._device is not device:
                        raise DreameLawnMowerConnectionError(
                            "Device changed during app operation"
                        )
                    if not protocol._host:
                        info = await cloud.async_get_device_info(
                            client._descriptor.did,
                            deadline=deadline,
                        )
                        if not info or str(info.get("did")) != client._descriptor.did:
                            raise DreameLawnMowerConnectionError(
                                "Cloud device identity is invalid"
                            )
                        host = info.get(cloud_strings(client._account_type)[9])
                        if not isinstance(host, str) or not host:
                            raise DreameLawnMowerConnectionError(
                                "Cloud device routing is invalid"
                            )
                        try:
                            protocol._handle_device_info(info)
                        except (KeyError, TypeError, ValueError) as err:
                            raise DreameLawnMowerConnectionError(
                                "Cloud device routing is invalid"
                            ) from err
                    if client._closing or client._device is not device:
                        raise DreameLawnMowerConnectionError(
                            "Device changed during app operation"
                        )
                    return await operation(device, cloud, protocol, request_id)
        except TimeoutError as err:
            raise DreameLawnMowerConnectionError(
                "Device RPC operation timed out"
            ) from err
        finally:
            cancelled.set()

    return await client._async_cloud_read(read)
