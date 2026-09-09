"""Realtime positions must acquire map identity without waiting for a poll."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.dreame_lawn_mower.coordinator import (
    DEVICE_SNAPSHOT_GENERATION_HISTORY,
    DreameLawnMowerCoordinator,
)
from custom_components.dreame_lawn_mower.performance import (
    DreameLawnMowerPerformanceTracker,
)
from custom_components.dreame_lawn_mower.runtime_cache import (
    DreameLawnMowerRuntimeTelemetryCache,
)


@pytest.mark.parametrize(
    "active,verified", [(True, False), (True, True), (False, False)]
)
def test_realtime_update_requests_missing_active_map_identity(active, verified):
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    snapshot = SimpleNamespace(
        available=True,
        activity="mowing" if active else "docked",
        docked=not active,
        mowing_session_active=active,
    )
    coordinator._shutting_down = False
    coordinator._runtime_map_identity_verified = verified
    coordinator._runtime_map_index_refreshed_at = (
        datetime.now(UTC) if verified else None
    )
    coordinator._last_device_settings_event_at = None
    coordinator.runtime_telemetry_cache = DreameLawnMowerRuntimeTelemetryCache()
    coordinator.app_maps = {"current_map_index": 0}
    coordinator.selected_map_index = 0
    coordinator.client = SimpleNamespace(
        async_get_cached_snapshot=AsyncMock(return_value=snapshot),
        async_get_runtime_status_blob=AsyncMock(return_value=None),
        async_get_bluetooth_connected=AsyncMock(return_value=False),
        update_runtime_live_tracking=Mock(),
    )
    coordinator.async_set_updated_data = Mock()
    coordinator._schedule_metadata_refresh = Mock()
    asyncio.run(coordinator._async_process_client_update())
    coordinator.async_set_updated_data.assert_called_once_with(snapshot)
    if active and not verified:
        coordinator._schedule_metadata_refresh.assert_called_once_with(
            refresh_map_and_runtime=True
        )
    else:
        coordinator._schedule_metadata_refresh.assert_not_called()


@pytest.mark.parametrize("active_at_execution", [True, False])
def test_background_map_phase_uses_latest_state_without_foreground_poll(
    active_at_execution,
):
    async def scenario():
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        coordinator.data = SimpleNamespace(
            available=True, activity="mowing", mowing_session_active=True
        )
        coordinator.performance = DreameLawnMowerPerformanceTracker()
        coordinator._shutting_down = False
        coordinator._metadata_refresh_task = None
        coordinator._metadata_refresh_semaphore = asyncio.Semaphore(0)
        coordinator.async_update_listeners = Mock()
        coordinator.async_refresh_app_maps = AsyncMock()
        coordinator._async_refresh_active_runtime = AsyncMock(return_value=True)
        for name in (
            "_async_refresh_bluetooth_state",
            "async_refresh_firmware_update_support",
            "async_refresh_app_map_objects",
            "async_refresh_vector_map_details",
            "async_refresh_weather_protection",
            "async_refresh_maintenance_status",
            "async_refresh_work_log_totals",
            "async_refresh_voice_settings",
            "async_refresh_schedules",
            "async_refresh_batch_device_data",
        ):
            setattr(coordinator, name, AsyncMock())
        task = asyncio.create_task(
            coordinator._async_refresh_metadata(refresh_map_and_runtime=True)
        )
        coordinator._metadata_refresh_task = task
        await asyncio.sleep(0)
        coordinator.data = SimpleNamespace(
            available=True,
            activity="mowing" if active_at_execution else "docked",
            docked=not active_at_execution,
            mowing_session_active=active_at_execution,
        )
        coordinator._metadata_refresh_semaphore.release()
        await task
        if active_at_execution:
            coordinator._async_refresh_active_runtime.assert_awaited_once()
            assert (
                coordinator._async_refresh_active_runtime.call_args.args[1]
                is coordinator.data
            )
        else:
            coordinator._async_refresh_active_runtime.assert_not_awaited()
        coordinator.async_refresh_app_maps.assert_awaited_once_with(force=False)

    asyncio.run(scenario())


@pytest.mark.parametrize("transition", ["docked", "mission", "pose"])
def test_background_map_read_cannot_revive_an_evicted_snapshot(transition):
    async def scenario():
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        original = SimpleNamespace(
            available=True, activity="mowing", mowing_session_active=True
        )
        coordinator.runtime_telemetry_cache = DreameLawnMowerRuntimeTelemetryCache()
        coordinator._runtime_map_identity_verified = False
        coordinator.app_maps_refreshed_at = object()
        coordinator.app_maps_refresh_succeeded = False
        coordinator.app_maps = {"current_map_index": 0}
        coordinator.selected_map_index = 0
        coordinator._record_device_snapshot(original)
        coordinator._published_device_snapshot_generation = 1
        coordinator.data = original

        async def read_map():
            coordinator.app_maps_refreshed_at = object()
            coordinator.app_maps_refresh_succeeded = True
            for _ in range(DEVICE_SNAPSHOT_GENERATION_HISTORY + 2):
                newest = SimpleNamespace(
                    available=True,
                    activity="docked" if transition == "docked" else "mowing",
                    docked=transition == "docked",
                    mowing_session_active=transition != "docked",
                )
                coordinator._record_device_snapshot(newest)
                coordinator._published_device_snapshot_generation = (
                    coordinator._device_snapshot_generation
                )
                coordinator.data = newest
            if transition == "mission":
                coordinator.runtime_telemetry_cache.begin_new_session(
                    session_started_at=100.0
                )
            assert id(original) not in coordinator._device_snapshot_generations
            return 0

        coordinator.async_refresh_app_maps = AsyncMock(return_value={})
        blob = SimpleNamespace(candidate_runtime_area_progress_percent=42.0)
        coordinator.client = SimpleNamespace(
            async_get_current_app_map_index=read_map,
            async_get_runtime_status_blob=AsyncMock(return_value=blob),
            update_runtime_live_tracking=Mock(),
        )
        coordinator._async_refresh_runtime_status = AsyncMock(
            wraps=coordinator._async_refresh_runtime_status
        )
        result = await coordinator._async_refresh_background_map_runtime(
            DreameLawnMowerPerformanceTracker().start("test")
        )
        assert result == {}
        if transition == "pose":
            assert (
                coordinator._async_refresh_runtime_status.call_args.args[0]
                is coordinator.data
            )
            coordinator.client.update_runtime_live_tracking.assert_called_once_with(
                blob, active=True, map_index=0
            )
        else:
            coordinator.client.update_runtime_live_tracking.assert_not_called()
            assert coordinator.runtime_telemetry_cache.blob is None

    asyncio.run(scenario())
