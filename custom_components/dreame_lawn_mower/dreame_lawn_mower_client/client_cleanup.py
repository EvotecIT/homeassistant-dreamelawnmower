"""Bounded cleanup using resources retained by an already-owned operation."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .exceptions import DreameLawnMowerConnectionError

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession
    from .device import DreameMowerDevice


@dataclass
class OwnedCleanup:
    """Internal, expiring access to a retained device during operation teardown."""

    client: DreameLawnMowerClient
    device: DreameMowerDevice
    cloud: DreameCloudSession
    deadline: float
    owner: asyncio.Task[Any]
    active: bool = True

    def require_active(self, client: DreameLawnMowerClient) -> None:
        if (not self.active or client is not self.client
                or client._device is not self.device
                or client._async_cloud is not self.cloud
                or self.owner.done()
                or time.monotonic() >= self.deadline):
            raise DreameLawnMowerConnectionError("Owned cleanup expired")


async def finish_owned_cleanup(
    client: DreameLawnMowerClient, device: DreameMowerDevice,
    cloud: DreameCloudSession,
    cleanup: Callable[[OwnedCleanup], Awaitable[None]],
    *, timeout: float = 20,
) -> None:
    """Drain bounded cleanup before the parent releases its retained resources."""
    owner = asyncio.current_task()
    if owner is None or owner not in client._cloud_read_tasks:
        raise DreameLawnMowerConnectionError("Cleanup requires an owned operation")
    scope = OwnedCleanup(client, device, cloud, time.monotonic() + timeout, owner)
    scope.require_active(client)

    async def run() -> None:
        try:
            async with asyncio.timeout(max(0, scope.deadline - time.monotonic())):
                await cleanup(scope)
        finally:
            scope.active = False

    task = asyncio.create_task(run())
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
        except Exception:
            break
    if interrupted:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    try:
        task.result()
    except TimeoutError as error:
        raise DreameLawnMowerConnectionError("Owned cleanup timed out") from error
