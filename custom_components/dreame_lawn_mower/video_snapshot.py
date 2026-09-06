"""Coalesce camera snapshot work independently of HTTP request cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from time import monotonic
from typing import Any

from homeassistant.core import HomeAssistant

from .ha_tasks import create_background_task


class VideoSnapshotRequest:
    """Own one bounded decoder request until completion or explicit shutdown.

    Home Assistant can cancel a camera proxy request before a cold video source
    is ready. That client lifetime must not repeatedly tear down the sole FLV
    decoder. Concurrent callers share the pending frame (and its dimensions);
    subsequent requests can select different dimensions after it completes.
    The operation itself owns the startup/image/cleanup timeout budgets.
    """

    def __init__(self) -> None:
        """Create an idle owner without starting any media work."""
        self._task: asyncio.Task[bytes | None] | None = None
        self._completed_at: float | None = None

    async def async_get(
        self,
        hass: HomeAssistant,
        operation: Callable[[], Coroutine[Any, Any, bytes | None]],
    ) -> bytes | None:
        """Share the current bounded operation without inheriting HTTP cancellation."""
        task = self._task
        if task is None or (
            task.done() and (
                self._completed_at is None or monotonic() - self._completed_at > 5
            )
        ):
            task = create_background_task(hass, operation(), "dreame-video-snapshot")
            self._task = task
            self._completed_at = None
            task.add_done_callback(self._consume_exception)
        try:
            return await asyncio.shield(task)
        finally:
            # A cancelled HTTP waiter leaves its decoder/result available for a
            # prompt retry, but successful callers do not cache future snapshots.
            if task.done() and self._task is task:
                self._task = None

    def _consume_exception(self, task: asyncio.Task[bytes | None]) -> None:
        """Retrieve failures even when every HTTP waiter has disconnected."""
        if not task.cancelled():
            task.exception()
        if self._task is task:
            self._completed_at = monotonic()

    async def async_cancel(self) -> None:
        """Cancel and drain owned work before closing the camera lifecycle."""
        task = self._task
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if self._task is task:
            self._task = None
