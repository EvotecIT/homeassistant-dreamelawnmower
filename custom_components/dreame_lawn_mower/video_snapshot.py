"""Coalesce camera snapshot work independently of HTTP request cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
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

    async def async_get(
        self,
        hass: HomeAssistant,
        operation: Callable[[], Coroutine[Any, Any, bytes | None]],
        *,
        cached_image: bytes | None = None,
    ) -> bytes | None:
        """Share the current bounded operation without inheriting HTTP cancellation."""
        task = self._task
        if task is None or task.done():
            task = create_background_task(hass, operation(), "dreame-video-snapshot")
            self._task = task
            task.add_done_callback(self._consume_exception)
        if cached_image is not None:
            return cached_image
        return await asyncio.shield(task)

    @staticmethod
    def _consume_exception(task: asyncio.Task[bytes | None]) -> None:
        """Retrieve failures even when every HTTP waiter has disconnected."""
        if not task.cancelled():
            task.exception()

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
