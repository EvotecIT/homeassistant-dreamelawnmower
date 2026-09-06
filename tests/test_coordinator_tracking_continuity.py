"""Live map hydration must tolerate pose updates without crossing missions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.dreame_lawn_mower.coordinator import DreameLawnMowerCoordinator
from custom_components.dreame_lawn_mower.performance import (
    DreameLawnMowerPerformanceTracker,
)
from custom_components.dreame_lawn_mower.runtime_cache import (
    DreameLawnMowerRuntimeTelemetryCache,
)


@pytest.mark.parametrize("transition", ["pose", "mission", "docked", "offline"])
def test_slow_map_read_hydrates_only_current_same_mission(transition: str) -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        original = SimpleNamespace(
            available=True, activity="mowing", mowing_session_active=True
        )
        newest = SimpleNamespace(
            available=transition != "offline",
            activity="docked" if transition == "docked" else "mowing",
            docked=transition == "docked",
            mowing_session_active=transition != "docked",
        )
        coordinator.runtime_telemetry_cache = DreameLawnMowerRuntimeTelemetryCache()
        coordinator._runtime_map_identity_verified = False
        coordinator.app_maps_refreshed_at = object()
        coordinator.app_maps_refresh_succeeded = False
        coordinator.app_maps = {"current_map_index": 0}
        coordinator.selected_map_index = 0
        coordinator._record_device_snapshot(original, retain=True)
        coordinator._published_device_snapshot_generation = 1
        coordinator.data = original

        async def read_map(*, force: bool) -> None:
            assert force is True
            coordinator.app_maps_refreshed_at = object()
            coordinator.app_maps_refresh_succeeded = True
            coordinator._record_device_snapshot(newest)
            coordinator._published_device_snapshot_generation = (
                coordinator._device_snapshot_generation
            )
            coordinator.data = newest
            if transition == "mission":
                coordinator.runtime_telemetry_cache._session_generation += 1

        coordinator.async_refresh_app_maps = read_map
        coordinator._async_refresh_runtime_status = AsyncMock(return_value=True)
        tracker = DreameLawnMowerPerformanceTracker()
        result = await coordinator._async_refresh_active_runtime(
            tracker.start("test"), original
        )

        # The outer refresh must publish newest, never its obsolete snapshot.
        assert result is False
        assert coordinator._snapshot_for_publication(original) is newest
        if transition == "pose":
            coordinator._async_refresh_runtime_status.assert_awaited_once_with(
                newest, runtime_map_index=0
            )
            assert coordinator._runtime_map_identity_verified is True
        else:
            coordinator._async_refresh_runtime_status.assert_not_awaited()
            assert coordinator._runtime_map_identity_verified is False

    asyncio.run(scenario())
