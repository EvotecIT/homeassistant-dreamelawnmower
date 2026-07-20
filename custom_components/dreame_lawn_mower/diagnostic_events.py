"""Sanitized recent-event diagnostics for the Home Assistant integration."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .debug import sanitize_debug_data, sanitize_diagnostic_text

DEFAULT_DIAGNOSTIC_EVENT_LIMIT = 25


class DreameLawnMowerDiagnosticEventStore:
    """Keep a bounded, deduplicated record of recent integration events."""

    def __init__(self, *, limit: int = DEFAULT_DIAGNOSTIC_EVENT_LIMIT) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=max(1, limit))

    def record(
        self,
        *,
        code: str,
        source: str,
        message: object,
        severity: str = "warning",
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record one safe event, coalescing consecutive duplicates."""
        captured_at = datetime.now(UTC).isoformat()
        safe_message = sanitize_diagnostic_text(message)
        safe_context = sanitize_debug_data(dict(context or {}))
        fingerprint = (code, source, safe_message, repr(safe_context))

        if self._events and self._events[-1]["_fingerprint"] == fingerprint:
            event = self._events[-1]
            event["last_seen"] = captured_at
            event["count"] += 1
            return self._public_event(event)

        event = {
            "_fingerprint": fingerprint,
            "code": code,
            "source": source,
            "severity": severity,
            "message": safe_message,
            "context": safe_context,
            "first_seen": captured_at,
            "last_seen": captured_at,
            "count": 1,
        }
        self._events.append(event)
        return self._public_event(event)

    def as_list(self) -> list[dict[str, Any]]:
        """Return JSON-safe events without internal bookkeeping."""
        return [self._public_event(event) for event in self._events]

    @staticmethod
    def _public_event(event: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in event.items() if not key.startswith("_")}


def record_diagnostic_event(
    coordinator: object,
    *,
    code: str,
    source: str,
    message: object,
    severity: str = "warning",
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Record an event when the coordinator exposes the shared store."""
    store = getattr(coordinator, "diagnostic_events", None)
    if not isinstance(store, DreameLawnMowerDiagnosticEventStore):
        return None
    return store.record(
        code=code,
        source=source,
        message=message,
        severity=severity,
        context=context,
    )
