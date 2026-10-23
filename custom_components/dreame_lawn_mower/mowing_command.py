"""Home Assistant error semantics for mower movement commands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .dreame_lawn_mower_client import DreameLawnMowerConnectionError


def cancel_managed_mowing(coordinator: Any) -> None:
    """Revoke automatic recovery when a resolved HA control reaches its owner."""
    manager = getattr(coordinator, "managed_mowing", None)
    if manager is not None:
        manager.cancel()


def scheduled_control_fields(
    require_inactive_task: bool, admission: Callable[[], bool] | None,
) -> dict[str, Any]:
    """Map optional scheduled safeguards without changing ordinary calls."""
    fields: dict[str, Any] = {}
    if require_inactive_task:
        fields["require_inactive_task"] = True
    if admission is not None:
        fields["admission"] = admission
    return fields


async def async_run_mowing_command(command: Awaitable[Any]) -> Any:
    """Surface expected mower rejection and connection failures to HA users."""
    try:
        return await command
    except DreameLawnMowerConnectionError as err:
        raise HomeAssistantError(str(err)) from err
