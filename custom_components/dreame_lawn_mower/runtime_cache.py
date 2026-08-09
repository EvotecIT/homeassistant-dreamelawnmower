"""Last-known runtime mission telemetry for dashboard continuity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_SESSION_METRIC_FIELDS = (
    "candidate_runtime_progress_percent",
    "candidate_runtime_area_progress_percent",
    "candidate_runtime_current_area_sqm",
    "candidate_runtime_total_area_sqm",
)
_COMPLETED_TASK_STATUSES = frozenset({"finished"})
_COMPLETED_STATUS_NOTICES = frozenset({"mowing_task_completed"})
_NEW_SESSION_TASK_STATUSES = frozenset({"starting"})
_NEW_SESSION_STATUS_NOTICES = frozenset(
    {"mowing_started", "mowing_task_started", "scheduled_mowing_started"}
)
_RESUMABLE_TASK_STATUSES = frozenset({"paused"})
_RESUME_STATUS_NOTICES = frozenset(
    {"mowing_resumed_after_charging", "resuming_unfinished_task"}
)


def _session_metric_signature(blob: Any) -> tuple[Any, ...]:
    """Return the mission values that identify a changed telemetry snapshot."""
    return tuple(getattr(blob, field, None) for field in _SESSION_METRIC_FIELDS)


def runtime_blob_has_session_metrics(blob: Any) -> bool:
    """Return whether a runtime payload contains useful mission telemetry."""
    if blob is None:
        return False
    return any(
        isinstance(getattr(blob, field, None), int | float)
        for field in _SESSION_METRIC_FIELDS
    )


def runtime_blob_has_nonzero_session_metrics(blob: Any) -> bool:
    """Return whether a runtime payload contains nonzero mission telemetry."""
    if blob is None:
        return False
    return any(
        isinstance(value := getattr(blob, field, None), int | float) and value != 0
        for field in _SESSION_METRIC_FIELDS
    )


def runtime_mission_completion_confirmed(
    snapshot: Any,
    *,
    tracking_active: bool | None,
    cached_completion_confirmed: bool = False,
) -> bool:
    """Return whether an inactive mission has an explicit completion signal."""
    mission_active = runtime_mission_session_active(
        snapshot,
        tracking_active=tracking_active,
    )
    if snapshot is None or mission_active is True:
        return False
    if mission_active is None:
        return cached_completion_confirmed
    return (
        cached_completion_confirmed
        or getattr(snapshot, "task_status", None) in _COMPLETED_TASK_STATUSES
        or getattr(snapshot, "status_notice_name", None) in _COMPLETED_STATUS_NOTICES
    )


def runtime_mission_session_active(
    snapshot: Any,
    *,
    tracking_active: bool | None,
) -> bool | None:
    """Return whether telemetry belongs to a continuing mower mission.

    ``None`` preserves the prior cache boundary when a docked device snapshot
    is missing the optional heartbeat fields that distinguish a charging pause
    from an inactive mower.
    """
    if snapshot is None:
        return tracking_active
    session_active = getattr(snapshot, "mowing_session_active", None)
    if session_active is not None:
        return bool(session_active)
    if getattr(snapshot, "task_resumable", None) is True:
        return True
    if getattr(snapshot, "task_status", None) in _RESUMABLE_TASK_STATUSES:
        return True
    if getattr(snapshot, "status_notice_name", None) in _RESUME_STATUS_NOTICES:
        return True
    if runtime_mission_new_session(snapshot):
        return True
    if tracking_active:
        return True
    if getattr(snapshot, "task_status", None) in _COMPLETED_TASK_STATUSES:
        return False
    if getattr(snapshot, "docked", False) or getattr(snapshot, "state", None) in {
        "charging",
        "charging_completed",
    }:
        return None
    return False


def runtime_mission_new_session(snapshot: Any) -> bool:
    """Return whether a snapshot explicitly announces a fresh mower mission."""
    if snapshot is None:
        return False
    return (
        getattr(snapshot, "task_status", None) in _NEW_SESSION_TASK_STATUSES
        or getattr(snapshot, "status_notice_name", None)
        in _NEW_SESSION_STATUS_NOTICES
    )


def runtime_mission_progress_percent(
    blob: Any,
    *,
    completion_confirmed: bool,
) -> float | int | None:
    """Return measured mission progress, normalized after confirmed completion."""
    area_progress = getattr(blob, "candidate_runtime_area_progress_percent", None)
    progress = getattr(blob, "candidate_runtime_progress_percent", None)
    measured = area_progress if isinstance(area_progress, int | float) else progress
    if not isinstance(measured, int | float):
        return None
    if completion_confirmed:
        return 100.0
    return measured


@dataclass(slots=True)
class DreameLawnMowerRuntimeTelemetryCache:
    """Preserve the latest useful mission metrics after a session ends."""

    blob: Any = None
    captured_at: datetime | None = None
    completion_confirmed: bool = False
    _metric_signature: tuple[Any, ...] | None = None
    _session_active: bool = False
    _new_session_signal_seen: bool = False
    _new_session_pending_activation: bool = False

    def _invalidate_for_new_session(self) -> None:
        self.blob = None
        self.captured_at = None
        self.completion_confirmed = False
        self._metric_signature = None
        self._session_active = True
        self._new_session_pending_activation = False

    def begin_new_session(self) -> None:
        """Invalidate prior telemetry after a fresh mission is accepted."""
        self._invalidate_for_new_session()
        self._new_session_signal_seen = True
        self._new_session_pending_activation = True

    def observe_session_state(
        self,
        *,
        active_session: bool | None,
        completion_confirmed: bool = False,
        new_session: bool = False,
    ) -> None:
        """Apply authoritative mission state before optional telemetry work."""
        if new_session and not self._new_session_signal_seen:
            self._invalidate_for_new_session()
        if new_session:
            self._new_session_signal_seen = True
            self._new_session_pending_activation = False
            active_session = True
        if active_session is None:
            return
        if active_session:
            if not self._session_active:
                self._invalidate_for_new_session()
            self.completion_confirmed = False
            self._session_active = True
            self._new_session_pending_activation = False
            return
        if self._new_session_pending_activation and not completion_confirmed:
            return
        self._session_active = False
        self._new_session_signal_seen = False
        self._new_session_pending_activation = False
        if completion_confirmed:
            self.completion_confirmed = True

    def update(
        self,
        blob: Any,
        *,
        now: datetime | None = None,
        allow_zero: bool = True,
        active_session: bool | None = False,
        completion_confirmed: bool = False,
        new_session: bool = False,
    ) -> bool:
        """Store a useful runtime payload without erasing it with empty polls."""
        self.observe_session_state(
            active_session=active_session,
            completion_confirmed=completion_confirmed,
            new_session=new_session,
        )
        if not runtime_blob_has_session_metrics(blob):
            return False
        if not allow_zero and not runtime_blob_has_nonzero_session_metrics(blob):
            return False
        signature = _session_metric_signature(blob)
        if signature == self._metric_signature:
            self.blob = blob
            if active_session:
                self.captured_at = now or datetime.now(UTC)
                return True
            return False
        self.blob = blob
        self.captured_at = now or datetime.now(UTC)
        self._metric_signature = signature
        return True


def observe_runtime_session_state(
    cache: Any,
    *,
    active_session: bool | None,
    completion_confirmed: bool = False,
    new_session: bool = False,
) -> None:
    """Apply authoritative session state when the integration owns this cache."""
    if isinstance(cache, DreameLawnMowerRuntimeTelemetryCache):
        cache.observe_session_state(
            active_session=active_session,
            completion_confirmed=completion_confirmed,
            new_session=new_session,
        )


def begin_runtime_mission_session(cache: Any) -> None:
    """Invalidate prior telemetry when an integration-owned start succeeds."""
    if isinstance(cache, DreameLawnMowerRuntimeTelemetryCache):
        cache.begin_new_session()
