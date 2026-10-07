"""Retain suspended state plans until their device lock permits closure."""

from collections.abc import Generator
from queue import Empty, SimpleQueue
from typing import Any


class _DevicePlanCleanup:
    """Transfer plan ownership without executing generator cleanup off-lock."""

    def __init__(self) -> None:
        self._plans: SimpleQueue[Generator[Any, Any, Any]] = SimpleQueue()

    def defer(self, plan: Generator[Any, Any, Any]) -> None:
        """Retain a plan after the action's cleanup grace period expires."""
        self._plans.put(plan)

    def drain(self) -> None:
        """Close retained plans while the caller owns the device state lock."""
        while True:
            try:
                plan = self._plans.get_nowait()
            except Empty:
                return
            plan.close()
