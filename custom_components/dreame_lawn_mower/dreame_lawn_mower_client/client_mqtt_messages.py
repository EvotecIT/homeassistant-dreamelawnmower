"""Ordered device-message delivery from the MQTT thread to the client loop."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections import deque
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

from .client_device_actions import async_run_device_plan
from .device_action_plan import DevicePlanEffect
from .exceptions import DreameLawnMowerConnectionError

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .device import DreameMowerDevice

_LOGGER = logging.getLogger(__name__)


class NativeMqttMessages:
    """Serialize messages and keep their state/network work owned through close."""

    def __init__(
        self, client: DreameLawnMowerClient, device: DreameMowerDevice
    ) -> None:
        self._client, self._device = client, device
        self._protocol = device._protocol.cloud
        self._loop = asyncio.get_running_loop()
        self._messages: deque[tuple[int, dict[str, Any]]] = deque()
        self._task: asyncio.Task[None] | None = None

    def _active(self) -> bool:
        return (
            not self._client._closing
            and self._client._device is self._device
            and self._device._protocol.cloud is self._protocol
            and not self._protocol._shutdown_requested
        )

    def request(self, message: dict[str, Any]) -> None:
        """Snapshot each message before returning to the MQTT callback thread."""
        if not self._active() or not self._device._ready or self._loop.is_closed():
            return
        try:
            with self._device._state_lock:
                generation = self._device._mqtt_generation
                snapshot = copy.deepcopy(message)
            self._loop.call_soon_threadsafe(self._enqueue, generation, snapshot)
        except RuntimeError:
            if not self._loop.is_closed():
                raise

    def _enqueue(self, generation: int, message: dict[str, Any]) -> None:
        if not self._active():
            return
        self._messages.append((generation, message))
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._consume())
            self._client._cloud_read_tasks.add(self._task)
            self._task.add_done_callback(self._completed)

    def _require_device(self, device: DreameMowerDevice, generation: int) -> None:
        if (
            device is not self._device
            or not self._active()
            or device._mqtt_generation != generation
        ):
            raise DreameLawnMowerConnectionError("MQTT message owner changed")

    async def _consume(self) -> None:
        try:
            while self._messages and self._active():
                generation, message = self._messages.popleft()
                if generation != self._device._mqtt_generation:
                    continue
                try:

                    def message_plan(
                        device: DreameMowerDevice,
                        current_message: dict[str, Any] = message,
                    ) -> Generator[DevicePlanEffect, Any]:
                        return device._message_plan(current_message)

                    def require_device(
                        device: DreameMowerDevice,
                        current_generation: int = generation,
                    ) -> None:
                        self._require_device(device, current_generation)

                    await async_run_device_plan(
                        self._client,
                        message_plan,
                        require_device=require_device,
                    )
                except Exception as error:
                    _LOGGER.warning(
                        "MQTT message application failed (%s)", type(error).__name__
                    )
        finally:
            # Shutdown or owner replacement invalidates the queued messages too.
            self._messages.clear()

    def _completed(self, task: asyncio.Task[None]) -> None:
        self._client._cloud_read_tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                _LOGGER.warning("MQTT message worker failed (%s)", type(error).__name__)
