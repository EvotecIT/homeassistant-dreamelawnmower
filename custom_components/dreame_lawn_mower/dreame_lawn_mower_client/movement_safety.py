"""Safety decisions for starting autonomous movement without mowing."""

from __future__ import annotations

from .models import (
    REMOTE_CONTROL_STATES,
    DreameLawnMowerSnapshot,
    remote_control_block_reason,
)
from .runtime_state import snapshot_session_control_state


def stationary_movement_block_reason(
    snapshot: DreameLawnMowerSnapshot | None,
) -> str | None:
    """Require an idle or docked mower before starting autonomous movement."""
    if snapshot is not None and not getattr(snapshot, "available", True):
        return "Mower is not available."
    block_reason = remote_control_block_reason(snapshot)
    if block_reason is not None:
        return block_reason

    state = str(getattr(snapshot, "state", "") or "").casefold()
    activity = str(getattr(snapshot, "activity", "") or "").casefold()
    if state in REMOTE_CONTROL_STATES or activity in REMOTE_CONTROL_STATES:
        return "Finish manual remote control before starting another movement."

    session_active = getattr(snapshot, "mowing_session_active", None)
    control_state = (
        snapshot_session_control_state(snapshot)
        if snapshot is not None and session_active is not None
        else activity
    )
    if control_state not in {"idle", "docked"}:
        return "The mower must be idle or docked before moving."
    return None
