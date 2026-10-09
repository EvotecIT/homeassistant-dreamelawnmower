"""Drive an owned Paho connection through the client's asyncio loop."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

import paho.mqtt.client as mqtt


class _MqttSocket(Protocol):
    def fileno(self) -> int: ...


class MqttEventLoop:
    """Own readiness registrations and keepalive for one Paho client.

    Blocking connection setup is retained until it finishes, including after
    cancellation. No persistent Paho network thread is started.
    """

    def __init__(self, client: mqtt.Client) -> None:
        self._loop = asyncio.get_running_loop()
        self._client = client
        self._socket: _MqttSocket | None = None
        self._fd: int | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._closed = False
        self._disconnected = False
        self._connecting: asyncio.Task[Any] | None = None
        client.on_socket_open = self._on_open
        client.on_socket_close = self._on_close
        client.on_socket_register_write = self._on_write
        client.on_socket_unregister_write = self._on_stop_write

    async def async_connect(self, host: str, port: int, keepalive: int = 50) -> None:
        """Connect off-loop, disposing late completion before cancellation returns."""
        if self._closed or self._connecting is not None:
            raise RuntimeError("MQTT connection owner is closed or already connecting")
        task = asyncio.create_task(
            asyncio.to_thread(self._client.connect, host, port, keepalive)
        )
        self._connecting = task
        try:
            result = await asyncio.shield(task)
            if self._closed:
                raise RuntimeError("MQTT connection owner closed during connection")
            if result != mqtt.MQTT_ERR_SUCCESS:
                raise OSError(f"MQTT connection failed with code {result}")
        except BaseException:
            self._closed = True
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()
            self._disconnect()
            raise
        finally:
            self._connecting = None

    def _disconnect(self) -> None:
        try:
            if not self._disconnected:
                self._disconnected = True
                self._client.disconnect()
                self._client.loop_write()
        finally:
            self.close()
            # A congested peer must not retain an owned socket after shutdown.
            sock = self._client.socket()
            if sock is not None:
                sock.close()

    async def async_close(self) -> None:
        """Drain an active connection before disposal, even if close is cancelled."""
        self._closed = True
        task = self._connecting
        interrupted = False
        if task is not None:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    interrupted = True
                except Exception:
                    break
            if not task.cancelled():
                task.exception()
        self._disconnect()
        if interrupted:
            raise asyncio.CancelledError

    def _dispatch(
        self, callback: Callable[[_MqttSocket], None], sock: _MqttSocket
    ) -> None:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._loop:
            callback(sock)
        else:
            self._loop.call_soon_threadsafe(callback, sock)

    def _on_open(self, client: mqtt.Client, userdata: Any, sock: _MqttSocket) -> None:
        self._dispatch(self._open, sock)

    def _on_close(self, client: mqtt.Client, userdata: Any, sock: _MqttSocket) -> None:
        self._dispatch(self._remove, sock)

    def _on_write(self, client: mqtt.Client, userdata: Any, sock: _MqttSocket) -> None:
        self._dispatch(self._write, sock)

    def _on_stop_write(
        self, client: mqtt.Client, userdata: Any, sock: _MqttSocket
    ) -> None:
        self._dispatch(self._stop_write, sock)

    def _open(self, sock: _MqttSocket) -> None:
        if self._closed or sock.fileno() < 0:
            return
        if self._socket is not None:
            self._remove(self._socket)
        self._socket = sock
        self._fd = sock.fileno()
        self._loop.add_reader(self._fd, self._read, sock)
        self._timer = self._loop.call_later(1, self._misc, sock)

    def _read(self, sock: _MqttSocket) -> None:
        if not self._closed and self._socket is sock:
            self._client.loop_read()

    def _write(self, sock: _MqttSocket) -> None:
        if not self._closed and self._socket is sock and self._fd is not None:
            self._loop.add_writer(self._fd, self._flush, sock)

    def _flush(self, sock: _MqttSocket) -> None:
        if not self._closed and self._socket is sock:
            self._client.loop_write()

    def _stop_write(self, sock: _MqttSocket) -> None:
        if self._socket is sock and self._fd is not None:
            self._loop.remove_writer(self._fd)

    def _misc(self, sock: _MqttSocket) -> None:
        self._timer = None
        if not self._closed and self._socket is sock:
            if self._client.loop_misc() == mqtt.MQTT_ERR_SUCCESS:
                self._timer = self._loop.call_later(1, self._misc, sock)

    def _remove(self, sock: _MqttSocket) -> None:
        if self._socket is not sock:
            return
        if self._fd is not None:
            self._loop.remove_reader(self._fd)
            self._loop.remove_writer(self._fd)
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._socket = None
        self._fd = None

    def close(self) -> None:
        """Remove loop resources after the caller drains connection attempts."""
        self._closed = True
        if self._socket is not None:
            self._remove(self._socket)
