"""Drain HA video cache writes before releasing their lifecycle owner."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import CoreState
from homeassistant.helpers.storage import Store


async def async_save_video_cache(
    store: Store[dict[str, Any]], payload: dict[str, Any],
) -> None:
    """Keep cancellation from abandoning a disk write behind entry removal."""
    async def write() -> None:
        await store.async_save(payload)
        if store.hass.state is CoreState.stopping:
            # Both supported HA versions defer stopping-state writes. Drain the
            # untyped HA method just as the observation checkpoint owner does.
            await store._async_handle_write_data()  # type: ignore[no-untyped-call]

    task = asyncio.create_task(write())
    cancelled: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as err:
            cancelled = err
    task.result()
    if cancelled is not None:
        raise cancelled
