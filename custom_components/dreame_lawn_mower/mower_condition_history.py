"""Bounded, in-memory history of mower faults and actionable notices."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from typing import Any

from .const import ACTIVITY_ERROR
from .debug import sanitize_diagnostic_text
from .dreame_lawn_mower_client.notice_events import MowerNoticeEventCursor

ACTIONABLE_NOTICE_TIERS = frozenset({"alert", "attention", "unknown"})
_HISTORY_LIMIT = 5
_STATE_LIMIT = 255


class MowerConditionHistory:
    """Retain condition transitions without repeating every polling snapshot."""

    def __init__(self) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=_HISTORY_LIMIT)
        self._last_error: dict[str, Any] | None = None
        self._notice_cursor = MowerNoticeEventCursor()
        self._current_event: dict[str, dict[str, Any] | None] = {
            "warning": None,
            "error": None,
        }
        self._state_known = {"warning": True, "error": True}
        self._active: dict[
            str, tuple[int | None, str, tuple[str, int] | None] | None
        ] = {
            "warning": None,
            "error": None,
        }

    def observe(self, snapshot: Any, *, observed_at: datetime | None = None) -> bool:
        """Record newly observed conditions and return whether history changed."""
        if not getattr(snapshot, "available", True):
            return self._set_known("warning", False) | self._set_known("error", False)

        changed = self._set_known("warning", True) | self._set_known(
            "error", getattr(snapshot, "activity", None) is not None
        )
        for event in self._notice_cursor.new_events(
            getattr(snapshot, "notification_events", ())
        ):
            changed = (
                self._record(
                    "warning",
                    event.code,
                    "Human detected",
                    event.source,
                    observed_at=datetime.fromtimestamp(event.received_at, UTC),
                    occurrence=(event.stream_id, event.sequence),
                    active=None,
                )
                or changed
            )

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

        for severity, condition in (("warning", warning), ("error", error)):
            if condition is None:
                if self._state_known[severity]:
                    changed = self._clear(severity, observed_at) or changed
                continue
            code, raw_message, source = condition
            changed = (
                self._record(
                    severity,
                    code,
                    raw_message,
                    source,
                    observed_at=observed_at,
                    occurrence=(
                        (events[-1].stream_id, events[-1].sequence)
                        if severity == "warning"
                        and code == 27
                        and (events := getattr(snapshot, "notification_events", ()))
                        else None
                    ),
                )
                or changed
            )
        return changed

    def _record(
        self,
        severity: str,
        code: int | None,
        raw_message: str,
        source: str | None,
        *,
        observed_at: datetime | None = None,
        occurrence: tuple[str, int] | None = None,
        active: bool | None = True,
    ) -> bool:
        """Keep transitions and timestamped occurrences without polling duplicates."""
        message = sanitize_diagnostic_text(raw_message).strip()[:_STATE_LIMIT]
        fingerprint = (code, message, occurrence)
        prior = self._active[severity]
        if (
            prior is not None
            and prior[:2] == fingerprint[:2]
            and (occurrence is None or occurrence == prior[2])
        ):
            # A polling condition has no occurrence identity after reconnect.
            # Keep the active fingerprint until a fresh event or condition change.
            event = self._current_event[severity]
            if event is not None and active is True and event["active"] is None:
                event["active"] = True
                return True
            return False
        self._clear(severity, observed_at)
        self._active[severity] = fingerprint
        event = {
            "severity": severity,
            "code": code,
            "message": message,
            "source": source,
            "observed_at": (observed_at or datetime.now(UTC)).isoformat(),
            "active": active,
        }
        self._current_event[severity] = event
        self._events.appendleft(event)
        if severity == "error":
            self._last_error = event
        return True

    def _clear(self, severity: str, observed_at: datetime | None) -> bool:
        """Only a known current snapshot can establish that a condition cleared."""
        event = self._current_event[severity]
        self._active[severity] = None
        self._current_event[severity] = None
        if event is None or event["active"] is not True:
            return False
        event["active"] = False
        event["cleared_at"] = (observed_at or datetime.now(UTC)).isoformat()
        return True

    def _set_known(self, severity: str, known: bool) -> bool:
        event = self._current_event[severity]
        changed = self._state_known[severity] != known
        self._state_known[severity] = known
        return changed and event is not None and event["active"] is True

    def _copy(self, event: dict[str, Any]) -> dict[str, Any]:
        result = dict(event)
        if event["active"] is True and not self._state_known[event["severity"]]:
            result["active"] = None
        return result

    def latest(self, *, severity: str | None = None) -> dict[str, Any] | None:
        """Return a copy of the latest matching condition."""
        if severity == "error":
            return (
                self._copy(self._last_error) if self._last_error is not None else None
            )
        return next(
            (
                self._copy(event)
                for event in self._events
                if severity is None or event["severity"] == severity
            ),
            None,
        )

    def recent(self) -> list[dict[str, Any]]:
        """Return the five latest conditions, newest first."""
        return [self._copy(event) for event in self._events]
