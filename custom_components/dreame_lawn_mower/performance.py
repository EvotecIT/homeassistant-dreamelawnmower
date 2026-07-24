"""Privacy-safe performance measurements for integration lifecycle operations."""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, TypeVar

DEFAULT_PERFORMANCE_SAMPLE_LIMIT = 20

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class DreameLawnMowerPerformanceSample:
    """One completed integration operation and its measured phases."""

    operation: str
    total_seconds: float
    phases: dict[str, float]
    outcome: str

    def as_dict(self) -> dict[str, Any]:
        """Return a compact JSON-safe representation for diagnostics."""
        return {
            "operation": self.operation,
            "total_ms": round(self.total_seconds * 1000, 1),
            "phases_ms": {
                name: round(duration * 1000, 1)
                for name, duration in sorted(self.phases.items())
            },
            "outcome": self.outcome,
        }


class DreameLawnMowerPerformanceCycle:
    """Measure named async phases that belong to one integration operation."""

    def __init__(
        self,
        tracker: DreameLawnMowerPerformanceTracker,
        operation: str,
    ) -> None:
        self._tracker = tracker
        self.operation = operation
        self._started = tracker.clock()
        self._phases: dict[str, float] = {}
        self._finished = False

    async def measure(
        self,
        phase: str,
        operation: Callable[[], Awaitable[_T]],
    ) -> _T:
        """Run and measure one named phase without changing its error behavior."""
        started = self._tracker.clock()
        try:
            return await operation()
        finally:
            self._phases[phase] = self._tracker.clock() - started

    def finish(self, *, outcome: str = "completed") -> DreameLawnMowerPerformanceSample:
        """Store and return this cycle exactly once."""
        if self._finished:
            raise RuntimeError("Performance cycle has already finished")
        self._finished = True
        sample = DreameLawnMowerPerformanceSample(
            operation=self.operation,
            total_seconds=self._tracker.clock() - self._started,
            phases=dict(self._phases),
            outcome=outcome,
        )
        self._tracker.record(sample)
        return sample


class DreameLawnMowerPerformanceTracker:
    """Keep a bounded history of privacy-safe integration timings."""

    def __init__(
        self,
        *,
        limit: int = DEFAULT_PERFORMANCE_SAMPLE_LIMIT,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self.clock = clock
        self._samples: deque[DreameLawnMowerPerformanceSample] = deque(
            maxlen=max(1, limit)
        )
        self._latest_by_operation: dict[
            str,
            DreameLawnMowerPerformanceSample,
        ] = {}
        self._summary: dict[str, dict[str, float | int]] = {}
        self._outcomes: dict[str, dict[str, int]] = {}

    def start(self, operation: str) -> DreameLawnMowerPerformanceCycle:
        """Start measuring one integration operation."""
        return DreameLawnMowerPerformanceCycle(self, operation)

    def record(self, sample: DreameLawnMowerPerformanceSample) -> None:
        """Record one completed operation."""
        self._samples.append(sample)
        self._latest_by_operation[sample.operation] = sample
        summary = self._summary.setdefault(
            sample.operation,
            {
                "count": 0,
                "total_seconds": 0.0,
                "maximum_seconds": 0.0,
            },
        )
        summary["count"] = int(summary["count"]) + 1
        summary["total_seconds"] = (
            float(summary["total_seconds"]) + sample.total_seconds
        )
        summary["maximum_seconds"] = max(
            float(summary["maximum_seconds"]),
            sample.total_seconds,
        )
        outcomes = self._outcomes.setdefault(sample.operation, {})
        outcomes[sample.outcome] = outcomes.get(sample.outcome, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        """Return bounded samples and aggregate values for diagnostics."""
        return {
            "sample_limit": self._samples.maxlen,
            "summary": {
                operation: {
                    "count": int(summary["count"]),
                    "latest_ms": round(
                        self._latest_by_operation[operation].total_seconds * 1000,
                        1,
                    ),
                    "average_ms": round(
                        float(summary["total_seconds"])
                        / int(summary["count"])
                        * 1000,
                        1,
                    ),
                    "maximum_ms": round(
                        float(summary["maximum_seconds"]) * 1000,
                        1,
                    ),
                    "outcomes": dict(sorted(self._outcomes[operation].items())),
                }
                for operation, summary in sorted(self._summary.items())
            },
            "latest_by_operation": {
                operation: sample.as_dict()
                for operation, sample in sorted(self._latest_by_operation.items())
            },
            "samples": [sample.as_dict() for sample in self._samples],
        }


def format_performance_sample(
    sample: DreameLawnMowerPerformanceSample,
) -> tuple[float, str]:
    """Return total seconds and deterministic phase text for logs."""
    phases = ", ".join(
        f"{name}={duration:.3f}s"
        for name, duration in sorted(sample.phases.items())
    )
    return sample.total_seconds, phases or "none"
