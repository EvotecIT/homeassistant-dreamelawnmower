"""HA lifetime and private persistence for the client-owned history models."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any

from .const import DOMAIN
from .control_options import active_map_index
from .dreame_lawn_mower_client.map_inventory import selected_map_revision
from .dreame_lawn_mower_client.session_photos import async_download_session_photo
from .dreame_lawn_mower_client.task_history import (
    TaskDetail,
    TaskHistory,
    finite_number,
)
from .dreame_lawn_mower_client.task_observations import (
    TaskObservations,
    capture_settings,
)
from .ha_tasks import create_background_task
from .observation_checkpoint import ObservationStore
from .runtime_cache import (
    runtime_mission_session_started_at,
)
from .task_battery_recorder import async_recorder_battery

_LOGGER = logging.getLogger(__name__)


def history_store(
    hass: Any, entry_id: str, suffix: str = "task_history"
) -> ObservationStore:
    """Keep one private, atomic store per configuration entry."""
    return ObservationStore(
        hass,
        1,
        f"{DOMAIN}.{entry_id}.{suffix}",
        private=True,
        atomic_writes=True,
    )


class TaskHistoryOwner:
    """Coalesce dashboard reads and retain only this entry's own observations."""

    def __init__(self, hass: Any, coordinator: Any) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.history = TaskHistory(coordinator.client)
        self.observations = TaskObservations()
        self.recorded_battery: dict[str, dict[str, Any] | None] = {}
        self._recorder_attempts: dict[str, float] = {}
        self.store = history_store(hass, coordinator.entry.entry_id)
        self.summary_store = history_store(
            hass, coordinator.entry.entry_id, "task_summaries"
        )
        self.scope = hashlib.sha256(
            str(coordinator.client.descriptor.unique_id).encode()
        ).hexdigest()
        self._lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._save_task: asyncio.Task[None] | None = None
        self._save_handle: asyncio.TimerHandle | None = None
        self._revision = 0
        self._saved_revision = 0
        self.closed = False
        self._load_failed = False
        self._history_load_failed = False
        self._saved_history_revision = 0

    async def async_load(self) -> None:
        """An unavailable history file must not prevent mower control."""
        try:
            record = await self.store.async_load()
            if isinstance(record, dict) and record.get("scope") == self.scope:
                self.observations.restore(record, now=int(time.time()))
        except Exception as err:  # noqa: BLE001 - optional local history
            self._load_failed = True
            _LOGGER.warning(
                "Could not load mower task observations: %s", type(err).__name__
            )
        try:
            record = await self.summary_store.async_load()
            if isinstance(record, dict) and record.get("scope") == self.scope:
                self.history.restore(record)
        except Exception as err:  # noqa: BLE001 - optional private summary cache
            self._history_load_failed = True
            _LOGGER.warning(
                "Could not load mower task summaries: %s", type(err).__name__
            )

    def observe(self, snapshot: Any, mission_active: bool | None) -> None:
        """Observe authoritative snapshots after the existing mission boundary."""
        if self.closed:
            return
        if not snapshot.available or mission_active is None:
            self.interrupt()
            return
        cache = self.coordinator.runtime_telemetry_cache
        start = runtime_mission_session_started_at(cache)
        key = str(start) if start is not None else None
        index = active_map_index(self.coordinator.app_maps)
        batch = self.coordinator.batch_device_data or {}
        preferences = batch.get("batch_mowing_preferences")
        if self.observations.observe(
            key=key,
            at=int(time.time()),
            level=snapshot.battery_level,
            charging=snapshot.charging,
            active=mission_active,
            map_revision=selected_map_revision(self.coordinator.app_maps, index),
            settings=capture_settings(preferences, index),
        ):
            self._revision += 1
            self._schedule_save()

    def interrupt(self) -> None:
        """Record explicit unknown/offline intervals even without a sample."""
        if not self.closed and self.observations.interrupt(at=int(time.time())):
            self._revision += 1
            self._schedule_save()

    def _schedule_save(self) -> None:
        if self.closed or self._save_handle is not None:
            return
        self._save_handle = asyncio.get_running_loop().call_later(60, self._start_save)

    def _start_save(self) -> None:
        self._save_handle = None
        if not self.closed:
            self._save_task = create_background_task(
                self.hass,
                self.async_save(),
                f"{DOMAIN} task history save",
            )

    async def async_save(self) -> None:
        """Serialize writes and preserve dirty evidence when a write fails."""
        async with self._save_lock:
            if not self._load_failed and self._saved_revision != self._revision:
                revision = self._revision
                try:
                    await self.store.async_save(
                        {"scope": self.scope, **self.observations.export()}
                    )
                    self._saved_revision = revision
                except Exception as err:  # noqa: BLE001 - retry optional evidence later
                    _LOGGER.warning(
                        "Could not save mower task observations: %s", type(err).__name__
                    )
            if (
                not self._history_load_failed
                and self._saved_history_revision != self.history.revision
            ):
                revision = self.history.revision
                try:
                    await self.summary_store.async_save(
                        {"scope": self.scope, **self.history.export()}
                    )
                    self._saved_history_revision = revision
                except Exception as err:  # noqa: BLE001 - retain dirty cache for retry
                    _LOGGER.warning(
                        "Could not save mower task summaries: %s", type(err).__name__
                    )
            if (not self._load_failed and self._saved_revision != self._revision) or (
                not self._history_load_failed
                and self._saved_history_revision != self.history.revision
            ):
                self._schedule_save()

    async def async_read(self, key: str) -> Any:
        """Share one operation per key; disconnecting readers leave no orphans."""
        if self.closed:
            raise RuntimeError("History entry is unloaded")
        task = self._tasks.get(key)
        if task is None:
            if len(self._tasks) >= 4:
                raise RuntimeError("History is busy")
            task = create_background_task(
                self.hass,
                self._async_read(key),
                f"{DOMAIN} task history read",
            )
            self._tasks[key] = task
            task.add_done_callback(lambda done: self._finish(key, done))
        return await asyncio.shield(task)

    def _finish(self, key: str, task: asyncio.Task[Any]) -> None:
        if self._tasks.get(key) is task:
            self._tasks.pop(key)
        if not task.cancelled():
            task.exception()

    async def _async_read(self, key: str) -> Any:
        async with self._lock:
            if self.closed:
                raise RuntimeError("History entry is unloaded")
            if self.observations.prune(int(time.time())):
                self._revision += 1
                self._schedule_save()
            try:
                await self.history.async_refresh()
            except Exception:
                if not self.history.sessions:
                    raise
                self.history.stale = True
                self.history.refreshed_at = time.monotonic()
            if self.history.revision != self._saved_history_revision:
                self._schedule_save()
            if key == "list":
                return None
            detail = await self.history.async_detail(key)
            if self.history.revision != self._saved_history_revision:
                self._schedule_save()
            if detail is not None:
                self.recorded_battery = {
                    task_id: value
                    for task_id, value in self.recorded_battery.items()
                    if task_id in self.history.sessions
                }
                self._recorder_attempts = {
                    task_id: attempted
                    for task_id, attempted in self._recorder_attempts.items()
                    if task_id in self.history.sessions
                }
                observed = self.observations.for_task(detail)
                now = time.monotonic()
                if (
                    not _has_battery_data(observed)
                    and not _has_battery_data(self.recorded_battery.get(key))
                    and now - self._recorder_attempts.get(key, float("-inf")) >= 60
                ):
                    self._recorder_attempts[key] = now
                    try:
                        self.recorded_battery[key] = await async_recorder_battery(
                            self.hass,
                            self.coordinator,
                            detail,
                        )
                    except Exception as err:  # noqa: BLE001 - optional recorder data
                        _LOGGER.debug(
                            "Task battery recorder history unavailable: %s",
                            type(err).__name__,
                        )
            return detail

    def battery_for_task(self, detail: TaskDetail) -> dict[str, Any] | None:
        """Prefer measured observations without losing separately captured settings."""
        observed = self.observations.for_task(detail)
        recorded = self.recorded_battery.get(detail.id)
        if _has_battery_data(observed) or not _has_battery_data(recorded):
            return observed or recorded
        result = dict(recorded or {})
        if observed and observed.get("settings"):
            for key in ("settings", "settings_observed_at", "settings_timing"):
                result[key] = observed[key]
        return result

    async def async_photo(self, key: str, index: int) -> bytes | None:
        """Coalesce private photo reads without keeping large images in memory."""
        operation = f"photo:{key}:{index}"
        if self.closed:
            raise RuntimeError("History entry is unloaded")
        task = self._tasks.get(operation)
        if task is None:
            if len(self._tasks) >= 4:
                raise RuntimeError("History is busy")
            task = create_background_task(
                self.hass,
                self._async_photo(key, index),
                f"{DOMAIN} task history photo",
            )
            self._tasks[operation] = task
            task.add_done_callback(lambda done: self._finish(operation, done))
        value: bytes | None = await asyncio.shield(task)
        return value

    async def _async_photo(self, key: str, index: int) -> bytes | None:
        async with self._lock:
            if self.closed:
                raise RuntimeError("History entry is unloaded")
            photos = self.history.photos.get(key, ())
            if not 0 <= index < len(photos):
                return None
            return await async_download_session_photo(
                self.coordinator.client, photos[index]
            )

    async def async_close(self) -> None:
        """Withdraw new reads, drain owned work, and flush the final observation."""
        self.closed = True
        if self.observations.interrupt():
            self._revision += 1
        if self._save_handle is not None:
            self._save_handle.cancel()
            self._save_handle = None
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks.values()), return_exceptions=True)
        if self._save_task is not None:
            await asyncio.gather(self._save_task, return_exceptions=True)
        await self.async_save()


async def async_remove_task_history(hass: Any, entry: Any) -> None:
    """Delete this entry's private history when the configuration is removed."""
    await history_store(hass, entry.entry_id).async_remove()
    await history_store(hass, entry.entry_id, "task_summaries").async_remove()


def _has_battery_data(value: dict[str, Any] | None) -> bool:
    return (
        value is not None and finite_number(value.get("percentage_points")) is not None
    )
