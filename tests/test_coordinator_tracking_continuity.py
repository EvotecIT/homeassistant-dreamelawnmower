"""Live map hydration must tolerate pose updates without crossing missions."""

import asyncio
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
    "transition", ["pose", "mission", "docked", "offline", "evicted", "evicted_error"]
)
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
        coordinator.runtime_status_blob = None
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
        blob = SimpleNamespace(candidate_runtime_area_progress_percent=42.0)

        async def read_runtime(**kwargs: object) -> object:
            if transition.startswith("evicted"):
                # Simulate updates while the real telemetry read is pending.
                await asyncio.sleep(0)
                for _ in range(DEVICE_SNAPSHOT_GENERATION_HISTORY + 2):
                    docked = SimpleNamespace(
                        available=True, docked=True, mowing_session_active=False
                    )
                    coordinator._record_device_snapshot(docked)
                    coordinator._published_device_snapshot_generation = (
                        coordinator._device_snapshot_generation
                    )
                    coordinator.data = docked
                assert id(newest) not in coordinator._device_snapshot_generations
                assert coordinator.runtime_telemetry_cache._session_generation == 0
                if transition == "evicted_error":
                    raise RuntimeError("Delayed read failed")
            return blob

        coordinator.client = SimpleNamespace(
            async_get_runtime_status_blob=AsyncMock(side_effect=read_runtime),
            update_runtime_live_tracking=Mock(),
        )
        tracker = DreameLawnMowerPerformanceTracker()
        result = await coordinator._async_refresh_active_runtime(
            tracker.start("test"), original
        )

        # The outer refresh must publish newest, never its obsolete snapshot.
        assert result is False
        assert coordinator._snapshot_for_publication(original) is coordinator.data
        if transition == "pose":
            coordinator.client.update_runtime_live_tracking.assert_called_once_with(
                blob, active=True, map_index=0
            )
            assert coordinator.runtime_status_blob is blob
            assert coordinator.runtime_telemetry_cache.blob is blob
            assert coordinator._runtime_map_identity_verified is True
        else:
            coordinator.client.update_runtime_live_tracking.assert_not_called()
            assert coordinator.runtime_status_blob is None
            assert coordinator.runtime_telemetry_cache.blob is None
            assert coordinator._runtime_map_identity_verified is False

    asyncio.run(scenario())
