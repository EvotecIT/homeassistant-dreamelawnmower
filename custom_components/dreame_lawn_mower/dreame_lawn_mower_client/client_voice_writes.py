"""Native voice writes using the shared command and confirmation owners."""

from __future__ import annotations

import time
from collections.abc import Generator, Mapping
from typing import TYPE_CHECKING, Any

from .client_app_reads import async_command_app_action

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient


async def async_write_voice(
    client: DreameLawnMowerClient,
    plan: Generator[Mapping[str, Any], Any, dict[str, Any]],
) -> dict[str, Any]:
    """Validate before dispatch and never replay an uncertain settings write."""
    try:
        action = next(plan)
        response = await async_command_app_action(
            client, action, deadline=time.monotonic() + 20
        )
        try:
            plan.send(response)
        except StopIteration as completed:
            result: dict[str, Any] = completed.value
            return result
        raise RuntimeError("Voice write emitted more than one command")
    finally:
        plan.close()
