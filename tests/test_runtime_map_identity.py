"""Live map identity must stay independent of geometry and stale reads."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.dreame_lawn_mower.coordinator import DreameLawnMowerCoordinator
from custom_components.dreame_lawn_mower.performance import (
    DreameLawnMowerPerformanceTracker,
)
from custom_components.dreame_lawn_mower.runtime_cache import (
    DreameLawnMowerRuntimeTelemetryCache,
)


def _coordinator():
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.app_maps = {"current_map_index": 2}
    coordinator.app_maps_refreshed_at = datetime.now(UTC)
    coordinator.selected_map_index = 2
    coordinator.runtime_status_blob = None
    coordinator.runtime_telemetry_cache = DreameLawnMowerRuntimeTelemetryCache()
    coordinator.client = SimpleNamespace(
        async_get_current_app_map_index=AsyncMock(return_value=2),
        async_get_runtime_status_blob=AsyncMock(return_value=None),
        update_runtime_live_tracking=Mock(),
    )
    return coordinator


def test_active_tracking_does_not_wait_for_geometry_download():
    async def scenario():
        coordinator = _coordinator()
        started, release = asyncio.Event(), asyncio.Event()

        async def hydrate(*, force):
            started.set()
            await release.wait()

        coordinator.async_refresh_app_maps = hydrate
        hydration = asyncio.create_task(hydrate(force=False))
        await started.wait()
        snapshot = SimpleNamespace(
            available=True, activity="mowing", mowing_session_active=True
        )
        try:
            assert await asyncio.wait_for(
                coordinator._async_refresh_active_runtime(
                    DreameLawnMowerPerformanceTracker().start("test"), snapshot
                ), timeout=1,
            )
            assert not hydration.done()
            coordinator.client.update_runtime_live_tracking.assert_called_once_with(
                None, active=True, map_index=2
            )
        finally:
            release.set()
            await hydration

    asyncio.run(scenario())


@pytest.mark.parametrize("index", [None, 0, 3])
def test_identity_refresh_expires_changed_geometry_and_reuses_fresh_read(index):
    async def scenario():
        coordinator = _coordinator()
        coordinator.client.async_get_current_app_map_index.return_value = index
        assert await coordinator._async_refresh_runtime_map_index(force=True) == (
            True, index
        )
        assert coordinator.app_maps_refreshed_at is None
        assert await coordinator._async_refresh_runtime_map_index(force=False) == (
            True, index
        )
        coordinator.client.async_get_current_app_map_index.assert_awaited_once()
        coordinator._runtime_map_index_refreshed_at -= timedelta(seconds=61)
        await coordinator._async_refresh_runtime_map_index(force=False)
        assert coordinator.client.async_get_current_app_map_index.await_count == 2

    asyncio.run(scenario())


def test_overlapping_forced_identity_reads_share_one_completed_request():
    async def scenario():
        coordinator = _coordinator()
        started, release = asyncio.Event(), asyncio.Event()

        async def read():
            started.set()
            await release.wait()
            return 2

        coordinator.client.async_get_current_app_map_index.side_effect = read
        first = asyncio.create_task(
            coordinator._async_refresh_runtime_map_index(force=True)
        )
        await started.wait()
        second = asyncio.create_task(
            coordinator._async_refresh_runtime_map_index(force=True)
        )
        await asyncio.sleep(0)
        release.set()
        assert await first == await second == (True, 2)
        coordinator.client.async_get_current_app_map_index.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["identity", "telemetry"])
def test_invalidated_map_read_cannot_restore_old_tracking(boundary):
    async def scenario():
        coordinator = _coordinator()
        started, release = asyncio.Event(), asyncio.Event()

        async def read(*args, **kwargs):
            started.set()
            await release.wait()
            return 2 if boundary == "identity" else None

        method = (
            coordinator.client.async_get_current_app_map_index
            if boundary == "identity"
            else coordinator.client.async_get_runtime_status_blob
        )
        method.side_effect = read
        snapshot = SimpleNamespace(
            available=True, activity="mowing", mowing_session_active=True
        )
        task = asyncio.create_task(coordinator._async_refresh_active_runtime(
            DreameLawnMowerPerformanceTracker().start("test"), snapshot
        ))
        await started.wait()
        coordinator._invalidate_runtime_map_identity()
        release.set()
        assert await task is False
        assert coordinator._runtime_map_identity_verified is False
        assert coordinator._runtime_map_index_refreshed_at is None
        coordinator.client.update_runtime_live_tracking.assert_not_called()

    asyncio.run(scenario())
