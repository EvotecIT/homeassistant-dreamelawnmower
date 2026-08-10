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
_UNSUCCESSFUL_TASK_STATUSES = frozenset({"failed", "exit"})
_TERMINAL_TASK_STATUSES = _COMPLETED_TASK_STATUSES | _UNSUCCESSFUL_TASK_STATUSES
_NEW_SESSION_TASK_STATUSES = frozenset({"starting"})
_NEW_SESSION_STATUS_NOTICES = frozenset(
    {"mowing_started", "mowing_task_started", "scheduled_mowing_started"}
)
_RESUMABLE_TASK_STATUSES = frozenset({"paused"})
_RESUME_STATUS_NOTICES = frozenset(
    {"mowing_resumed_after_charging", "resuming_unfinished_task"}
)
_INACTIVE_PHYSICAL_STATES = frozenset(
    {"error", "idle", "standby", "waiting_for_task", "water_tank_drying"}
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
    session_started_at: float | None = None,
    session_identity: int | None = None,
) -> bool:
    """Return whether an inactive mission has an explicit completion signal."""
    mission_active = runtime_mission_session_active(
        snapshot,
        tracking_active=tracking_active,
        session_started_at=session_started_at,
        session_identity=session_identity,
    )
    if snapshot is None or mission_active is True:
        return False
    if mission_active is None:
        return cached_completion_confirmed
    if getattr(snapshot, "task_status", None) in _UNSUCCESSFUL_TASK_STATUSES:
        return False
    if cached_completion_confirmed:
        return True
    if getattr(snapshot, "task_status", None) in _COMPLETED_TASK_STATUSES:
        return True
    if getattr(snapshot, "status_notice_name", None) not in _COMPLETED_STATUS_NOTICES:
        return False
    notice_event_at = _event_timestamp(
        getattr(snapshot, "status_notice_event_at", None)
    )
    if (
        notice_event_at is not None
        and session_started_at is not None
        and notice_event_at <= session_started_at
    ):
        return False
    return True


def runtime_mission_completion_rejected(
    snapshot: Any,
    *,
    session_started_at: float | None = None,
    session_identity: int | None = None,
) -> bool:
    """Return whether the mower explicitly ended the mission unsuccessfully."""
    return (
        snapshot is not None
        and getattr(snapshot, "task_status", None) in _UNSUCCESSFUL_TASK_STATUSES
        and _runtime_terminal_state_is_current(
            snapshot,
            session_started_at=session_started_at,
            session_identity=session_identity,
        )
    )


def runtime_mission_session_active(
    snapshot: Any,
    *,
    tracking_active: bool | None,
    session_started_at: float | None = None,
    session_identity: int | None = None,
) -> bool | None:
    """Return whether telemetry belongs to a continuing mower mission.

    ``None`` preserves the prior cache boundary when a docked device snapshot
    is missing the optional heartbeat fields that distinguish a charging pause
    from an inactive mower.
    """
    if snapshot is None:
        return tracking_active
    task_status = getattr(snapshot, "task_status", None)
    if (
        task_status in _TERMINAL_TASK_STATUSES
        and not _runtime_terminal_state_is_current(
            snapshot,
            session_started_at=session_started_at,
            session_identity=session_identity,
        )
    ):
        return True
    session_active = getattr(snapshot, "mowing_session_active", None)
    terminal_event_at = _runtime_terminal_evidence_event_at(snapshot)
    heartbeat_event_at = _event_timestamp(
        getattr(snapshot, "task_status_event_at", None)
    )
    active_heartbeat_superseded = bool(
        session_active is True
        and terminal_event_at is not None
        and (heartbeat_event_at is None or terminal_event_at > heartbeat_event_at)
    )
    if session_active is not None and not active_heartbeat_superseded:
        return bool(session_active)
    if (
        getattr(snapshot, "task_resumable", None) is True
        and not active_heartbeat_superseded
    ):
        return True
    if task_status in _RESUMABLE_TASK_STATUSES and not active_heartbeat_superseded:
        return True
    if runtime_mission_new_session(snapshot):
        return True
    if tracking_active and not active_heartbeat_superseded:
        return True
    if getattr(snapshot, "status_notice_name", None) in _RESUME_STATUS_NOTICES:
        notice_event_at = getattr(snapshot, "status_notice_event_at", None)
        state_event_at = getattr(snapshot, "state_event_at", None)
        if (
            isinstance(notice_event_at, int | float)
            and not isinstance(notice_event_at, bool)
            and isinstance(state_event_at, int | float)
            and not isinstance(state_event_at, bool)
            and notice_event_at > state_event_at
        ):
            return True
    if task_status in _COMPLETED_TASK_STATUSES:
        return False
    if getattr(snapshot, "state", None) in {
        "charging",
        "charging_completed",
    }:
        return None
    return False


