"""Foreground and background refresh lifecycle for the mower coordinator."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
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
from .performance import (
    DreameLawnMowerPerformanceCycle,
    DreameLawnMowerPerformanceSample,
    DreameLawnMowerPerformanceTracker,
    format_performance_sample,
)

_LOGGER = logging.getLogger(__name__)

# The reverse-engineered cloud owner shares request ids, login state, and one
# requests session. Keep its background calls serialized until that owner
# provides an explicit concurrency contract.
METADATA_REFRESH_CONCURRENCY = 1
METADATA_SHUTDOWN_GRACE_SECONDS = 5.0
SLOW_FOREGROUND_REFRESH_SECONDS = 15.0
SLOW_METADATA_REFRESH_SECONDS = 30.0


def runtime_tracking_active(snapshot: DreameLawnMowerSnapshot) -> bool:
    """Prefer explicit heartbeat session state over legacy activity state."""
    session_active = getattr(snapshot, "mowing_session_active", None)
    if session_active is not None:
        return bool(session_active)
    return getattr(snapshot, "activity", None) in {"mowing", "paused", "returning"}


class DreameLawnMowerRefreshMixin:
    """Keep blocking state refreshes separate from optional metadata hydration."""

    async def _async_update_data(self) -> DreameLawnMowerSnapshot:
        """Fetch essential state and hydrate optional metadata in the background."""
        if not hasattr(self, "performance"):
            self.performance = DreameLawnMowerPerformanceTracker()
        cycle = self.performance.start("foreground_refresh")
        outcome = "completed"
        try:
            try:
                snapshot = await cycle.measure("snapshot", self.client.async_refresh)
            except DreameLawnMowerConnectionError as err:
                outcome = type(err).__name__
                safe_error = sanitize_diagnostic_text(err)
                record_diagnostic_event(
                    self,
                    code="coordinator_update_failed",
                    source="coordinator",
                    message=safe_error,
                    context={"exception_type": type(err).__name__},
                )
                raise UpdateFailed(safe_error) from err

            if not snapshot.available:
                self._cancel_metadata_refresh()
                self.runtime_status_blob = None
                self._runtime_map_identity_verified = False
                self.client.update_runtime_live_tracking(None, active=False)
                return snapshot

            runtime_active = runtime_tracking_active(snapshot)
            if runtime_active:
                await self._async_refresh_active_runtime(cycle, snapshot)
            else:
                self._runtime_map_identity_verified = False
                self.client.update_runtime_live_tracking(None, active=False)

            self._schedule_metadata_refresh(
                refresh_map_and_runtime=not runtime_active,
            )
            return snapshot
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as err:  # noqa: BLE001 - preserve coordinator availability
            outcome = type(err).__name__
            raise
        finally:
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
    ) -> None:
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
        map_identity_refreshed = bool(
            getattr(self, "app_maps_refresh_succeeded", False)
            and (
                not force_map_identity
                or self.app_maps_refreshed_at is not previous_app_maps_refreshed_at
            )
        )
        self._runtime_map_identity_verified = map_identity_refreshed
        runtime_map_index = (
            self._runtime_map_index() if map_identity_refreshed else None
        )
        await cycle.measure(
            "active_runtime_status",
            lambda: self._async_refresh_runtime_status(
                snapshot,
                runtime_map_index=runtime_map_index,
            ),
        )

    async def _async_refresh_runtime_status(
        self,
        snapshot: DreameLawnMowerSnapshot,
        *,
        runtime_map_index: int | None = None,
    ) -> None:
        """Refresh optional runtime telemetry without failing the main snapshot."""
        runtime_active = runtime_tracking_active(snapshot)
        if runtime_map_index is None and not runtime_active:
            runtime_map_index = self._runtime_map_index()
        try:
            self.runtime_status_blob = await self.client.async_get_runtime_status_blob(
                refresh=False,
                include_cloud=True,
            )
            self.runtime_telemetry_cache.update(
                self.runtime_status_blob,
                allow_zero=runtime_active,
                active_session=runtime_active,
            )
            self.client.update_runtime_live_tracking(
                self.runtime_status_blob,
                active=runtime_active,
                map_index=runtime_map_index,
            )
        except Exception as err:  # noqa: BLE001 - best-effort extra metadata
            _LOGGER.debug("Failed to refresh runtime status blob: %s", err)
            self.runtime_status_blob = None
            self.client.update_runtime_live_tracking(
                None,
                active=runtime_active,
                map_index=runtime_map_index,
            )

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
        self._metadata_refresh_task = self.hass.async_create_task(
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
            if refresh_map_and_runtime:
                app_maps_task = asyncio.create_task(
                    self._async_run_metadata_phase(
                        cycle,
                        "app_maps",
                        lambda: self.async_refresh_app_maps(force=False),
                    )
                )
                tasks.append(app_maps_task)
            else:
                app_maps_task = None

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
                    "voice",
                    lambda: self.async_refresh_voice_settings(force=False),
                ),
            )

            tasks.extend(
                asyncio.create_task(
                    self._async_run_metadata_phase(cycle, phase, operation)
                )
                for phase, operation in operations
            )

            if app_maps_task is not None:
                await app_maps_task
            tasks.extend(
                (
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
                )
            )
            results = await asyncio.gather(*tasks, return_exceptions=True)
            unexpected = [
                result
                for result in results
                if isinstance(result, Exception)
                and not isinstance(result, asyncio.CancelledError)
            ]
            if unexpected:
                outcome = "partial"
                _LOGGER.debug(
                    "Optional mower metadata refresh had unexpected failures: %s",
                    ", ".join(type(error).__name__ for error in unexpected),
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
        metadata_task = self._metadata_refresh_task
        if metadata_task is None or metadata_task is asyncio.current_task():
            return True

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
            self._metadata_shutdown_close_task = self.hass.async_create_task(
                self._async_close_after_metadata(metadata_task),
                f"{DOMAIN}-metadata-shutdown",
            )
            return False
        return True

    async def _async_close_after_metadata(
        self,
        metadata_task: asyncio.Task[None],
    ) -> None:
        """Close the shared client once a cancellation-resistant request drains."""
        try:
            with suppress(asyncio.CancelledError):
                await metadata_task
        finally:
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
            "Dreame mower performance: operation=%s outcome=%s total=%.3fs "
            "phases=[%s]"
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
