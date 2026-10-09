"""Event-loop scheduling for device refreshes owned by the async client."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .client_refresh import async_update_device
from .client_state_reads import async_read_device_state

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .device import DreameMowerDevice

_LOGGER = logging.getLogger(__name__)


class NativeDeviceUpdates:
    """Coalesce device schedules while retaining refresh and result policies."""

    def __init__(self, client: DreameLawnMowerClient) -> None:
        self._client = client
        self._loop = asyncio.get_running_loop()
        self._timer: asyncio.TimerHandle | None = None
        self._closed = False

    def schedule(self, device: DreameMowerDevice, delay: float, force: bool) -> None:
        """Accept requests from state workers and the MQTT callback thread."""
        if self._closed or self._loop.is_closed():
            return
        try:
            self._loop.call_soon_threadsafe(self._schedule, device, delay, force)
        except RuntimeError:
            # Loop closure can race a final device callback during teardown.
            if not self._loop.is_closed():
                raise

    def _schedule(self, device: DreameMowerDevice, delay: float, force: bool) -> None:
        if (device is None or self._closed or self._client._closing
                or self._client._device is not device or device.disconnected):
            return
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if delay >= 0:
            self._timer = self._loop.call_later(delay, self._start, device, force)

    def _start(self, device: DreameMowerDevice, force: bool) -> None:
        self._timer = None
        if (device is None or self._closed or self._client._closing
                or self._client._device is not device or device.disconnected):
            return
        task = asyncio.create_task(self._refresh(device, force))
        self._client._cloud_read_tasks.add(task)
        task.add_done_callback(self._completed)

    def _completed(self, task: asyncio.Task[None]) -> None:
        self._client._cloud_read_tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                _LOGGER.warning(
                    "Scheduled device update failed (%s)", type(error).__name__
                )

    async def _refresh(self, device: DreameMowerDevice, force: bool) -> None:
        error: Exception | None = None
        try:
            await async_update_device(self._client, force_request_properties=force)
        except Exception as caught:
            error = caught

        def finish(current: DreameMowerDevice) -> None:
            if current is device:
                device._complete_scheduled_update(error)

        try:
            await async_read_device_state(self._client, finish, refresh=False)
        except Exception:
            # A busy executor/state lock must not permanently stop polling.
            # Avoid reading more state on this failure path, and retain a newer
            # caller's schedule if one already exists. Cancellation still exits.
            if self._timer is None:
                self._schedule(device, 30, False)
            raise

    def close(self) -> None:
        """Stop future work; the client's ordinary task drain owns active refreshes."""
        self._closed = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
