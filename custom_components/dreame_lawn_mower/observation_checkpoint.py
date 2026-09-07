"""Bounded, low-write HA persistence for observation evidence, not telemetry."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from datetime import UTC, datetime
from typing import Any

from homeassistant.core import CoreState
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .dreame_lawn_mower_client.session_timing import ObservedMowingTimer

_LOGGER = logging.getLogger(__name__)
MAX_CHECKPOINT_BYTES = 16 * 1024
CHECKPOINT_INTERVAL_SECONDS = 60
TRANSITION_DELAY_SECONDS = 2
MIN_TRANSITION_INTERVAL_SECONDS = 10
MAX_CHECKPOINT_AGE_SECONDS = 30 * 86400


class ObservationStore(Store):
    """Keep HA's atomic storage while exposing immediate write failures.

    Store logs and consumes write errors. The scheduler needs that outcome to
    avoid acknowledging a failed checkpoint and suppressing an identical retry.
    Drain HA's queued shutdown write before releasing this owner, so a later
    entry removal cannot leave an old final-write listener behind.
    """

    async def async_save(self, data: Any) -> None:
        self._checkpoint_write_error: Exception | None = None
        await super().async_save(data)
        if self.hass.state is CoreState.stopping:
            await self._async_handle_write_data()
        if self._checkpoint_write_error is not None:
            raise self._checkpoint_write_error

    async def _async_write_data(self, data: dict) -> None:
        try:
            await super()._async_write_data(data)
        except Exception as err:
            self._checkpoint_write_error = err
            raise


def checkpoint_store(hass: Any, entry_id: str) -> Store:
    """Use one private, overwritten HA store per mower configuration entry."""
    return ObservationStore(
        hass,
        1,
        f"{DOMAIN}.{entry_id}.observation_checkpoint",
        private=True,
        atomic_writes=True,
    )


def checkpoint_fits(record: Any, key: str) -> bool:
    """Bound the complete on-disk envelope, including HA storage metadata."""
    try:
        envelope = {"version": 1, "minor_version": 1, "key": key, "data": record}
        return (
            len(json.dumps(envelope, indent=2, allow_nan=False).encode())
            <= MAX_CHECKPOINT_BYTES
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


class ObservationCheckpoint:
    """Load once, coalesce transitions, checkpoint progress, and drain on close.

    The timer and position tracker own validation and reconciliation. This
    adapter only owns HA storage and write scheduling. No recorder history,
    routes, map geometry, images, tokens or raw packets enter this file.
    """

    def __init__(self, hass: Any, entry_id: str, coordinator: Any) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._key = f"{DOMAIN}.{entry_id}.observation_checkpoint"
        self._store = checkpoint_store(hass, entry_id)
        self._scope = hashlib.sha256(
            str(coordinator.client.descriptor.unique_id).encode()
        ).hexdigest()
        self._lock = asyncio.Lock()
        self._handle: asyncio.TimerHandle | None = None
        self._task: asyncio.Task | None = None
        self._closed = False
        self._close_complete = False
        self._removed = False
        self._last_signature: str | None = None
        self._transition: tuple[Any, ...] | None = None
        self._failed = False
        self._loading = False
        self._load_started = False
        self._load_failed = False
        self._last_write_at: float | None = None
        if getattr(coordinator, "observed_mowing_timer", None) is None:
            coordinator.observed_mowing_timer = ObservedMowingTimer()

    async def async_load(self) -> None:
        """Load before first refresh; storage failures must not disable control."""
        if self._load_started or self._closed:
            return
        self._load_started = True
        self._loading = True
        try:
            record = await self._store.async_load()
        except asyncio.CancelledError:
            self._load_failed = True
            raise
        except Exception as err:  # noqa: BLE001 - optional recovery evidence
            self._load_failed = True
            self._log_failure(err)
            return
        finally:
            self._loading = False
        if (
            self._closed
            or not isinstance(record, dict)
            or set(record) != {"scope", "saved_at", "timing", "position"}
        ):
            return
        now = datetime.now(UTC)
        saved_at = record["saved_at"]
        if (
            record["scope"] != self._scope
            or type(saved_at) not in (int, float)
            or not 0 <= saved_at <= now.timestamp()
            or not 0 <= now.timestamp() - saved_at <= MAX_CHECKPOINT_AGE_SECONDS
            or not math.isfinite(saved_at)
            or not checkpoint_fits(record, self._key)
        ):
            return
        self._coordinator.observed_mowing_timer.restore_checkpoint(
            record["timing"], now=now
        )
        self._coordinator.client._position_tracker.restore_checkpoint(
            record["position"], now=now
        )
        self._last_signature = self._signature(self._capture())

    def _capture(self) -> dict[str, Any]:
        return {
            "scope": self._scope,
            "timing": self._coordinator.observed_mowing_timer.checkpoint(),
            "position": self._coordinator.client._position_tracker.checkpoint(),
        }

    @staticmethod
    def _signature(record: dict[str, Any]) -> str:
        # Repeated paused/idle observations update a timestamp, not measured
        # duration. They must not generate a write every minute indefinitely.
        timing = dict(record["timing"])
        current = timing.get("current")
        if current is not None:
            current = dict(current)
            current.pop("updated_at", None)
            timing["current"] = current
        return json.dumps({**record, "timing": timing}, sort_keys=True, allow_nan=False)

    def async_schedule_save(self) -> None:
        """Schedule one write; frequent updates cannot postpone it forever."""
        if self._closed:
            return
        timer = self._coordinator.observed_mowing_timer
        transition = (timer.generation, timer.state, timer.partial)
        changed = self._transition != transition
        self._transition = transition
        delay = (
            TRANSITION_DELAY_SECONDS
            if changed and not self._failed
            else CHECKPOINT_INTERVAL_SECONDS
        )
        if changed and self._last_write_at is not None:
            delay = max(
                delay,
                MIN_TRANSITION_INTERVAL_SECONDS
                - (self._hass.loop.time() - self._last_write_at),
            )
        when = self._hass.loop.time() + delay
        if self._handle is not None:
            if self._handle.when() <= when:
                return
            self._handle.cancel()
        self._handle = self._hass.loop.call_later(delay, self._start_write)

    def _start_write(self) -> None:
        self._handle = None
        if not self._closed and (self._task is None or self._task.done()):
            self._task = self._hass.async_create_task(self.async_flush())

    async def async_flush(self) -> None:
        """Serialize saves and suppress identical checkpoints, including at stop."""
        async with self._lock:
            if self._loading or self._load_failed or self._removed:
                return
            record = self._capture()
            signature = self._signature(record)
            if signature == self._last_signature:
                return
            record["saved_at"] = time.time()
            if not checkpoint_fits(record, self._key):
                self._log_failure(ValueError("Checkpoint size limit exceeded"))
                return
            try:
                write = self._hass.async_create_task(self._store.async_save(record))
                try:
                    await asyncio.shield(write)
                except asyncio.CancelledError:
                    # HA may finish an executor write after cancellation. Keep
                    # the lock until it drains, especially before removal.
                    await write
                    raise
            except Exception as err:  # noqa: BLE001 - retain control on disk errors
                self._log_failure(err)
                return
            self._last_signature = signature
            self._last_write_at = self._hass.loop.time()
            self._failed = False
            if not self._closed and self._signature(self._capture()) != signature:
                self.async_schedule_save()

    def _log_failure(self, err: Exception) -> None:
        if not self._failed:
            _LOGGER.warning(
                "Mower observation checkpoint unavailable (%s)", type(err).__name__
            )
        self._failed = True

    async def async_close(self) -> None:
        """Stop scheduling before draining the last save on reload or shutdown."""
        if self._close_complete:
            return
        self._closed = True
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None
        if self._task is not None:
            try:
                await asyncio.shield(self._task)
            except asyncio.CancelledError:
                if not self._task.cancelled():
                    raise
        await self.async_flush()
        self._close_complete = True

    async def async_remove(self) -> None:
        """Drain and remove without allowing a late write to recreate the file."""
        await self.async_close()
        async with self._lock:
            self._removed = True
            await self._store.async_remove()


async def async_remove_observation_checkpoint(hass: Any, entry_id: str) -> None:
    """Remove evidence even when the config entry failed to load."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
    checkpoint = getattr(coordinator, "observation_checkpoint", None)
    if isinstance(checkpoint, ObservationCheckpoint):
        await checkpoint.async_remove()
    else:
        await checkpoint_store(hass, entry_id).async_remove()
