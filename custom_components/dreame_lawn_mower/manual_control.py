"""Manual-control safety helpers for Dreame lawn mower."""

from __future__ import annotations

from .dreame_lawn_mower_client.models import (
    remote_control_block_reason,
    remote_control_state_safe,
)
from .dreame_lawn_mower_client.runtime_state import snapshot_session_control_state

__all__ = [
    "maintenance_point_movement_block_reason",
    "remote_control_block_reason",
    "remote_control_state_safe",
]


def maintenance_point_movement_block_reason(snapshot: object) -> str | None:
    """Return why configured maintenance-point movement is unsafe."""
    if snapshot is not None and not getattr(snapshot, "available", True):
        return "Mower is not available."

    block_reason = remote_control_block_reason(snapshot)
    if block_reason is not None:
        return block_reason

    session_active = getattr(snapshot, "mowing_session_active", None)
    control_state = (
        snapshot_session_control_state(snapshot)
        if session_active is not None
        else str(getattr(snapshot, "activity", "") or "").casefold()
    )
    if control_state not in {"idle", "docked"}:
        return (
            "The mower must be idle or docked before going to a maintenance point."
        )
    return None
