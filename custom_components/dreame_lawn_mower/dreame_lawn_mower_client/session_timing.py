"""Observed mowing intervals, separate from device-reported mowing duration."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class ObservedMowingTimer:
    """Count observed mowing state without guessing across gaps or restarts.

    The existing mission owner supplies the generation and session boundary.
    This timer does not interpret firmware task codes or infer blade activity.
    Values are deliberately process-local: startup during a mission begins a
    partial observation, rather than restoring an apparently exact duration.
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
        continuous = (
            self._last_tick is not None
            and 0 <= tick - self._last_tick <= max_gap_seconds
        )
        new_generation = self.generation != generation
        if new_generation:
            self.seconds = 0.0
            self.started_at = None
            self.partial = not (self._observed_idle and continuous)
            self.generation = generation
            self._last_tick = None
            self._was_mowing = False
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
        if self.started_at is not None:
            self.partial = True
            self.state = "interrupted"

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
