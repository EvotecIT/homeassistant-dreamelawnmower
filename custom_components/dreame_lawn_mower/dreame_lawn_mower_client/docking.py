"""Safe docking orchestration for Dreame lawn mowers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

_LOGGER = logging.getLogger(__name__)

ACTIVE_DOCKING_STATES = frozenset(
    {
        "mowing",
        "remote_control",
        "clean_summon",
        "second_cleaning",
        "human_following",
        "spot_cleaning",
        "shortcut",
        "monitoring",
    }
)

AsyncCommand = Callable[[], Awaitable[object]]
AsyncStateReader = Callable[[], Awaitable[str | None]]


async def async_stop_then_dock(
    *,
    initial_state: str | None,
    stop: AsyncCommand,
    dock: AsyncCommand,
    refresh_state: AsyncStateReader,
    timeout: float = 20.0,
    initial_delay: float = 0.6,
    poll_interval: float = 2.0,
) -> bool:
    """End an active mowing session before sending the mower to its dock.

    Returns whether an active session was observed to stop. A timeout does not
    prevent docking because returning the mower remains the safer final action.
    """

    state = _normalize_state(initial_state)
    if state not in ACTIVE_DOCKING_STATES:
        await dock()
        return True

    await stop()
    stopped = False
    try:
        async with asyncio.timeout(timeout):
            if initial_delay > 0:
                await asyncio.sleep(initial_delay)
            while True:
                state = _normalize_state(await refresh_state())
                if state not in ACTIVE_DOCKING_STATES:
                    stopped = True
                    break
                await asyncio.sleep(max(poll_interval, 0))
    except TimeoutError:
        _LOGGER.warning(
            "Timed out waiting for mower session state %s to stop; docking anyway.",
            initial_state,
        )

    await dock()
    return stopped


def _normalize_state(value: object) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", value)
    text = str(name).strip().lower()
    return text or None
