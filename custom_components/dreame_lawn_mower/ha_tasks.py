"""Home Assistant task ownership helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from homeassistant.core import HomeAssistant


def create_background_task[T](
    hass: HomeAssistant,
    coroutine: Coroutine[Any, Any, T],
    name: str,
) -> asyncio.Task[T]:
    """Create intentionally long-lived work outside config-entry setup tracking."""
    create = getattr(hass, "async_create_background_task", None)
    if callable(create):
        return create(coroutine, name)

    # Keep lightweight test doubles and older Home Assistant builds usable.
    create = hass.async_create_task
    try:
        return create(coroutine, name)
    except TypeError:
        return create(coroutine)
