"""Home Assistant stream lifetime helpers for owned mower video sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from typing import Any, Protocol

from homeassistant.core import HomeAssistant

from .dreame_lawn_mower_client.runtime_state import snapshot_session_control_state
from .ha_tasks import create_background_task

_DEFAULT_PROVIDER_GRACE = 30.0
_DEFAULT_IDLE_GRACE = 15.0
_DEFAULT_IDLE_POLL_INTERVAL = 1.0


def mower_video_session_should_stay_warm(snapshot: Any) -> bool:
    """Return whether an already-started video session should stay warm."""
    if snapshot is None:
        return False
    state = str(getattr(snapshot, "state", None) or "").casefold()
    activity = str(getattr(snapshot, "activity", None) or "").casefold()
    if (
        bool(getattr(snapshot, "docked", False))
        or state in {"charging", "charging_completed"}
        or activity in {"docked", "charging", "charging_completed"}
    ):
        return False
    if getattr(snapshot, "mowing_session_active", None) is not None:
        return snapshot_session_control_state(snapshot) == "mowing"
    return (
        bool(getattr(snapshot, "mowing", False))
        or state == "mowing"
        or activity == "mowing"
    )


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
        has_external_consumers: Callable[[], bool] | None = None,
        should_stay_warm: Callable[[], bool] | None = None,
        provider_grace: float = _DEFAULT_PROVIDER_GRACE,
        idle_grace: float = _DEFAULT_IDLE_GRACE,
        poll_interval: float = _DEFAULT_IDLE_POLL_INTERVAL,
    ) -> None:
        self._hass = hass
        self._stream_lock = stream_lock
        self._is_current = is_current
        self._stop_active = stop_active
        self._has_external_consumers = has_external_consumers or (lambda: False)
        self._should_stay_warm = should_stay_warm or (lambda: False)
        self._provider_grace = provider_grace
        self._idle_grace = idle_grace
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None

    def schedule(self, ha_stream: _HaStream, session: object) -> None:
        """Watch one HA stream/session pair, replacing an older watcher."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        self._task = create_background_task(
            self._hass,
            self._async_watch(ha_stream, session),
            "dreame-lawn-mower-video-idle-monitor",
        )

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
                stop_due = False
                if (
                    ha_stream.outputs()
                    or self._has_external_consumers()
                    or self._should_stay_warm()
                ):
                    provider_seen = True
                    idle_deadline = None
                else:
                    now = asyncio.get_running_loop().time()
                    if provider_seen:
                        idle_deadline = idle_deadline or now + self._idle_grace
                        if now >= idle_deadline:
                            stop_due = True
                    elif now >= provider_deadline:
                        stop_due = True
                if stop_due:
                    async with self._stream_lock:
                        if (
                            self._is_current(ha_stream, session)
                            and not ha_stream.outputs()
                            and not self._has_external_consumers()
                            and not self._should_stay_warm()
                        ):
                            await self._stop_active()
                            return
                    idle_deadline = None
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            raise
        finally:
            if self._task is current_task:
                self._task = None
