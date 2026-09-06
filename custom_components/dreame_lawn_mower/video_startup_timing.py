"""Numeric startup diagnostics, separate from live media verification."""

from collections.abc import Callable
from time import monotonic


class VideoStartupTiming:
    """Retain one startup attempt without transport URLs or device credentials."""

    def __init__(self, clock: Callable[[], float] = monotonic):
        self._clock = clock
        self._started = self._phase_started = clock()
        self.phase = "safety"
        self.outcome = "starting"
        self._phases: dict[str, float] = {}
        self._finished: float | None = None
        self._verified: float | None = None

    def enter(self, phase: str) -> None:
        """Accumulate repeated route attempts in their owning startup phase."""
        now = self._clock()
        self._phases[self.phase] = self._phases.get(self.phase, 0) + (
            now - self._phase_started
        )
        self.phase = phase
        self._phase_started = now

    def finish(self, outcome: str) -> None:
        """Close startup; source readiness is not decoded-media verification."""
        self.enter(outcome)
        self.outcome = outcome
        self._finished = self._clock()

    def verified(self) -> None:
        """Record when the relay first confirms decoder-ready media."""
        if self._verified is None:
            self._verified = self._clock()

    def as_dict(self) -> dict:
        """Return only phase names, outcomes, and milliseconds."""
        now = self._clock()
        phases = dict(self._phases)
        if self._finished is None:
            phases[self.phase] = phases.get(self.phase, 0) + now - self._phase_started
        return {
            "phase": self.phase,
            "outcome": self.outcome,
            "total_ms": round((
                (self._finished if self._finished is not None else now) - self._started
            ) * 1000, 1),
            "phases_ms": {key: round(value * 1000, 1) for key, value in phases.items()},
            "verified_media_ms": (
                round((self._verified - self._started) * 1000, 1)
                if self._verified is not None else None
            ),
        }
