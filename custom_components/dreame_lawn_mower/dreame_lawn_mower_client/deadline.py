"""Bound potentially blocking connection setup by an absolute deadline."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

_ResultT = TypeVar("_ResultT")
_MAX_ABANDONED_OPERATIONS = 4
_operation_slots = threading.BoundedSemaphore(_MAX_ABANDONED_OPERATIONS)


class DeadlineExceededError(TimeoutError):
    """Raised when an operation does not finish before its deadline."""


def _close_if_possible(value: Any) -> None:
    """Close a late response without assuming a particular HTTP client."""
    close = getattr(value, "close", None)
    if callable(close):
        close()


def run_with_deadline(
    operation: Callable[[], _ResultT],
    *,
    deadline: float,
) -> _ResultT:
    """Run a blocking operation without letting it retain its caller forever.

    Socket timeouts only bound periods without network activity. A peer can still
    trickle response headers indefinitely, so connection setup runs in a daemon
    thread and the caller observes the absolute deadline. The semaphore bounds
    abandoned operations if a dependency ignores both its socket timeout and
    cancellation.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not _operation_slots.acquire(timeout=remaining):
        raise DeadlineExceededError("The operation deadline expired.")

    finished = threading.Event()
    state_lock = threading.Lock()
    state: dict[str, Any] = {"abandoned": False}

    def worker() -> None:
        value: Any = None
        has_value = False
        try:
            value = operation()
            has_value = True
            with state_lock:
                if not state["abandoned"]:
                    state["value"] = value
                    has_value = False
        except BaseException as err:
            with state_lock:
                if not state["abandoned"]:
                    state["error"] = err
        finally:
            if has_value:
                _close_if_possible(value)
            finished.set()
            _operation_slots.release()

    threading.Thread(
        target=worker,
        name="dreame-deadline-operation",
        daemon=True,
    ).start()

    remaining = deadline - time.monotonic()
    if remaining <= 0 or not finished.wait(remaining):
        late_value: Any = None
        with state_lock:
            state["abandoned"] = True
            late_value = state.pop("value", None)
        if late_value is not None:
            _close_if_possible(late_value)
        raise DeadlineExceededError("The operation deadline expired.")

    with state_lock:
        error = state.get("error")
        if error is not None:
            raise error
        return state["value"]
