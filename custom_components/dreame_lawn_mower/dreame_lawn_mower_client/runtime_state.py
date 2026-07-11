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
    """Apply heartbeat-confirmed mowing-session state to a snapshot."""
    task_status = status_blob.task_status
    if task_status is None:
        return snapshot
    return replace(
        snapshot,
        task_status=task_status,
        task_status_name=task_status.replace("_", " ").title(),
        task_status_source=f"heartbeat_{status_blob.source or 'unknown'}",
        mowing_session_active=status_blob.mowing_session_active,
        task_resumable=status_blob.task_resumable,
    )


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
