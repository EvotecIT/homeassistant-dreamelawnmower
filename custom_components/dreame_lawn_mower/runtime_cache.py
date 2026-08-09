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
    tracking_active: bool,
    cached_completion_confirmed: bool = False,
) -> bool:
    """Return whether an inactive mission has an explicit completion signal."""
    if snapshot is None or tracking_active:
        return False
    return (
        cached_completion_confirmed
        or getattr(snapshot, "task_status", None) in _COMPLETED_TASK_STATUSES
        or getattr(snapshot, "status_notice_name", None) in _COMPLETED_STATUS_NOTICES
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

    def observe_session_state(
        self,
        *,
        active_session: bool,
        completion_confirmed: bool = False,
    ) -> None:
        """Apply authoritative mission state before optional telemetry work."""
        if active_session:
            if not self._session_active:
                self.blob = None
                self.captured_at = None
                self._metric_signature = None
            self.completion_confirmed = False
            self._session_active = True
            return
        self._session_active = False
        if completion_confirmed:
            self.completion_confirmed = True

    def update(
        self,
        blob: Any,
        *,
        now: datetime | None = None,
        allow_zero: bool = True,
        active_session: bool = False,
        completion_confirmed: bool = False,
    ) -> bool:
        """Store a useful runtime payload without erasing it with empty polls."""
        self.observe_session_state(
            active_session=active_session,
            completion_confirmed=completion_confirmed,
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
    active_session: bool,
    completion_confirmed: bool = False,
) -> None:
    """Apply authoritative session state when the integration owns this cache."""
    if isinstance(cache, DreameLawnMowerRuntimeTelemetryCache):
        cache.observe_session_state(
            active_session=active_session,
            completion_confirmed=completion_confirmed,
        )
