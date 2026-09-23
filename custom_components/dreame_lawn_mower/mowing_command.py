"""Home Assistant error semantics for mower movement commands."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .dreame_lawn_mower_client import DreameLawnMowerConnectionError


async def async_run_mowing_command(command: Awaitable[Any]) -> Any:
    """Surface expected mower rejection and connection failures to HA users."""
    try:
        return await command
    except DreameLawnMowerConnectionError as err:
        raise HomeAssistantError(str(err)) from err
