"""Failure isolation and accounting for legacy cloud and LAN callback requests."""

from __future__ import annotations

import logging
from collections.abc import Callable
from queue import Queue
from threading import Lock
from time import sleep
from typing import Any

# Responses and parameters carry vendor JSON or MiIO values at the transport boundary.
type ResponseCallback = Callable[[Any], None]
type QueuedRequest = tuple[ResponseCallback | None, str, Any, int]


class RequestQueue(Queue[QueuedRequest | tuple[()]]):
    """Unbounded request queue with one terminal stop marker per worker lifetime."""

    def __init__(self) -> None:
        super().__init__()
        self._stop_lock = Lock()
        self._stopped = False

    def put(
        self,
        item: QueuedRequest | tuple[()],
        block: bool = True,
        timeout: float | None = None,
    ) -> None:
        """Accept work until teardown, including requests racing with close."""
        with self._stop_lock:
            if not self._stopped:
                super().put(item, block, timeout)

    def stop(self) -> None:
        """Close admission and submit one accounted stop marker atomically."""
        with self._stop_lock:
            if not self._stopped:
                self._stopped = True
                super().put(())


def run_callback_queue(
    requests: Queue[QueuedRequest | tuple[()]],
    dispatch: Callable[[str, Any, int], Any],
    logger: logging.Logger,
    *,
    delay: float = 0,
) -> None:
    """Deliver each result once and drain failures without stranding later requests.

    A transport exception delivers the existing absent-response value, ``None``.
    Callback exceptions are isolated from the worker. Logs retain the exception
    class only, since transport errors and user callbacks may contain secrets.
    An empty tuple stops the worker after preceding requests finish.
    """
    while True:
        item = requests.get()
        try:
            if not item:
                return
            callback, method, parameters, retry_count = item
            try:
                response = dispatch(method, parameters, retry_count)
            except Exception as err:
                logger.warning("Legacy request failed (%s)", type(err).__name__)
                response = None
            if callback is not None:
                try:
                    callback(response)
                except Exception as err:
                    logger.warning(
                        "Legacy request callback failed (%s)", type(err).__name__
                    )
            if delay:
                sleep(delay)
        finally:
            requests.task_done()
