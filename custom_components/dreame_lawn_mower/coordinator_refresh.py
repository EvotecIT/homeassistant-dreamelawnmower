"""Foreground and background refresh lifecycle for the mower coordinator."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from functools import partial
from typing import Any

from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import (
    DreameLawnMowerConnectionError,
    DreameLawnMowerSnapshot,
)
from .const import DOMAIN
from .control_options import active_map_index
from .debug import sanitize_diagnostic_text
from .diagnostic_events import record_diagnostic_event
from .ha_tasks import create_background_task
from .performance import (
    DreameLawnMowerPerformanceCycle,
    DreameLawnMowerPerformanceSample,
    DreameLawnMowerPerformanceTracker,
    format_performance_sample,
)
from .runtime_cache import (
    runtime_mission_cached_session_identity,
    runtime_mission_completion_confirmed,
    runtime_mission_completion_rejected,
    runtime_mission_new_session,
    runtime_mission_new_session_evidence,
    runtime_mission_session_active,
    runtime_mission_session_event_at,
    runtime_mission_session_generation,
    runtime_mission_session_identity,
    runtime_mission_session_started_at,
)

_LOGGER = logging.getLogger(__name__)

# The reverse-engineered cloud owner shares request ids, login state, and one
# requests session. Keep its background calls serialized until that owner
# provides an explicit concurrency contract.
METADATA_REFRESH_CONCURRENCY = 1
METADATA_RETRY_DELAY_SECONDS = 2.0
METADATA_SHUTDOWN_GRACE_SECONDS = 5.0
SLOW_FOREGROUND_REFRESH_SECONDS = 15.0
SLOW_METADATA_REFRESH_SECONDS = 30.0


def runtime_tracking_active(snapshot: DreameLawnMowerSnapshot) -> bool:
    """Prefer explicit heartbeat session state over legacy activity state."""
    activity = getattr(snapshot, "activity", None)
    state = getattr(snapshot, "state", None)
    if (
        getattr(snapshot, "docked", False)
        or state in {"charging", "charging_completed"}
    ):
        return False
    session_active = getattr(snapshot, "mowing_session_active", None)
    if session_active is not None:
        return bool(session_active)
    return activity in {"mowing", "paused", "returning"}


class DreameLawnMowerRefreshMixin:
    """Keep blocking state refreshes separate from optional metadata hydration."""

    def _snapshot_is_stale(self, snapshot: DreameLawnMowerSnapshot) -> bool:
        """Return whether a newer device snapshot already owns coordinator state."""
        stale_check = getattr(self, "_device_snapshot_is_stale", None)
        return bool(callable(stale_check) and stale_check(snapshot))

    def _snapshot_for_publication(
        self,
        snapshot: DreameLawnMowerSnapshot,
    ) -> DreameLawnMowerSnapshot:
        """Return current coordinator data instead of an older hydrated snapshot."""
        if not self._snapshot_is_stale(snapshot):
            return snapshot
        current = getattr(self, "data", None)
        if current is not None and not self._snapshot_is_stale(current):
            return current
        generations = getattr(self, "_device_snapshot_generations", {})
        published_generation = getattr(
            self,
            "_published_device_snapshot_generation",
            0,
        )
        for candidate, generation in generations.values():
            if generation == published_generation and not self._snapshot_is_stale(
                candidate
            ):
                return candidate
        raise UpdateFailed(
            "A newer mower state replaced this refresh before publication."
        )

    async def _async_update_data(self) -> DreameLawnMowerSnapshot:
        """Fetch essential state and hydrate optional metadata in the background."""
        if not hasattr(self, "performance"):
            self.performance = DreameLawnMowerPerformanceTracker()
        if not hasattr(self, "_device_refresh_lock"):
            self._device_refresh_lock = asyncio.Lock()
        cycle = self.performance.start("foreground_refresh")
        outcome = "completed"
        snapshot: DreameLawnMowerSnapshot | None = None
        try:
            try:
                async with self._device_refresh_lock:
                    snapshot = await cycle.measure(
                        "snapshot",
                        self.client.async_refresh,
                    )
                    if not snapshot.available:
                        retained = self._record_connectivity_failure(
                            "Mower is temporarily offline."
                        )
                        if retained is not None:
                            outcome = "degraded"
                            return retained
                    record_snapshot = getattr(
                        self,
                        "_record_device_snapshot",
                        None,
                    )
                    if callable(record_snapshot):
                        record_snapshot(snapshot, retain=True)
            except DreameLawnMowerConnectionError as err:
                outcome = type(err).__name__
                safe_error = sanitize_diagnostic_text(err)
                retained = self._record_connectivity_failure(err)
                if retained is not None:
                    outcome = "degraded"
                    return retained
                record_diagnostic_event(
                    self,
                    code="coordinator_update_failed",
                    source="coordinator",
                    message=safe_error,
                    context={"exception_type": type(err).__name__},
                )
                raise UpdateFailed(safe_error) from err

            if self._snapshot_is_stale(snapshot):
                return self._snapshot_for_publication(snapshot)

            if not snapshot.available:
                self._cancel_metadata_refresh()
                self.runtime_status_blob = None
                self._runtime_map_identity_verified = False
                self.client.update_runtime_live_tracking(None, active=False)
                return snapshot

            self._record_connectivity_success(snapshot)

            runtime_active = runtime_tracking_active(snapshot)
            if runtime_active:
                if not await self._async_refresh_active_runtime(cycle, snapshot):
                    return self._snapshot_for_publication(snapshot)
            else:
                self._runtime_map_identity_verified = False
                self.client.update_runtime_live_tracking(None, active=False)

            # Active runtime hydration yields to app-map and telemetry reads.
            # Commit the authoritative mission boundary only after those awaits
            # confirm that this foreground snapshot is still current.
            self._observe_runtime_mission_boundary(snapshot)
            self._schedule_metadata_refresh(
                refresh_map_and_runtime=not runtime_active,
            )
            return self._snapshot_for_publication(snapshot)
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as err:  # noqa: BLE001 - preserve coordinator availability
            outcome = type(err).__name__
            raise
        finally:
            release_snapshot = getattr(
                self,
                "_release_device_snapshot",
                None,
            )
            if snapshot is not None and callable(release_snapshot):
                release_snapshot(snapshot)
            self._foreground_refresh_count = (
                getattr(self, "_foreground_refresh_count", 0) + 1
            )
            sample = cycle.finish(outcome=outcome)
            self._log_performance_sample(
                sample,
                slow_after=SLOW_FOREGROUND_REFRESH_SECONDS,
                log_success_at_info=self._foreground_refresh_count == 1,
            )

    async def _async_refresh_active_runtime(
        self,
        cycle: DreameLawnMowerPerformanceCycle,
        snapshot: DreameLawnMowerSnapshot,
    ) -> bool:
        """Refresh authoritative map identity before publishing active tracking."""
        force_map_identity = not getattr(
            self,
            "_runtime_map_identity_verified",
            False,
        )
        previous_app_maps_refreshed_at = self.app_maps_refreshed_at
        await cycle.measure(
            "active_app_maps",
            lambda: self.async_refresh_app_maps(force=force_map_identity),
        )
        if self._snapshot_is_stale(snapshot):
            return False
        map_identity_refreshed = bool(
            getattr(self, "app_maps_refresh_succeeded", False)
            and (
                not force_map_identity
                or self.app_maps_refreshed_at is not previous_app_maps_refreshed_at
            )
        )
        runtime_map_index = (
            self._runtime_map_index() if map_identity_refreshed else None
        )
        runtime_current = await cycle.measure(
            "active_runtime_status",
            lambda: self._async_refresh_runtime_status(
                snapshot,
                runtime_map_index=runtime_map_index,
            ),
        )
        if not runtime_current or self._snapshot_is_stale(snapshot):
            return False
        self._runtime_map_identity_verified = map_identity_refreshed
        return True

    async def _async_refresh_runtime_status(
        self,
        snapshot: DreameLawnMowerSnapshot,
        *,
        runtime_map_index: int | None = None,
    ) -> bool:
        """Refresh optional runtime telemetry without failing the main snapshot."""
        runtime_active = runtime_tracking_active(snapshot)
        session_started_at = runtime_mission_session_started_at(
            self.runtime_telemetry_cache
        )
        session_identity = runtime_mission_cached_session_identity(
            self.runtime_telemetry_cache
        )
        observed_mission_generation = runtime_mission_session_generation(
            self.runtime_telemetry_cache
        )
        mission_active = runtime_mission_session_active(
            snapshot,
            tracking_active=runtime_active,
            session_started_at=session_started_at,
            session_identity=session_identity,
        )
        if runtime_map_index is None and not runtime_active:
            runtime_map_index = self._runtime_map_index()
        try:
            runtime_status_blob = await self.client.async_get_runtime_status_blob(
                refresh=False,
                include_cloud=True,
            )
            if self._snapshot_is_stale(snapshot) or (
                observed_mission_generation is not None
                and runtime_mission_session_generation(
                    self.runtime_telemetry_cache
                )
                != observed_mission_generation
            ):
                return False
            self.runtime_status_blob = runtime_status_blob
            self.runtime_telemetry_cache.update(
                self.runtime_status_blob,
                allow_zero=runtime_active,
                active_session=mission_active,
                completion_confirmed=runtime_mission_completion_confirmed(
                    snapshot,
                    tracking_active=mission_active,
                    session_started_at=session_started_at,
                    session_identity=session_identity,
                ),
                completion_rejected=runtime_mission_completion_rejected(
                    snapshot,
                    session_started_at=session_started_at,
                    session_identity=session_identity,
                ),
                new_session=runtime_mission_new_session(snapshot),
                new_session_event_at=runtime_mission_session_event_at(
                    snapshot,
                    active_session=mission_active,
                ),
                new_session_evidence=runtime_mission_new_session_evidence(snapshot),
                session_identity=runtime_mission_session_identity(
                    snapshot,
                    session_started_at=session_started_at,
                    cached_session_identity=session_identity,
                ),
            )
            self.client.update_runtime_live_tracking(
                self.runtime_status_blob,
                active=runtime_active,
                map_index=runtime_map_index,
            )
            return True
        except Exception as err:  # noqa: BLE001 - best-effort extra metadata
            if self._snapshot_is_stale(snapshot) or (
                observed_mission_generation is not None
                and runtime_mission_session_generation(
                    self.runtime_telemetry_cache
                )
                != observed_mission_generation
            ):
                return False
            _LOGGER.debug("Failed to refresh runtime status blob: %s", err)
            self.runtime_status_blob = None
            self.client.update_runtime_live_tracking(
                None,
                active=runtime_active,
                map_index=runtime_map_index,
            )
            return True

    async def _async_refresh_bluetooth_state(self) -> None:
        """Refresh optional Bluetooth connection state."""
        try:
            self.bluetooth_connected = await self.client.async_get_bluetooth_connected(
                refresh=False,
                include_cloud=True,
            )
        except Exception as err:  # noqa: BLE001 - best-effort extra metadata
            _LOGGER.debug("Failed to refresh Bluetooth connection state: %s", err)
            self.bluetooth_connected = None

    def _schedule_metadata_refresh(
        self,
        *,
        refresh_map_and_runtime: bool,
    ) -> None:
        """Start one coalesced optional-metadata hydration task."""
        if getattr(self, "_shutting_down", False) or not hasattr(self, "hass"):
            return
        task = getattr(self, "_metadata_refresh_task", None)
        if task is not None and not task.done():
            self._metadata_refresh_pending = True
            return
        self._metadata_refresh_pending = False
        self._metadata_refresh_publish = True
        self._metadata_refresh_task = create_background_task(
            self.hass,
            self._async_refresh_metadata(
                refresh_map_and_runtime=refresh_map_and_runtime,
            ),
            f"{DOMAIN}-metadata-refresh",
        )

    async def _async_refresh_metadata(
        self,
        *,
        refresh_map_and_runtime: bool,
    ) -> None:
        """Hydrate independent optional metadata with bounded concurrency."""
        cycle = self.performance.start("metadata_refresh")
        outcome = "completed"
        current_task = asyncio.current_task()
        tasks: list[asyncio.Task[Any]] = []
        try:
            core_operations: list[
                tuple[str, Callable[[], Awaitable[Any]]]
            ] = []
            if refresh_map_and_runtime:
                core_operations.append(
                    (
                        "app_maps",
                        lambda: self.async_refresh_app_maps(force=False),
                    )
                )

            operations: tuple[
                tuple[str, Callable[[], Awaitable[Any]]],
                ...,
            ] = (
                ("bluetooth", self._async_refresh_bluetooth_state),
                (
                    "firmware",
                    lambda: self.async_refresh_firmware_update_support(force=False),
                ),
                (
                    "app_map_objects",
                    lambda: self.async_refresh_app_map_objects(force=False),
                ),
                (
                    "vector_map",
                    lambda: self.async_refresh_vector_map_details(force=False),
                ),
                (
                    "weather",
                    lambda: self.async_refresh_weather_protection(force=False),
                ),
                (
                    "maintenance",
                    lambda: self.async_refresh_maintenance_status(force=False),
                ),
                (
                    "work_log_totals",
                    lambda: self.async_refresh_work_log_totals(force=False),
                ),
                (
                    "voice",
                    lambda: self.async_refresh_voice_settings(force=False),
                ),
            )

            core_operations.extend(operations)
            tasks = [
                asyncio.create_task(
                    self._async_run_metadata_phase(cycle, phase, operation)
                )
                for phase, operation in core_operations
            ]

            core_results = await asyncio.gather(*tasks, return_exceptions=True)
            core_unexpected = [
                result
                for result in core_results
                if isinstance(result, Exception)
                and not isinstance(result, asyncio.CancelledError)
            ]
            if core_unexpected:
                outcome = "partial"
                _LOGGER.debug(
                    "Optional mower core metadata refresh had unexpected failures: %s",
                    ", ".join(type(error).__name__ for error in core_unexpected),
                )

            if not self._shutting_down and getattr(
                self, "_metadata_refresh_publish", True
            ):
                self.async_update_listeners()

            retry_operations: list[
                tuple[str, Callable[[], Awaitable[Any]]]
            ] = []
            for (phase, operation), result in zip(
                core_operations,
                core_results,
                strict=True,
            ):
                if not self._metadata_phase_needs_retry(phase, result):
                    continue
                if phase == "app_maps":
                    retry_operation = partial(
                        self.async_refresh_app_maps,
                        force=True,
                    )
                else:
                    retry_operation = operation
                retry_operations.append(
                    (phase, retry_operation)
                )
            if retry_operations:
                await asyncio.sleep(METADATA_RETRY_DELAY_SECONDS)
                tasks = [
                    asyncio.create_task(
                        self._async_run_metadata_phase(
                            cycle,
                            f"{phase}_retry",
                            operation,
                        )
                    )
                    for phase, operation in retry_operations
                ]
                retry_results = await asyncio.gather(
                    *tasks,
                    return_exceptions=True,
                )
                retry_failures = [
                    result
                    for (phase, _operation), result in zip(
                        retry_operations,
                        retry_results,
                        strict=True,
                    )
                    if self._metadata_phase_needs_retry(phase, result)
                ]
                if retry_failures:
                    outcome = "partial"
                    _LOGGER.debug(
                        "Optional mower core metadata remained incomplete "
                        "after one bounded retry: %s",
                        ", ".join(
                            type(error).__name__
                            if isinstance(error, Exception)
                            else "empty"
                            for error in retry_failures
                        ),
                    )
                if (
                    not self._shutting_down
                    and getattr(self, "_metadata_refresh_publish", True)
                ):
                    self.async_update_listeners()

            tasks = [
                asyncio.create_task(
                    self._async_run_metadata_phase(
                        cycle,
                        "schedules",
                        lambda: self.async_refresh_schedules(force=False),
                    )
                ),
                asyncio.create_task(
                    self._async_run_metadata_phase(
                        cycle,
                        "batch_device_data",
                        lambda: self.async_refresh_batch_device_data(force=False),
                    )
                ),
            ]
            schedule_results = await asyncio.gather(*tasks, return_exceptions=True)
            schedule_unexpected = [
                result
                for result in schedule_results
                if isinstance(result, Exception)
                and not isinstance(result, asyncio.CancelledError)
            ]
            if schedule_unexpected:
                outcome = "partial"
                _LOGGER.debug(
                    "Optional mower schedule metadata refresh had unexpected "
                    "failures: %s",
                    ", ".join(type(error).__name__ for error in schedule_unexpected),
                )
        except asyncio.CancelledError:
            outcome = "cancelled"
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        except Exception as err:  # noqa: BLE001 - background task must not escape
            outcome = type(err).__name__
            _LOGGER.debug(
                "Optional mower metadata refresh failed: %s",
                sanitize_diagnostic_text(err),
            )
        finally:
            self._metadata_refresh_count = (
                getattr(self, "_metadata_refresh_count", 0) + 1
            )
            sample = cycle.finish(outcome=outcome)
            self._log_performance_sample(
                sample,
                slow_after=SLOW_METADATA_REFRESH_SECONDS,
                log_success_at_info=self._metadata_refresh_count == 1,
            )
            if self._metadata_refresh_task is current_task:
                self._metadata_refresh_task = None
            if (
                not self._shutting_down
                and outcome != "cancelled"
                and getattr(self, "_metadata_refresh_publish", True)
            ):
                self.async_update_listeners()
            if (
                not self._shutting_down
                and outcome != "cancelled"
                and getattr(self, "_metadata_refresh_pending", False)
            ):
                self._schedule_metadata_refresh(
                    refresh_map_and_runtime=True,
                )

    def _metadata_phase_needs_retry(self, phase: str, result: Any) -> bool:
        """Return whether one core phase has not populated its cache yet."""
        if phase == "app_maps":
            if not hasattr(self, "app_maps_refresh_succeeded"):
                return False
            if not self.app_maps_refresh_succeeded:
                return True
            app_maps = getattr(self, "app_maps", None)
            if (
                not isinstance(app_maps, Mapping)
                or app_maps.get("map_list_valid") is not True
            ):
                return True
            current_index = active_map_index(app_maps)
            maps = app_maps.get("maps")
            if not isinstance(maps, Sequence):
                return False
            if current_index is None:
                selected_index = getattr(self, "selected_map_index", None)
                if (
                    isinstance(selected_index, int)
                    and not isinstance(selected_index, bool)
                    and selected_index >= 0
                    and any(
                        isinstance(item, Mapping)
                        and item.get("idx") == selected_index
                        and item.get("created") is not False
                        for item in maps
                    )
                ):
                    current_index = selected_index
            if current_index is None:
                return False
            return not any(
                isinstance(item, Mapping)
                and item.get("idx") == current_index
                and bool(item.get("available"))
                for item in maps
            )
        refreshed_at_by_phase = {
            "firmware": "firmware_update_support_refreshed_at",
            "app_map_objects": "app_map_objects_refreshed_at",
            "vector_map": "vector_map_details_refreshed_at",
            "weather": "weather_protection_refreshed_at",
            "maintenance": "maintenance_status_refreshed_at",
            "work_log_totals": "work_log_totals_refreshed_at",
            "voice": "voice_settings_refreshed_at",
        }
        marker = refreshed_at_by_phase.get(phase)
        if marker is None or not hasattr(self, marker):
            return False
        return isinstance(result, Exception) or getattr(self, marker) is None

    async def _async_run_metadata_phase(
        self,
        cycle: DreameLawnMowerPerformanceCycle,
        phase: str,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Measure one optional operation after entering the concurrency budget."""
        async with self._metadata_refresh_semaphore:
            return await cycle.measure(phase, operation)

    def _cancel_metadata_refresh(self) -> None:
        """Suppress publication while an in-flight vendor call drains safely."""
        self._metadata_refresh_pending = False
        self._metadata_refresh_publish = False

    async def _async_drain_metadata_for_shutdown(self) -> bool:
        """Cancel queued metadata and bound the wait for an in-flight request."""
        if getattr(self, "_home_assistant_stopping", False):
            return False
        metadata_task = self._metadata_refresh_task
        if metadata_task is None or metadata_task is asyncio.current_task():
            return await self._async_drain_batch_schedule_for_shutdown()

        close_task = getattr(self, "_metadata_shutdown_close_task", None)
        if close_task is not None and not close_task.done():
            return False

        metadata_task.cancel()
        try:
            async with asyncio.timeout(METADATA_SHUTDOWN_GRACE_SECONDS):
                await asyncio.shield(metadata_task)
        except asyncio.CancelledError:
            if not metadata_task.done():
                raise
        except TimeoutError:
            if getattr(self, "_home_assistant_stopping", False):
                return False
            self._metadata_shutdown_close_task = self.hass.async_create_task(
                self._async_close_after_metadata(metadata_task),
                f"{DOMAIN}-metadata-shutdown",
            )
            return False
        return await self._async_drain_batch_schedule_for_shutdown()

    async def _async_drain_batch_schedule_for_shutdown(self) -> bool:
        """Wait for shared batch reads without cancelling their worker threads."""
        if getattr(self, "_home_assistant_stopping", False):
            return False
        tasks = set(getattr(self, "_batch_schedule_read_tasks", set()))
        latest = getattr(self, "_batch_schedule_read_task", None)
        if latest is not None:
            tasks.add(latest)
        tasks.discard(asyncio.current_task())
        if not tasks:
            return True

        drain_task = asyncio.create_task(self._async_wait_batch_schedule_reads())
        try:
            async with asyncio.timeout(METADATA_SHUTDOWN_GRACE_SECONDS):
                await asyncio.shield(drain_task)
        except asyncio.CancelledError:
            if not drain_task.done():
                raise
        except TimeoutError:
            if getattr(self, "_home_assistant_stopping", False):
                return False
            self._metadata_shutdown_close_task = self.hass.async_create_task(
                self._async_close_after_metadata(drain_task),
                f"{DOMAIN}-batch-schedule-shutdown",
            )
            return False
        return True

    async def _async_wait_batch_schedule_reads(self) -> None:
        """Wait until every tracked batch worker reaches a terminal result."""
        while True:
            tasks = set(getattr(self, "_batch_schedule_read_tasks", set()))
            latest = getattr(self, "_batch_schedule_read_task", None)
            if latest is not None:
                tasks.add(latest)
            tasks.discard(asyncio.current_task())
            if not tasks:
                return

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception) and not isinstance(
                    result,
                    asyncio.CancelledError,
                ):
                    _LOGGER.debug(
                        "Batch schedule task failed during shutdown: %s",
                        result,
                    )
            tracked_tasks = getattr(self, "_batch_schedule_read_tasks", None)
            if tracked_tasks is not None:
                tracked_tasks.difference_update(tasks)
            if getattr(self, "_batch_schedule_read_task", None) in tasks:
                self._batch_schedule_read_task = None
                self._batch_schedule_read_key = None
                self._batch_schedule_read_completed_at = None

    async def _async_close_after_metadata(
        self,
        metadata_task: asyncio.Task[Any],
    ) -> None:
        """Close the shared client once a cancellation-resistant request drains."""
        try:
            with suppress(asyncio.CancelledError, Exception):
                await metadata_task
        finally:
            await self._async_wait_batch_schedule_reads()
            await self.client.async_close()

    @staticmethod
    def _log_performance_sample(
        sample: DreameLawnMowerPerformanceSample,
        *,
        slow_after: float,
        log_success_at_info: bool,
    ) -> None:
        """Log deterministic privacy-safe timings at an appropriate level."""
        total, phases = format_performance_sample(sample)
        message = (
            "Dreame mower performance: operation=%s outcome=%s total=%.3fs phases=[%s]"
        )
        args = (sample.operation, sample.outcome, total, phases)
        if total >= slow_after:
            _LOGGER.warning(message, *args)
        elif log_success_at_info:
            _LOGGER.info(message, *args)
        else:
            _LOGGER.debug(message, *args)

    def _runtime_map_index(self) -> int | None:
        """Return the map identity used to scope transient runtime overlays."""
        return active_map_index(
            self.app_maps,
            selected_map_index=self.selected_map_index,
        )
