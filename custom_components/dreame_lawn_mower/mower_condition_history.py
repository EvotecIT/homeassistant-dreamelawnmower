"""Bounded history of mower faults and actionable notices."""

from __future__ import annotations

import re
from collections import deque
from datetime import UTC, datetime
from typing import Any

from .const import ACTIVITY_ERROR
from .debug import sanitize_diagnostic_text
from .dreame_lawn_mower_client.notice_events import MowerNoticeEventCursor
from .dreame_lawn_mower_client.session_checkpoint import MAX_RUN_AGE_SECONDS

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

    def checkpoint(self) -> dict[str, Any]:
        """Preserve the last observed state, without claiming it is still current."""
        return {
            "events": [dict(event) for event in self._events],
            "last_error": dict(self._last_error) if self._last_error else None,
        }

    def restore_checkpoint(self, record: Any, *, now: datetime) -> None:
        """Retain valid history and reconcile active faults with a fresh snapshot."""
        if not isinstance(record, dict) or set(record) != {"events", "last_error"}:
            return
        events = record["events"]
        last_error = record["last_error"]
        if not isinstance(events, list) or len(events) > _HISTORY_LIMIT:
            return
        self._events = deque(
            (dict(event) for event in events if _valid_event(event, now)),
            maxlen=_HISTORY_LIMIT,
        )
        if last_error is not None and (
            not _valid_event(last_error, now) or last_error["severity"] != "error"
        ):
            last_error = None
        if last_error is None:
            last_error = next(
                (event for event in self._events if event["severity"] == "error"),
                None,
            )
        self._last_error = next(
            (event for event in self._events if event == last_error),
            dict(last_error) if last_error else None,
        )
        self._state_known = {"warning": False, "error": False}
        for severity in self._current_event:
            event = next(
                (
                    event
                    for event in self._events
                    if event["severity"] == severity and event["active"] is True
                ),
                self._last_error
                if severity == "error"
                and self._last_error
                and self._last_error["active"] is True
                else None,
            )
            self._current_event[severity] = event
            self._active[severity] = (
                (event["code"], event["message"], None) if event else None
            )


def _valid_event(event: Any, now: datetime) -> bool:
    required = {"severity", "code", "message", "source", "observed_at", "active"}
    if not isinstance(event, dict) or set(event) not in (
        required,
        required | {"cleared_at"},
    ):
        return False
    if (
        event["severity"] not in ("warning", "error")
        or (
            event["code"] is not None
            and (type(event["code"]) is not int or not 0 <= event["code"] <= 65535)
        )
        or not isinstance(event["message"], str)
        or not 1 <= len(event["message"]) <= _STATE_LIMIT
        or sanitize_diagnostic_text(event["message"]).strip() != event["message"]
        or (
            event["source"] is not None
            and (
                not isinstance(event["source"], str)
                or not re.fullmatch(
                    r"(?:realtime|cloud|property|notification|runtime|status|unknown|realtime_property_[0-9]{1,3}\.[0-9]{1,3})",
                    event["source"],
                )
            )
        )
        or (event["active"] is not None and type(event["active"]) is not bool)
        or ("cleared_at" in event and event["active"] is not False)
    ):
        return False
    try:
        observed = datetime.fromisoformat(event["observed_at"])
        if (
            observed.tzinfo is None
            or not 0 <= (now - observed).total_seconds() <= MAX_RUN_AGE_SECONDS
        ):
            return False
        if "cleared_at" in event:
            cleared = datetime.fromisoformat(event["cleared_at"])
            if cleared.tzinfo is None or not observed <= cleared <= now:
                return False
    except (ValueError, TypeError, OverflowError):
        return False
    return True
