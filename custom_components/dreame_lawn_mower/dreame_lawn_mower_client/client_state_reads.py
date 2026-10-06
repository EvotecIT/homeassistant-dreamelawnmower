"""Owned state-only reads with native refresh and optional cloud fallback."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from threading import Event
from typing import TYPE_CHECKING, Any

from .client_refresh import _run_state_worker
from .exceptions import DreameLawnMowerConnectionError

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession
    from .device import DreameMowerDevice


def read_locked_device_state[T](
    device: DreameMowerDevice,
    read_state: Callable[[DreameMowerDevice], T],
    require_active: Callable[[], None],
) -> T:
    """Acquire state with bounded waits and recheck lifetime around callbacks."""
    while True:
        require_active()
        if device._state_lock.acquire(timeout=0.05):
            break
    try:
        require_active()
        result = read_state(device)
        require_active()
        return result
    finally:
        device._state_lock.release()


async def async_read_device_state[T](
    client: DreameLawnMowerClient,
    read_state: Callable[[DreameMowerDevice], T],
    *, refresh: bool,
) -> T:
    """Refresh natively, then drain any started state reader before closing."""
    cancelled = Event()
    deadline = time.monotonic() + 20.0

    async def read(_cloud: DreameCloudSession) -> T:
        device = (
            await client._async_update_device()
            if refresh else await _run_state_worker(
                lambda: client._ensure_device(deadline=deadline, cancelled=cancelled),
                cancelled,
            )
        )

        def active() -> None:
            if cancelled.is_set() or client._closing or client._device is not device:
                raise DreameLawnMowerConnectionError("Device state read was cancelled")
            if time.monotonic() >= deadline:
                raise DreameLawnMowerConnectionError("Device state read timed out")

        def build() -> T:
            return read_locked_device_state(device, read_state, active)

        return await _run_state_worker(build, cancelled)

    async def bounded(cloud: DreameCloudSession) -> T:
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                return await read(cloud)
        except TimeoutError as error:
            raise DreameLawnMowerConnectionError(
                "Device state read timed out"
            ) from error
        finally:
            cancelled.set()

    return await client._async_cloud_read(bounded)


async def async_read_cached_property[T](
    client: DreameLawnMowerClient,
    property_key: str,
    read_state: Callable[[DreameMowerDevice], T | None],
    decode_cloud: Callable[[Any], T | None],
    *, refresh: bool, include_cloud: bool,
) -> T | None:
    """Keep realtime evidence first and own the complete cloud fallback."""
    async def read(_cloud: DreameCloudSession) -> T | None:
        value = await async_read_device_state(client, read_state, refresh=refresh)
        if value is not None or not include_cloud:
            return value
        response = await client.async_get_cloud_properties(property_key)
        return decode_cloud(response)

    return await client._async_cloud_read(read)
