"""Observed mowing intervals, separate from device-reported mowing duration."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .session_checkpoint import ObservedRun, decode_run, session_identity


@dataclass(slots=True)
class ObservedMowingTimer:
    """Count observed mowing state without guessing across gaps or restarts.

    The existing mission owner supplies the generation and session boundary.
    This timer does not interpret firmware task codes or infer blade activity.
    Checkpoints restore only observed intervals. A fresh matching task identity
    is required to continue a saved run, and the offline interval is excluded.
    """

    seconds: float = 0.0
    generation: int | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    partial: bool = True
    state: str = "unavailable"
    _last_tick: float | None = None
    _was_mowing: bool = False
    _observed_idle: bool = False
    last_run: ObservedRun | None = None
    _identity: int | None = None
    _recovery: ObservedRun | None = None

    @property
    def minutes(self) -> float | None:
        """Return observed minutes, never substituting for native telemetry."""
        return round(self.seconds / 60, 1) if self.started_at is not None else None

    def observe(
        self,
        *,
        generation: int,
        session_active: bool | None,
        mowing: bool,
        max_gap_seconds: float = 130.0,
        now: datetime | None = None,
        monotonic: float | None = None,
        identity: int | None = None,
        identity_observed_at: float | None = None,
        new_start_at: float | None = None,
    ) -> None:
        """Advance on successful, ordered observations from the session owner.

        Pauses, returning and docking do not accumulate time. Intervals longer
        than the host's observation budget are excluded and marked partial.
        A state transition is sampled, not an exact blade-on/off measurement.
        """
        tick = time.monotonic() if monotonic is None else monotonic
        if not math.isfinite(tick) or max_gap_seconds <= 0:
            self.interrupt()
            return
        timestamp = now or datetime.now(UTC)
        active = session_active is True
        if self._recovery is not None and session_active is not None:
            saved = self._recovery
            self._recovery = None
            if (
                active
                and saved.identity is not None
                and session_identity(identity) == saved.identity
                and identity_observed_at is not None
                and saved.updated_at.timestamp()
                <= identity_observed_at
                <= timestamp.timestamp() + 5
                and (
                    new_start_at is None or new_start_at <= saved.updated_at.timestamp()
                )
                and 0 <= (timestamp - saved.updated_at).total_seconds() <= 86400
            ):
                self.seconds = saved.seconds
                self.started_at = saved.started_at
                self.generation = generation
                self.partial = True
                self._last_tick = None
                self._was_mowing = False
            else:
                self.last_run = saved.interrupted()
        continuous = (
            self._last_tick is not None
            and 0 <= tick - self._last_tick <= max_gap_seconds
        )
        new_generation = self.generation != generation
        if not new_generation and self.state == "ended":
            # Uncertain/offline samples cannot reopen a completed generation.
            # Preserve its summary while retaining only fresh idle continuity.
            self._last_tick = tick if session_active is False else None
            self._was_mowing = False
            self._observed_idle = session_active is False
            return
        if new_generation:
            if self.started_at is not None and self.state != "ended":
                self.last_run = self._run("interrupted")
            self.seconds = 0.0
            self.started_at = None
            self.partial = not (self._observed_idle and continuous)
            self.generation = generation
            self._last_tick = None
            self._was_mowing = False
            self._identity = None
        if active and session_identity(identity) is not None:
            self._identity = identity
        if active and mowing and self.started_at is None:
            self.started_at = timestamp
        if self.started_at is not None and self._last_tick is not None:
            interval = tick - self._last_tick
            if not 0 <= interval <= max_gap_seconds:
                self.partial = True
            elif self._was_mowing:
                self.seconds += interval
        self._last_tick = tick
        self._was_mowing = active and mowing
        self.updated_at = timestamp
        if active:
            self.state = "mowing" if mowing else "paused"
            self._observed_idle = False
        elif session_active is False:
            if self.started_at is not None and self.state != "ended":
                self.last_run = self._run("ended", updated_at=timestamp)
            self.state = "ended" if self.started_at is not None else "unavailable"
            self._observed_idle = True
        else:
            self._observed_idle = False
            self.state = "uncertain" if self.started_at is not None else "unavailable"
            if self.started_at is not None:
                self.partial = True

    def interrupt(self) -> None:
        """Freeze at the last successful observation after connectivity loss."""
        self._last_tick = None
        self._was_mowing = False
        self._observed_idle = False
        if self.started_at is not None and self.state != "ended":
            self.partial = True
            self.state = "interrupted"

    def _run(self, state: str, *, updated_at: datetime | None = None) -> ObservedRun:
        """Capture one bounded summary, never a stream of samples."""
        assert self.started_at is not None and self.updated_at is not None
        return ObservedRun(
            self.seconds,
            self.started_at,
            updated_at or self.updated_at,
            self.partial or state == "interrupted",
            state,
            self._identity,
        )

    def checkpoint(self) -> dict[str, Any]:
        """Export wall-clock evidence only; monotonic ticks cannot be restored."""
        current = self._recovery
        if self.started_at is not None and self.state != "ended":
            current = self._run(self.state)
        return {
            "current": current.as_dict() if current else None,
            "previous": self.last_run.as_dict() if self.last_run else None,
        }

    def restore_checkpoint(self, record: Any, *, now: datetime | None = None) -> None:
        """Stage validated saved evidence without publishing it as a live run."""
        if not isinstance(record, dict):
            return
        timestamp = now or datetime.now(UTC)
        self.last_run = decode_run(record.get("previous"), now=timestamp)
        self._recovery = decode_run(record.get("current"), now=timestamp)
        if self._recovery is not None and self._recovery.state == "ended":
            self._recovery = None

    def attributes(self) -> dict[str, Any]:
        """Explain the measurement so consumers do not present it as exact."""
        return {
            "source": "observed_mowing_state",
            "partial": self.partial,
            "includes_pauses": False,
            "measurement_state": self.state,
            "observed_since": self.started_at.isoformat() if self.started_at else None,
            "last_observed_at": self.updated_at.isoformat()
            if self.updated_at
            else None,
        }
