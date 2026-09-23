"""Bounded, in-memory history of mower faults and actionable notices."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from typing import Any

from .const import ACTIVITY_ERROR
from .debug import sanitize_diagnostic_text

ACTIONABLE_NOTICE_TIERS = frozenset({"alert", "attention", "unknown"})
_HISTORY_LIMIT = 5
_STATE_LIMIT = 255


class MowerConditionHistory:
    """Retain condition transitions without repeating every polling snapshot."""

    def __init__(self) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=_HISTORY_LIMIT)
        self._last_error: dict[str, Any] | None = None
        self._active: dict[str, tuple[int | None, str] | None] = {
            "warning": None,
            "error": None,
        }

    def observe(self, snapshot: Any, *, observed_at: datetime | None = None) -> bool:
        """Record newly observed conditions and return whether history changed."""
        if not getattr(snapshot, "available", True):
            return False

        warning = None
        if (
            getattr(snapshot, "status_notice_display", None)
            and (getattr(snapshot, "status_notice_tier", None) or "unknown").casefold()
            in ACTIONABLE_NOTICE_TIERS
        ):
            warning = (
                getattr(snapshot, "status_notice_code", None),
                snapshot.status_notice_display,
                getattr(snapshot, "status_notice_source", None),
            )

        error = None
        if getattr(snapshot, "activity", None) == ACTIVITY_ERROR:
            error = (
                getattr(snapshot, "error_code", None),
                getattr(snapshot, "error_display", None)
                or getattr(snapshot, "error_name", None)
                or "Unknown fault",
                getattr(snapshot, "error_source", None),
            )

        changed = False
        for severity, condition in (("warning", warning), ("error", error)):
            if condition is None:
                self._active[severity] = None
                continue
            code, raw_message, source = condition
            message = sanitize_diagnostic_text(raw_message).strip()[:_STATE_LIMIT]
            fingerprint = (code, message)
            if fingerprint == self._active[severity]:
                continue
            self._active[severity] = fingerprint
            event = {
                "severity": severity,
                "code": code,
                "message": message,
                "source": source,
                "observed_at": (observed_at or datetime.now(UTC)).isoformat(),
            }
            self._events.appendleft(event)
            if severity == "error":
                self._last_error = event
            changed = True
        return changed

    def latest(self, *, severity: str | None = None) -> dict[str, Any] | None:
        """Return a copy of the latest matching condition."""
        if severity == "error":
            return dict(self._last_error) if self._last_error is not None else None
        return next(
            (
                dict(event)
                for event in self._events
                if severity is None or event["severity"] == severity
            ),
            None,
        )

    def recent(self) -> list[dict[str, Any]]:
        """Return the five latest conditions, newest first."""
        return [dict(event) for event in self._events]
