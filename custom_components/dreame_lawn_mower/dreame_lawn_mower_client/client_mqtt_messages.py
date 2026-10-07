"""Ordered device-message delivery from the MQTT thread to the client loop."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections import deque
from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any

from .client_device_actions import async_run_device_plan
from .client_state_reads import async_read_device_state
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
        self._messages: deque[tuple[int, dict[str, Any] | None]] = deque()
        self._generation = device._mqtt_generation
        self._task: asyncio.Task[None] | None = None

    def _active(self) -> bool:
        return (
            not self._client._closing
            and self._client._device is self._device
            and self._device._protocol.cloud is self._protocol
            and not self._protocol._shutdown_requested
        )

    def request(self, message: dict[str, Any]) -> None:
        """Detach a Paho payload without waiting for worker-owned device state."""
        if not self._active() or not self._device._ready or self._loop.is_closed():
            return
        try:
            snapshot = copy.deepcopy(message)
            self._dispatch(lambda: self._enqueue(self._generation, snapshot))
        except RuntimeError:
            if not self._loop.is_closed():
                raise

    def request_connected(self) -> None:
        """Order reconnect before subsequent Paho messages without taking a lock."""
        if self._active() and not self._loop.is_closed():
            self._dispatch(self._enqueue_connected)

    def _dispatch(self, callback: Callable[[], None]) -> None:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._loop:
            callback()
        else:
            self._loop.call_soon_threadsafe(callback)

    def _enqueue_connected(self) -> None:
        if self._active():
            self._generation += 1
            self._enqueue(self._generation, None)

    def _enqueue(self, generation: int, message: dict[str, Any] | None) -> None:
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
            or generation != self._generation
            or device._mqtt_generation != generation
        ):
            raise DreameLawnMowerConnectionError("MQTT message owner changed")

    async def _consume(self) -> None:
        try:
            while self._messages and self._active():
                generation, message = self._messages.popleft()
                try:
                    if message is None:

                        def connected(device: DreameMowerDevice) -> None:
                            if device is not self._device or not self._active():
                                raise DreameLawnMowerConnectionError(
                                    "MQTT connection owner changed"
                                )
                            device._apply_connected_callback()

                        await async_read_device_state(
                            self._client, connected, refresh=False
                        )
                        continue
                    if generation != self._generation:
                        continue

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