def runtime_mission_new_session(snapshot: Any) -> bool:
    """Return whether a snapshot explicitly announces a fresh mower mission."""
    if snapshot is None:
        return False
    terminal_event_at = _runtime_terminal_evidence_event_at(snapshot)
    task_status = getattr(snapshot, "task_status", None)
    task_status_event_at = _event_timestamp(
        getattr(snapshot, "task_status_event_at", None)
    )
    if task_status in _NEW_SESSION_TASK_STATUSES and (
        terminal_event_at is None
        or (
            task_status_event_at is not None
            and task_status_event_at > terminal_event_at
        )
    ):
        return True
    if getattr(snapshot, "status_notice_name", None) not in _NEW_SESSION_STATUS_NOTICES:
        return False
    notice_event_at = _event_timestamp(
        getattr(snapshot, "status_notice_event_at", None)
    )
    if getattr(snapshot, "mowing_session_active", None) is not False:
        return bool(
            terminal_event_at is None
            or (notice_event_at is not None and notice_event_at > terminal_event_at)
        )
    physical_event_at = max(
        (
            event_at
            for event_at in (
                _event_timestamp(getattr(snapshot, "state_event_at", None)),
                _event_timestamp(getattr(snapshot, "task_status_event_at", None)),
            )
            if event_at is not None
        ),
        default=None,
    )
    return (
        notice_event_at is not None
        and physical_event_at is not None
        and notice_event_at > physical_event_at
    )


