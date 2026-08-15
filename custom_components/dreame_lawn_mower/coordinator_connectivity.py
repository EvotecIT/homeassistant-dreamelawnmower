"""Connectivity recovery policy for the mower coordinator."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime

from .api import DreameLawnMowerSnapshot
from .const import DOMAIN
from .debug import sanitize_diagnostic_text

_LOGGER = logging.getLogger(__name__)

CONNECTIVITY_STALE_GRACE_SECONDS = 180.0
CONNECTIVITY_RETRY_DELAYS_SECONDS = (1.0, 2.0, 5.0, 10.0, 30.0)
CONNECTIVITY_SHUTDOWN_GRACE_SECONDS = 1.0


class DreameLawnMowerConnectivityMixin:
    """Keep short mower disconnects from tearing down otherwise useful state."""

    def _initialize_connectivity_recovery(self) -> None:
        """Initialize state used by the bounded reconnect policy."""
        self._connectivity_last_good_snapshot: DreameLawnMowerSnapshot | None = None
        self._connectivity_last_success_monotonic: float | None = None
        self._connectivity_last_success_at: datetime | None = None
        self._connectivity_degraded_since: datetime | None = None
        self._connectivity_recovered_at: datetime | None = None
        self._connectivity_failure_count = 0
        self._connectivity_last_error: str | None = None
        self._connectivity_retry_after_seconds: float | None = None
        self._connectivity_retry_task: asyncio.Task[None] | None = None
        self._connectivity_retry_inflight_task: asyncio.Task[None] | None = None

    @property
    def connection_degraded(self) -> bool:
        """Return whether current state is retained across a link interruption."""
        return getattr(self, "_connectivity_degraded_since", None) is not None

    @property
    def connection_last_success_at(self) -> datetime | None:
        """Return the last time authoritative mower state was received."""
        return getattr(self, "_connectivity_last_success_at", None)

    @property
    def connection_degraded_since(self) -> datetime | None:
        """Return when the current interrupted-connection period began."""
        return getattr(self, "_connectivity_degraded_since", None)

    @property
    def connection_recovered_at(self) -> datetime | None:
        """Return when the most recent interrupted connection recovered."""
        return getattr(self, "_connectivity_recovered_at", None)

    @property
    def connection_failure_count(self) -> int:
        """Return consecutive failed mower-state observations."""
        return getattr(self, "_connectivity_failure_count", 0)

    @property
    def connection_retry_after_seconds(self) -> float | None:
        """Return the scheduled bounded reconnect delay."""
        return getattr(self, "_connectivity_retry_after_seconds", None)

    @property
    def connection_last_error(self) -> str | None:
        """Return the sanitized reason for the current interrupted connection."""
        return getattr(self, "_connectivity_last_error", None)

    def _record_connectivity_success(
        self,
        snapshot: DreameLawnMowerSnapshot,
    ) -> None:
        """Record authoritative state and finish any degraded period."""
        now = datetime.now(UTC)
        was_degraded = self.connection_degraded
        self._connectivity_last_good_snapshot = snapshot
        self._connectivity_last_success_monotonic = time.monotonic()
        self._connectivity_last_success_at = now
        self._connectivity_failure_count = 0
        self._connectivity_last_error = None
        self._connectivity_retry_after_seconds = None
        self._connectivity_degraded_since = None
        if was_degraded:
            self._connectivity_recovered_at = now
        self._cancel_pending_connectivity_retry()

    def _record_connectivity_failure(
        self,
        error: BaseException | str | None,
    ) -> DreameLawnMowerSnapshot | None:
        """Record a link interruption and return safe retained state when fresh."""
        now = datetime.now(UTC)
        if not self.connection_degraded:
            self._connectivity_degraded_since = now
        self._connectivity_failure_count = self.connection_failure_count + 1
        if error is not None:
            self._connectivity_last_error = sanitize_diagnostic_text(error)

        delay = CONNECTIVITY_RETRY_DELAYS_SECONDS[
            min(
                self._connectivity_failure_count - 1,
                len(CONNECTIVITY_RETRY_DELAYS_SECONDS) - 1,
            )
        ]
        self._connectivity_retry_after_seconds = delay
        self._schedule_connectivity_retry(delay)

        snapshot = getattr(self, "_connectivity_last_good_snapshot", None)
        last_success = getattr(self, "_connectivity_last_success_monotonic", None)
        if snapshot is None or last_success is None:
            return None
        if time.monotonic() - last_success > CONNECTIVITY_STALE_GRACE_SECONDS:
            return None
        return snapshot

    def _schedule_connectivity_retry(self, delay: float) -> None:
        """Schedule one coalesced fast refresh after a connectivity failure."""
        if getattr(self, "_shutting_down", False) or not hasattr(self, "hass"):
            return
        task = getattr(self, "_connectivity_retry_task", None)
        if task is not None and not task.done():
            return
        self._connectivity_retry_task = self.entry.async_create_background_task(
            self.hass,
            self._async_connectivity_retry(delay),
            f"{DOMAIN}-connectivity-retry",
        )

    async def _async_connectivity_retry(self, delay: float) -> None:
        """Run one retry; a repeated failure schedules the next backoff step."""
        current = asyncio.current_task()
        try:
            await asyncio.sleep(delay)
            if getattr(self, "_shutting_down", False):
                return
            self._connectivity_retry_task = None
            self._connectivity_retry_inflight_task = current
            await self.async_request_refresh()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - coordinator records refresh outcome
            _LOGGER.debug("Fast mower connectivity refresh failed: %s", err)
        finally:
            if getattr(self, "_connectivity_retry_task", None) is current:
                self._connectivity_retry_task = None
            if getattr(self, "_connectivity_retry_inflight_task", None) is current:
                self._connectivity_retry_inflight_task = None

    def _cancel_pending_connectivity_retry(self) -> None:
        """Cancel a delayed retry after authoritative state returns."""
        task = getattr(self, "_connectivity_retry_task", None)
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if task is not None and task is not current and not task.done():
            task.cancel()
        if task is not current:
            self._connectivity_retry_task = None

    async def _async_shutdown_connectivity_recovery(self) -> None:
        """Cancel delayed and in-flight retry owners before closing the client."""
        current = asyncio.current_task()
        tasks = {
            task
            for task in (
                getattr(self, "_connectivity_retry_task", None),
                getattr(self, "_connectivity_retry_inflight_task", None),
            )
            if task is not None and task is not current
        }
        for task in tasks:
            task.cancel()
        if not tasks:
            return
        done, pending = await asyncio.wait(
            tasks,
            timeout=CONNECTIVITY_SHUTDOWN_GRACE_SECONDS,
        )
        for task in done:
            with suppress(asyncio.CancelledError):
                task.result()
            if getattr(self, "_connectivity_retry_task", None) is task:
                self._connectivity_retry_task = None
            if getattr(self, "_connectivity_retry_inflight_task", None) is task:
                self._connectivity_retry_inflight_task = None
        if pending:
            _LOGGER.warning(
                "Continuing mower shutdown while %s cancelled connectivity "
                "task(s) finish in the background.",
                len(pending),
            )
