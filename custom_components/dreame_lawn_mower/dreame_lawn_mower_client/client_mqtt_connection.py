"""Own the HA-facing MQTT connection and reconnect task."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import paho.mqtt.client as mqtt

from .mqtt_event_loop import MqttEventLoop

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient

_LOGGER = logging.getLogger(__name__)
MQTT_CONNECT_TIMEOUT = 20.0


class NativeMqttConnection:
    """Keep connection retries and cleanup on the owning asyncio loop."""

    def __init__(self, owner: DreameLawnMowerClient) -> None:
        self._owner = owner
        self._loop = asyncio.get_running_loop()
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    def request(self, client: mqtt.Client, host: str, port: int) -> None:
        """Accept startup from the serialized device worker."""
        self._loop.call_soon_threadsafe(self._start, client, host, port)

    def _start(self, client: mqtt.Client, host: str, port: int) -> None:
        if self._closed or self._owner._closing:
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(client, host, port))
            self._task.add_done_callback(self._completed)

    def _completed(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            _LOGGER.warning("MQTT connection owner failed (%s)", type(error).__name__)

    async def _run(self, client: mqtt.Client, host: str, port: int) -> None:
        original_connect = client.on_connect
        original_disconnect = client.on_disconnect
        disconnected = asyncio.Event()
        acknowledged = asyncio.Event()
        accepted = False

        def on_connect(*args: Any) -> None:
            nonlocal accepted
            accepted = args[3] == 0
            acknowledged.set()
            if original_connect is not None:
                original_connect(*args)
            if args[3] != 0:
                disconnected.set()

        def on_disconnect(*args: Any) -> None:
            acknowledged.set()
            if original_disconnect is not None:
                original_disconnect(*args)
            disconnected.set()

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        delay = 1
        try:
            while not self._closed and not self._owner._closing:
                driver = MqttEventLoop(client)
                disconnected.clear()
                acknowledged.clear()
                accepted = False
                try:
                    async with asyncio.timeout(MQTT_CONNECT_TIMEOUT):
                        await driver.async_connect(host, port)
                        await acknowledged.wait()
                    if not accepted:
                        raise ConnectionError("MQTT connection was not accepted")
                    delay = 1
                    await disconnected.wait()
                except OSError as error:
                    _LOGGER.debug(
                        "MQTT connection attempt failed (%s)", type(error).__name__
                    )
                finally:
                    await driver.async_close()
                if not self._closed and not self._owner._closing:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 15)
        finally:
            client.on_connect = original_connect
            client.on_disconnect = original_disconnect

    async def async_close(self) -> None:
        """Prevent new requests and drain the owned connection task."""
        self._closed = True
        task = self._task
        if task is None:
            return
        task.cancel()
        interrupted = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.done():
                    interrupted = True
        if not task.cancelled():
            task.result()
        if interrupted:
            raise asyncio.CancelledError
