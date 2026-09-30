"""Bounded MQTT notice occurrences, separate from the latest property value."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .device_code_semantics import mower_operational_human_detection_notice

NOTICE_EVENT_LIMIT = 64


@dataclass(frozen=True, slots=True)
class MowerNoticeEvent:
    """One received person-detection announcement, not a persistent fault."""

    stream_id: str
    sequence: int
    received_at: float
    code: int = 27
    name: str = "human_detected"
    tier: str = "attention"
    source: str = "realtime_property_2.2"


class MowerNoticeEventBuffer:
    """Retain occurrences under the device state lock for independent readers."""

    def __init__(self) -> None:
        self._stream_id = uuid4().hex
        self._sequence = 0
        self._events: deque[MowerNoticeEvent] = deque(maxlen=NOTICE_EVENT_LIMIT)
        self._seen: deque[int | str] = deque(maxlen=NOTICE_EVENT_LIMIT)

    @property
    def events(self) -> tuple[MowerNoticeEvent, ...]:
        """Return an immutable snapshot without consuming other readers' events."""
        return tuple(self._events)

    def reset_connection(self) -> None:
        """Reset delivery identities while retaining queued and consumed events."""
        self._seen.clear()

    def record(self, *, received_at: float, message_id: Any = None) -> bool:
        """Record a fresh announcement, deduplicating known delivery identities."""
        if not math.isfinite(received_at):
            return False
        if (
            isinstance(message_id, int)
            and not isinstance(message_id, bool)
            and message_id > 0
        ) or (isinstance(message_id, str) and 0 < len(message_id) <= 128):
            if message_id in self._seen:
                return False
            self._seen.append(message_id)
        self._sequence += 1
        self._events.append(
            MowerNoticeEvent(self._stream_id, self._sequence, received_at)
        )
        return True


class MowerNoticeEventCursor:
    """Track occurrences already consumed by one ordered snapshot reader."""

    def __init__(self) -> None:
        self._stream_id: str | None = None
        self._sequence = 0

    def new_events(
        self, events: Sequence[MowerNoticeEvent]
    ) -> tuple[MowerNoticeEvent, ...]:
        """Return each retained occurrence once, including after device replacement."""
        fresh = []
        for event in events:
            if event.stream_id != self._stream_id:
                self._stream_id = event.stream_id
                self._sequence = 0
            if event.sequence > self._sequence:
                self._sequence = event.sequence
                fresh.append(event)
        return tuple(fresh)


def remember_notice_events(device: Any, receipt: Mapping[str, Any]) -> bool:
    """Capture notices after all properties in an MQTT batch update device state."""
    message = receipt["message"]
    state_obj = getattr(device.status, "state", None)
    state = str(getattr(state_obj, "name", "")).casefold()
    model = getattr(getattr(device, "info", None), "model", None)
    for param in message.get("params", ()):
        if (
            param.get("siid") != 2
            or param.get("piid") != 2
            or param.get("code", 0) != 0
            or not mower_operational_human_detection_notice(
                param.get("value"), model=model, state=state
            )
        ):
            continue
        buffer = getattr(device, "_notice_events", None)
        if buffer is None:
            buffer = device._notice_events = MowerNoticeEventBuffer()
        return buffer.record(
            received_at=receipt["received_at"], message_id=message.get("id")
        )
    return False