def _event_timestamp(value: Any) -> float | None:
    """Return a comparable realtime event timestamp when available."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _runtime_terminal_evidence_event_at(snapshot: Any) -> float | None:
    """Return the newest ordered non-heartbeat evidence that a mission ended."""
    candidates: list[float] = []
    if getattr(snapshot, "state", None) in _INACTIVE_PHYSICAL_STATES:
        state_event_at = _event_timestamp(getattr(snapshot, "state_event_at", None))
        if state_event_at is not None:
            candidates.append(state_event_at)
    if getattr(snapshot, "status_notice_name", None) in _COMPLETED_STATUS_NOTICES:
        notice_event_at = _event_timestamp(
            getattr(snapshot, "status_notice_event_at", None)
        )
        if notice_event_at is not None:
            candidates.append(notice_event_at)
    return max(candidates, default=None)


def runtime_mission_new_session_event_at(snapshot: Any) -> float | None:
    """Return ordered realtime evidence for the current mission start."""
    if snapshot is None or not runtime_mission_new_session(snapshot):
        return None
    task_status = getattr(snapshot, "task_status", None)
    if task_status in _NEW_SESSION_TASK_STATUSES:
        event_at = _event_timestamp(getattr(snapshot, "task_status_event_at", None))
        if event_at is not None:
            return event_at
    notice_name = getattr(snapshot, "status_notice_name", None)
    if notice_name in _NEW_SESSION_STATUS_NOTICES:
        return _event_timestamp(getattr(snapshot, "status_notice_event_at", None))
    return None


def runtime_mission_session_event_at(
    snapshot: Any,
    *,
    active_session: bool | None,
) -> float | None:
    """Return the best ordered boundary for a newly observed active mission."""
    explicit_start_at = runtime_mission_new_session_event_at(snapshot)
    if explicit_start_at is not None or active_session is not True:
        return explicit_start_at
    return max(
        (
            event_at
            for event_at in (
                _event_timestamp(getattr(snapshot, "state_event_at", None)),
                _event_timestamp(getattr(snapshot, "task_status_event_at", None)),
            )
            if event_at is not None
        ),
        default=None,
    )


def runtime_mission_session_identity(
    snapshot: Any,
    *,
    session_started_at: float | None = None,
    cached_session_identity: int | None = None,
) -> int | None:
    """Return a heartbeat task id only when it can belong to this mission."""
    task_id = _snapshot_mission_session_identity(snapshot)
    if (
        getattr(snapshot, "task_status", None) in _TERMINAL_TASK_STATUSES
        and (session_started_at is not None or cached_session_identity is not None)
        and not _runtime_terminal_state_is_current(
            snapshot,
            session_started_at=session_started_at,
            session_identity=cached_session_identity,
        )
    ):
        return None
    return task_id


def _snapshot_mission_session_identity(snapshot: Any) -> int | None:
    """Return the raw heartbeat task id carried by a snapshot."""
    task_id = getattr(snapshot, "mission_task_id", None)
    if isinstance(task_id, int) and not isinstance(task_id, bool):
        return task_id
    return None


def _runtime_terminal_state_is_current(
    snapshot: Any,
    *,
    session_started_at: float | None,
    session_identity: int | None,
) -> bool:
    """Return whether terminal task evidence belongs to the cached mission."""
    if session_started_at is None and session_identity is None:
        return True
    snapshot_identity = _snapshot_mission_session_identity(snapshot)
    if session_identity is not None and snapshot_identity is not None:
        return snapshot_identity == session_identity
    terminal_event_at = _event_timestamp(
        getattr(snapshot, "task_status_event_at", None)
    )
    return (
        session_started_at is not None
        and terminal_event_at is not None
        and terminal_event_at > session_started_at
    )


def runtime_mission_new_session_evidence(snapshot: Any) -> tuple[Any, ...] | None:
    """Return stable evidence that distinguishes repeated start snapshots."""
    if snapshot is None or not runtime_mission_new_session(snapshot):
        return None
    task_id = runtime_mission_session_identity(snapshot)
    if task_id is not None:
        return ("task", task_id)
    task_status = getattr(snapshot, "task_status", None)
    task_status_event_at = getattr(snapshot, "task_status_event_at", None)
    if (
        task_status in _NEW_SESSION_TASK_STATUSES
        and isinstance(task_status_event_at, int | float)
        and not isinstance(task_status_event_at, bool)
    ):
        return ("task_status", task_status, float(task_status_event_at))
    notice_name = getattr(snapshot, "status_notice_name", None)
    notice_event_at = getattr(snapshot, "status_notice_event_at", None)
    if (
        notice_name in _NEW_SESSION_STATUS_NOTICES
        and isinstance(notice_event_at, int | float)
        and not isinstance(notice_event_at, bool)
    ):
        return ("notice", notice_name, float(notice_event_at))
    return None


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
    _session_generation: int = 0
    _session_identity: int | None = None
    _session_started_at: float | None = None
    _new_session_signal_seen: bool = False
    _new_session_evidence: tuple[Any, ...] | None = None
    _new_session_evidence_pending: bool = False
    _new_session_evidence_after: float | None = None
    _new_session_pending_activation: bool = False

    def _invalidate_for_new_session(
        self,
        *,
        session_identity: int | None = None,
        session_started_at: float | None = None,
    ) -> None:
        self.blob = None
        self.captured_at = None
        self.completion_confirmed = False
        self._metric_signature = None
        self._session_active = True
        self._session_generation += 1
        self._session_identity = session_identity
        self._session_started_at = session_started_at
        self._new_session_signal_seen = False
        self._new_session_evidence = None
        self._new_session_evidence_pending = False
        self._new_session_evidence_after = None
        self._new_session_pending_activation = False

    def begin_new_session(
        self,
        *,
        observed_generation: int | None = None,
        session_started_at: float | None = None,
    ) -> None:
        """Invalidate prior telemetry after a fresh mission is accepted."""
        if (
            observed_generation is not None
            and self._session_generation != observed_generation
        ):
            # A realtime callback already observed and invalidated the new
            # mission while the command awaited its device response. Keep any
            # telemetry that callback captured instead of resetting it again.
            self._new_session_signal_seen = True
            self._new_session_evidence_pending = self._new_session_evidence is None
            self._new_session_evidence_after = (
                session_started_at if self._new_session_evidence_pending else None
            )
            self._new_session_pending_activation = False
            if self._session_started_at is None:
                self._session_started_at = session_started_at
            return
        self._invalidate_for_new_session(session_started_at=session_started_at)
        self._new_session_signal_seen = True
        self._new_session_evidence_pending = True
        self._new_session_evidence_after = session_started_at
        self._new_session_pending_activation = True

    def observe_session_state(
        self,
        *,
        active_session: bool | None,
        completion_confirmed: bool = False,
        completion_rejected: bool = False,
        new_session: bool = False,
        new_session_evidence: tuple[Any, ...] | None = None,
        new_session_event_at: float | None = None,
        session_identity: int | None = None,
    ) -> None:
        """Apply authoritative mission state before optional telemetry work."""
        invalidated = False
        ordered_evidence_is_current = bool(
            not self._new_session_evidence_pending
            or self._new_session_evidence_after is None
            or (
                new_session_event_at is not None
                and new_session_event_at > self._new_session_evidence_after
            )
        )
        if (
            active_session
            and ordered_evidence_is_current
            and session_identity is not None
            and self._session_identity is not None
            and session_identity != self._session_identity
        ):
            self._invalidate_for_new_session(
                session_identity=session_identity,
                session_started_at=new_session_event_at,
            )
            invalidated = True
        if new_session:
            if (
                new_session_evidence is not None
                and new_session_evidence != self._new_session_evidence
            ):
                if self._new_session_evidence_pending and ordered_evidence_is_current:
                    self._new_session_evidence_pending = False
                    self._new_session_evidence_after = None
                elif not self._new_session_evidence_pending and not invalidated:
                    self._invalidate_for_new_session(
                        session_identity=session_identity,
                        session_started_at=new_session_event_at,
                    )
                    invalidated = True
                if not self._new_session_evidence_pending:
                    self._new_session_evidence = new_session_evidence
                    if new_session_event_at is not None:
                        self._session_started_at = new_session_event_at
            elif (
                new_session_evidence is None
                and not self._new_session_signal_seen
                and not invalidated
            ):
                self._invalidate_for_new_session(
                    session_identity=session_identity,
                    session_started_at=new_session_event_at,
                )
                invalidated = True
            self._new_session_signal_seen = True
            self._new_session_pending_activation = False
            active_session = True
        if active_session is None:
            return
        if active_session:
            if not self._session_active:
                self._invalidate_for_new_session(
                    session_identity=session_identity,
                    session_started_at=new_session_event_at,
                )
            elif session_identity is not None and ordered_evidence_is_current:
                self._session_identity = session_identity
            self.completion_confirmed = False
            self._session_active = True
            self._new_session_pending_activation = False
            return
        if self._new_session_pending_activation and not (
            completion_confirmed or completion_rejected
        ):
            return
        self._session_active = False
        self._session_identity = None
        self._new_session_signal_seen = False
        self._new_session_evidence = None
        self._new_session_evidence_pending = False
        self._new_session_evidence_after = None
        self._new_session_pending_activation = False
        if completion_rejected:
            self.completion_confirmed = False
        elif completion_confirmed:
            self.completion_confirmed = True

    def update(
        self,
        blob: Any,
        *,
        now: datetime | None = None,
        allow_zero: bool = True,
        active_session: bool | None = False,
        completion_confirmed: bool = False,
        completion_rejected: bool = False,
        new_session: bool = False,
        new_session_evidence: tuple[Any, ...] | None = None,
        new_session_event_at: float | None = None,
        session_identity: int | None = None,
    ) -> bool:
        """Store a useful runtime payload without erasing it with empty polls."""
        self.observe_session_state(
            active_session=active_session,
            completion_confirmed=completion_confirmed,
            completion_rejected=completion_rejected,
            new_session=new_session,
            new_session_evidence=new_session_evidence,
            new_session_event_at=new_session_event_at,
            session_identity=session_identity,
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
    completion_rejected: bool = False,
    new_session: bool = False,
    new_session_evidence: tuple[Any, ...] | None = None,
    new_session_event_at: float | None = None,
    session_identity: int | None = None,
) -> None:
    """Apply authoritative session state when the integration owns this cache."""
    if isinstance(cache, DreameLawnMowerRuntimeTelemetryCache):
        cache.observe_session_state(
            active_session=active_session,
            completion_confirmed=completion_confirmed,
            completion_rejected=completion_rejected,
            new_session=new_session,
            new_session_evidence=new_session_evidence,
            new_session_event_at=new_session_event_at,
            session_identity=session_identity,
        )


def runtime_mission_session_generation(cache: Any) -> int | None:
    """Return the current integration-owned mission generation."""
    if isinstance(cache, DreameLawnMowerRuntimeTelemetryCache):
        return cache._session_generation
    return None


def runtime_mission_session_started_at(cache: Any) -> float | None:
    """Return ordered evidence for the current cached mission boundary."""
    if isinstance(cache, DreameLawnMowerRuntimeTelemetryCache):
        return cache._session_started_at
    return None


def runtime_mission_cached_session_identity(cache: Any) -> int | None:
    """Return the current integration-owned heartbeat task identity."""
    if isinstance(cache, DreameLawnMowerRuntimeTelemetryCache):
        return cache._session_identity
    return None


def begin_runtime_mission_session(
    cache: Any,
    *,
    observed_generation: int | None = None,
    session_started_at: float | None = None,
) -> None:
    """Invalidate prior telemetry when an integration-owned start succeeds."""
    if isinstance(cache, DreameLawnMowerRuntimeTelemetryCache):
        cache.begin_new_session(
            observed_generation=observed_generation,
            session_started_at=session_started_at,
        )
