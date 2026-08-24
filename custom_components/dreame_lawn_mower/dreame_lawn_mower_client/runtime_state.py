"""Normalize live mower task and cloud-presence evidence."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from .models import DreameLawnMowerSnapshot, DreameLawnMowerStatusBlob

RESUME_MOWING_REQUEST = {"m": "a", "p": 0, "o": 5}

_INACTIVE_HEARTBEAT_MAX_AGE_SECONDS = 130.0
# Supervised A3 captures show unchanged 22-byte standby frames repeating every
# five minutes. Keep one coordinator interval of delivery margin while newer
# property/session evidence below remains authoritative.
_A3_IDLE_HEARTBEAT_MAX_AGE_SECONDS = 365.0
_HEARTBEAT_CLOCK_SKEW_SECONDS = 5.0
# ISO timestamps retain microseconds while vendor reception clocks can expose
# a slightly finer float. Treat only that serialization quantum as equal.
_EVENT_ORDERING_TOLERANCE_SECONDS = 0.000001

_ACTIVE_TASK_CONTROL_STATES = {
    "starting": "mowing",
    "mowing": "mowing",
    "paused": "paused",
    "returning_to_dock": "returning",
}


def snapshot_with_heartbeat_task_state(
    snapshot: DreameLawnMowerSnapshot,
    status_blob: DreameLawnMowerStatusBlob,
    *,
    observed_at: float | None = None,
    active_state_observed_at: float | None = None,
) -> DreameLawnMowerSnapshot:
    """Apply heartbeat-confirmed task and physical docking state."""
    task_status = status_blob.task_status
    heartbeat_may_weaken_active_state = (
        status_blob.mowing_session_active is False
        or (
            status_blob.heartbeat_docked is True
            and status_blob.mowing_session_active is not True
        )
    )
    if heartbeat_may_weaken_active_state and not _inactive_heartbeat_is_current(
        snapshot,
        status_blob,
        observed_at=observed_at,
        active_state_observed_at=active_state_observed_at,
    ):
        # A cached inactive heartbeat must never clear newer or unorderable
        # paused/running evidence. Active heartbeat evidence remains fail-closed.
        return snapshot

    changes: dict[str, Any] = {}
    if status_blob.candidate_runtime_task_id is not None:
        changes["mission_task_id"] = status_blob.candidate_runtime_task_id
    if task_status is not None:
        changes.update(
            task_status=task_status,
            task_status_name=task_status.replace("_", " ").title(),
            task_status_source=f"heartbeat_{status_blob.source or 'unknown'}",
            task_status_event_at=_heartbeat_event_at(status_blob),
            mowing_session_active=status_blob.mowing_session_active,
            task_resumable=status_blob.task_resumable,
        )
    if status_blob.mowing_session_active is False:
        # Native task metadata belongs only to the active mowing session that
        # produced it. Do not let a retained TASK property make an idle mower
        # look as though it is still executing the previous targeted request.
        changes.update(task_operation=None, task_region_ids=None, task_area_ids=None)

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


def _heartbeat_event_at(status_blob: DreameLawnMowerStatusBlob) -> float | None:
    """Return the heartbeat property's own reception time when available."""
    received_at = getattr(status_blob, "received_at", None)
    if isinstance(received_at, int | float) and not isinstance(received_at, bool):
        return float(received_at)
    if not isinstance(received_at, str):
        return None
    try:
        parsed = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _inactive_heartbeat_is_current(
    snapshot: DreameLawnMowerSnapshot,
    status_blob: DreameLawnMowerStatusBlob,
    *,
    observed_at: float | None,
    active_state_observed_at: float | None,
) -> bool:
    """Return whether inactive heartbeat evidence is fresh and correctly ordered."""
    heartbeat_event_at = _heartbeat_event_at(status_blob)
    if heartbeat_event_at is None:
        return False

    now = time.time() if observed_at is None else float(observed_at)
    if heartbeat_event_at > now + _HEARTBEAT_CLOCK_SKEW_SECONDS:
        return False
    heartbeat_max_age = (
        _A3_IDLE_HEARTBEAT_MAX_AGE_SECONDS
        if _is_a3_idle_heartbeat(status_blob)
        else _INACTIVE_HEARTBEAT_MAX_AGE_SECONDS
    )
    if now - heartbeat_event_at > heartbeat_max_age:
        return False

    state_event_at = _numeric_event_at(snapshot.state_event_at)
    task_event_at = _numeric_event_at(snapshot.task_status_event_at)
    active_observed_at = _numeric_event_at(active_state_observed_at)
    physical_state_active = bool(
        snapshot.activity in {"mowing", "paused", "returning"}
        or snapshot.paused
        or snapshot.mowing
        or snapshot.returning
    )
    task_state_active = bool(
        snapshot.mowing_session_active is True
        or snapshot.task_status in _ACTIVE_TASK_CONTROL_STATES
    )
    if (
        (physical_state_active and state_event_at is None)
        or (task_state_active and task_event_at is None)
    ) and active_observed_at is None:
        return False

    event_times = [state_event_at, task_event_at]
    if (physical_state_active and state_event_at is None) or (
        task_state_active and task_event_at is None
    ):
        event_times.append(active_observed_at)
    for event_at in event_times:
        if (
            event_at is not None
            and event_at > heartbeat_event_at + _EVENT_ORDERING_TOLERANCE_SECONDS
        ):
            return False
    return True


def _is_a3_idle_heartbeat(status_blob: DreameLawnMowerStatusBlob) -> bool:
    """Return whether this is the supervised 22-byte A3 standby frame."""
    return bool(
        status_blob.length == 22
        and status_blob.frame_valid
        and status_blob.main_state == 0
        and status_blob.sub_state == 0xFF
        and status_blob.task_status == "idle"
        and status_blob.mowing_session_active is False
        and status_blob.heartbeat_docked is True
    )


def _numeric_event_at(value: Any) -> float | None:
    """Return a finite event timestamp without accepting booleans."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


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
