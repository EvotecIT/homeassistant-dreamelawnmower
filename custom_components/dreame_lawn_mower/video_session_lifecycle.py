"""Home Assistant stream lifetime helpers for owned mower video sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from typing import Any, Protocol

from homeassistant.core import HomeAssistant

_DEFAULT_PROVIDER_GRACE = 30.0
_DEFAULT_IDLE_GRACE = 15.0
_DEFAULT_IDLE_POLL_INTERVAL = 1.0


class _HaStream(Protocol):
    """Small public HA Stream surface needed by the idle monitor."""

    def outputs(self) -> Mapping[str, Any]:
        """Return active output providers."""


class DreameLawnMowerHaStreamIdleMonitor:
    """Stop an owned XP2P session when HA removes its last output provider."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        stream_lock: asyncio.Lock,
        is_current: Callable[[_HaStream, object], bool],
        stop_active: Callable[[], Awaitable[None]],
        provider_grace: float = _DEFAULT_PROVIDER_GRACE,
        idle_grace: float = _DEFAULT_IDLE_GRACE,
        poll_interval: float = _DEFAULT_IDLE_POLL_INTERVAL,
    ) -> None:
        self._hass = hass
        self._stream_lock = stream_lock
        self._is_current = is_current
        self._stop_active = stop_active
        self._provider_grace = provider_grace
        self._idle_grace = idle_grace
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None

    def schedule(self, ha_stream: _HaStream, session: object) -> None:
        """Watch one HA stream/session pair, replacing an older watcher."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        self._task = self._hass.async_create_task(self._async_watch(ha_stream, session))

    async def async_cancel(self) -> None:
        """Cancel the current watcher without cancelling itself during cleanup."""
        task = self._task
        self._task = None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _async_watch(self, ha_stream: _HaStream, session: object) -> None:
        current_task = asyncio.current_task()
        provider_seen = False
        provider_deadline = asyncio.get_running_loop().time() + self._provider_grace
        idle_deadline: float | None = None
        try:
            while self._is_current(ha_stream, session):
                if ha_stream.outputs():
                    provider_seen = True
                    idle_deadline = None
                else:
                    now = asyncio.get_running_loop().time()
                    if provider_seen:
                        idle_deadline = idle_deadline or now + self._idle_grace
                        if now >= idle_deadline:
                            break
                    elif now >= provider_deadline:
                        break
                await asyncio.sleep(self._poll_interval)

            async with self._stream_lock:
                if self._is_current(ha_stream, session) and not ha_stream.outputs():
                    await self._stop_active()
        except asyncio.CancelledError:
            raise
        finally:
            if self._task is current_task:
                self._task = None
