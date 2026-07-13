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


@dataclass(slots=True)
class DreameLawnMowerRuntimeTelemetryCache:
    """Preserve the latest useful mission metrics after a session ends."""

    blob: Any = None
    captured_at: datetime | None = None
    _metric_signature: tuple[Any, ...] | None = None

    def update(
        self,
        blob: Any,
        *,
        now: datetime | None = None,
        allow_zero: bool = True,
        active_session: bool = False,
    ) -> bool:
        """Store a useful runtime payload without erasing it with empty polls."""
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
