"""Normalize live mower task and cloud-presence evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .models import DreameLawnMowerSnapshot, DreameLawnMowerStatusBlob

RESUME_MOWING_REQUEST = {"m": "a", "p": 0, "o": 5}

_ACTIVE_TASK_CONTROL_STATES = {
    "starting": "mowing",
    "mowing": "mowing",
    "paused": "paused",
    "returning_to_dock": "returning",
}


def snapshot_with_heartbeat_task_state(
    snapshot: DreameLawnMowerSnapshot,
    status_blob: DreameLawnMowerStatusBlob,
) -> DreameLawnMowerSnapshot:
    """Apply heartbeat-confirmed task and physical docking state."""
    task_status = status_blob.task_status
    changes: dict[str, Any] = {}
    if task_status is not None:
        changes.update(
            task_status=task_status,
            task_status_name=task_status.replace("_", " ").title(),
            task_status_source=f"heartbeat_{status_blob.source or 'unknown'}",
            mowing_session_active=status_blob.mowing_session_active,
            task_resumable=status_blob.task_resumable,
        )

    if status_blob.heartbeat_docked is True:
        changes.update(raw_docked=True, docked=True)
        stale_paused_state = snapshot.state in {"paused", "monitoring_paused"}
        if (
            status_blob.mowing_session_active is False
            and snapshot.activity == "paused"
            and stale_paused_state
        ):
            # Some firmware leaves property 2.1 at a paused/event state while
            # the heartbeat and app both report an idle mower in the station.
            changes.update(
                state="idle",
                state_name="idle",
                activity="docked",
                started=False,
                paused=False,
                mowing=False,
                returning=False,
            )

    if not changes:
        return snapshot
    return replace(snapshot, **changes)


def snapshot_with_cloud_presence(
    snapshot: DreameLawnMowerSnapshot,
    cloud_device_info: Mapping[str, Any],
) -> DreameLawnMowerSnapshot:
    """Apply authoritative cloud presence without exposing cached state as live."""
    online = _optional_bool(cloud_device_info.get("online"))
    if online is None:
        return snapshot
    return replace(
        snapshot,
        online=online,
        available=snapshot.available and online,
    )


def snapshot_session_control_state(snapshot: DreameLawnMowerSnapshot) -> str:
    """Return the authoritative state used for session-ending commands."""
    session_active = snapshot.mowing_session_active
    if session_active is False:
        return "idle"
    if session_active is True:
        return _ACTIVE_TASK_CONTROL_STATES.get(snapshot.task_status or "", "mowing")
    return snapshot.state


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "on", "yes", "1"}:
            return True
        if normalized in {"false", "off", "no", "0"}:
            return False
        return None
    return bool(value)
