"""Small, validated observation summaries shared by timer and persistence."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

MAX_RUN_AGE_SECONDS = 30 * 86400


def session_identity(value: Any) -> int | None:
    """Keep only bounded firmware task identifiers, not process counters."""
    return value if type(value) is int and 0 <= value <= 2**64 - 1 else None


@dataclass(frozen=True, slots=True)
class ObservedRun:
    """One observation interval summary, not proof of firmware completion."""

    seconds: float
    started_at: datetime
    updated_at: datetime
    partial: bool
    state: str
    identity: int | None

    def interrupted(self) -> ObservedRun:
        """Preserve useful time without claiming the missing ending was seen."""
        return replace(self, partial=True, state="interrupted")

    def as_dict(self) -> dict[str, Any]:
        """Serialize a fixed set of scalar fields only."""
        return {
            "seconds": self.seconds,
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "partial": self.partial,
            "state": self.state,
            "identity": self.identity,
        }


def decode_run(record: Any, *, now: datetime) -> ObservedRun | None:
    """Reject invalid numbers, dates, identities and implausible durations."""
    if not isinstance(record, dict) or set(record) != {
        "seconds",
        "started_at",
        "updated_at",
        "partial",
        "state",
        "identity",
    }:
        return None
    seconds = record["seconds"]
    if (
        type(seconds) not in (int, float)
        or not 0 <= seconds <= MAX_RUN_AGE_SECONDS
        or not math.isfinite(seconds)
        or type(record["partial"]) is not bool
        or not isinstance(record["state"], str)
        or record["state"]
        not in {"mowing", "paused", "ended", "uncertain", "interrupted"}
        or (
            record["identity"] is not None
            and session_identity(record["identity"]) is None
        )
    ):
        return None
    try:
        start = datetime.fromisoformat(record["started_at"])
        updated = datetime.fromisoformat(record["updated_at"])
        if (
            start.tzinfo is None
            or updated.tzinfo is None
            or not 0 <= (now - updated).total_seconds() <= MAX_RUN_AGE_SECONDS
            or not 0 <= (updated - start).total_seconds() <= MAX_RUN_AGE_SECONDS
            or seconds > (updated - start).total_seconds() + 1
        ):
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    return ObservedRun(
        float(seconds),
        start,
        updated,
        record["partial"],
        record["state"],
        record["identity"],
    )
