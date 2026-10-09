"""Owned native authentication requested by the MQTT callback thread."""

from __future__ import annotations

import asyncio
import logging
import time
from threading import Event
from typing import TYPE_CHECKING

from .client_refresh import _run_state_worker
from .exceptions import DreameLawnMowerConnectionError

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession
    from .device import DreameMowerDevice
    from .protocol_cloud import DreameMowerDreameHomeCloudProtocol

_LOGGER = logging.getLogger(__name__)


class NativeMqttAuthentication:
    """Coalesce reconnect notifications and retain client shutdown ownership."""

    def __init__(
        self,
        client: DreameLawnMowerClient,
        device: DreameMowerDevice,
        protocol: DreameMowerDreameHomeCloudProtocol,
    ) -> None:
        self._client = client
        self._device = device
        self._protocol = protocol
        self._loop = asyncio.get_running_loop()
        self._task: asyncio.Task[None] | None = None

    def request(self) -> None:
        """Return immediately to MQTT; network work belongs to the client loop."""
        if self._client._closing or self._loop.is_closed():
            return
        try:
            self._loop.call_soon_threadsafe(self._start)
        except RuntimeError:
            if not self._loop.is_closed():
                raise

    def _active(self) -> bool:
        return (
            not self._client._closing
            and self._client._device is self._device
            and self._device._protocol.cloud is self._protocol
            and not self._protocol._shutdown_requested
        )

    def _start(self) -> None:
        if not self._active() or (self._task is not None and not self._task.done()):
            return
        self._task = asyncio.create_task(self._refresh())
        self._client._cloud_read_tasks.add(self._task)
        self._task.add_done_callback(self._completed)

    def _completed(self, task: asyncio.Task[None]) -> None:
        self._client._cloud_read_tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                _LOGGER.warning(
                    "MQTT authentication refresh failed (%s)", type(error).__name__
                )

    async def _refresh(self) -> None:
        cancelled = Event()
        deadline = time.monotonic() + 20

        def require_active() -> None:
            if cancelled.is_set() or not self._active() or time.monotonic() >= deadline:
                raise DreameLawnMowerConnectionError(
                    "MQTT authentication refresh ended"
                )

        async def refresh(cloud: DreameCloudSession) -> None:
            require_active()
            await cloud.async_login(deadline=deadline)
            authentication = cloud.authentication

            def apply() -> None:
                lock = self._protocol._operation_lock()
                while not lock.acquire(timeout=0.05):
                    require_active()
                try:
                    require_active()
                    self._protocol._apply_authentication(authentication)
                    if self._protocol._client is not None:
                        self._protocol._set_client_key()
                finally:
                    lock.release()

            await _run_state_worker(apply, cancelled)

        try:
            async with asyncio.timeout(20):
                await self._client._async_cloud_read(refresh)
        finally:
            cancelled.set()
