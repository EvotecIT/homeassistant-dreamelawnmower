"""Per-request, bounded evidence that survives an abandoned worker thread."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

from .point_cloud_diagnostics import safe_attempt_diagnostics

active_point_cloud_trace: ContextVar[PointCloudTrace | None] = ContextVar(
    "point_cloud_trace",
    default=None,
)


class PointCloudTrace:
    """Keep the first eight and latest 24 observations, never raw cloud values.

    The worker publishes copied summaries under a lock. The awaiting task can take
    an independent snapshot at its deadline without reading mutable worker state.
    Context propagation through asyncio.to_thread isolates concurrent requests.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._id = uuid.uuid4().hex
        self._first: list[dict[str, Any]] = []
        self._last: deque[dict[str, Any]] = deque(maxlen=24)
        self._count = 0
        self._previous: tuple[str, dict[str, Any]] | None = None
        self._latest: dict[str, Any] = {}
        self._first_failure: dict[str, Any] = {}

    def record(self, stage: str, observation: Mapping[str, Any] | None = None) -> None:
        safe = safe_attempt_diagnostics(observation or {}, include_trace=False)
        with self._lock:
            if observation is not None:
                self._latest = safe
            if not self._first_failure and (
                safe.get("validation_reason")
                or safe.get("download_reason")
                or str(safe.get("last_download_result", "")).startswith(
                    ("error:", "rejected:")
                )
            ):
                self._first_failure = safe
            signature = (stage, safe)
            if signature == self._previous:
                return
            self._previous = signature
            self._count += 1
            event = {
                "trace_stage": stage,
                "elapsed_ms": max(0, int((time.monotonic() - self._started) * 1000)),
                "observation": safe,
            }
            if len(self._first) < 8:
                self._first.append(event)
            else:
                self._last.append(event)

    def snapshot(self, *, complete: bool) -> dict[str, Any]:
        with self._lock:
            return safe_attempt_diagnostics(
                {
                    **self._latest,
                    "attempt_id": self._id,
                    "trace_schema_version": 1,
                    "worker_finished": complete,
                    "first_failure": self._first_failure,
                    "trace_event_count": self._count,
                    "trace_dropped_events": max(0, self._count - 32),
                    "timeline": [*self._first, *self._last],
                }
            )


def record_point_cloud_stage(
    stage: str,
    observation: Mapping[str, Any] | None = None,
) -> None:
    """Publish evidence only when this operation belongs to a traced request."""
    trace = active_point_cloud_trace.get()
    if trace is not None:
        trace.record(stage, observation)
