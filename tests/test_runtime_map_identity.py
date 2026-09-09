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


def test_background_geometry_does_not_repeat_verified_active_telemetry():
    async def scenario():
        coordinator = _coordinator()
        coordinator.data = SimpleNamespace(
            available=True, activity="mowing", mowing_session_active=True
        )
        coordinator._runtime_map_identity_verified = True
        coordinator._async_refresh_active_runtime = AsyncMock()
        coordinator.async_refresh_app_maps = AsyncMock()
        await coordinator._async_refresh_background_map_runtime(
            DreameLawnMowerPerformanceTracker().start("test")
        )
        coordinator._async_refresh_active_runtime.assert_not_awaited()
        coordinator.async_refresh_app_maps.assert_awaited_once_with(force=False)
    asyncio.run(scenario())


def test_same_index_recovery_supersedes_unscoped_telemetry():
    async def scenario():
        coordinator = _coordinator()
        await coordinator._async_refresh_runtime_map_index(force=True)
        coordinator.client.async_get_current_app_map_index.side_effect = TimeoutError()
        started, release = asyncio.Event(), asyncio.Event()
        async def telemetry(**kwargs):
            started.set()
            await release.wait()
        coordinator.client.async_get_runtime_status_blob.side_effect = telemetry
        snapshot = SimpleNamespace(
            available=True, activity="mowing", mowing_session_active=True
        )
        task = asyncio.create_task(coordinator._async_refresh_active_runtime(
            DreameLawnMowerPerformanceTracker().start("test"), snapshot
        ))
        await started.wait()
        coordinator.client.async_get_current_app_map_index.side_effect = None
        await coordinator._async_refresh_runtime_map_index(force=True)
        release.set()
        assert await task is False
        coordinator.client.update_runtime_live_tracking.assert_not_called()
    asyncio.run(scenario())


@pytest.mark.parametrize("new_identity", [None, 3])
def test_old_geometry_cannot_replace_newer_map_identity(new_identity):
    async def scenario():
        coordinator = _coordinator()
        coordinator._invalidate_schedule_map_hint = Mock()
        original = coordinator.app_maps
        started, release = asyncio.Event(), asyncio.Event()
        async def geometry(**kwargs):
            started.set()
            await release.wait()
            return {"map_list_valid": True, "current_map_index": 2}
        coordinator.client.async_get_app_maps = geometry
        task = asyncio.create_task(coordinator.async_refresh_app_maps(force=True))
        await started.wait()
        coordinator.client.async_get_current_app_map_index.return_value = new_identity
        await coordinator._async_refresh_runtime_map_index(force=True)
        coordinator.selected_map_index = new_identity
        release.set()
        assert await task is original
        assert coordinator.selected_map_index == new_identity
        assert coordinator.app_maps_refreshed_at is None
    asyncio.run(scenario())


@pytest.mark.parametrize("fails", [False, True])
def test_superseded_geometry_cannot_expire_a_newer_geometry_result(fails):
    async def scenario():
        coordinator = _coordinator()
        started, release = asyncio.Event(), asyncio.Event()
        async def geometry(**kwargs):
            started.set()
            await release.wait()
            if fails:
                raise TimeoutError("Older download failed")
            return {"map_list_valid": True, "current_map_index": 2}
        coordinator.client.async_get_app_maps = geometry
        task = asyncio.create_task(coordinator.async_refresh_app_maps(force=True))
        await started.wait()
        newer = {"map_list_valid": True, "current_map_index": 3}
        coordinator.app_maps = newer
        coordinator.app_maps_refreshed_at = datetime.now(UTC)
        coordinator.app_maps_refresh_succeeded = True
        release.set()
        assert await task is newer
        assert coordinator.app_maps_refresh_succeeded is True
        assert coordinator.app_maps_refreshed_at is not None
    asyncio.run(scenario())


def test_matching_geometry_can_finish_after_identity_refresh():
    async def scenario():
        coordinator = _coordinator()
        coordinator._invalidate_schedule_map_hint = Mock()
        async def geometry(**kwargs):
            await coordinator._async_refresh_runtime_map_index(force=True)
            return {"map_list_valid": True, "current_map_index": 2}
        coordinator.client.async_get_app_maps = geometry
        result = await coordinator.async_refresh_app_maps(force=True)
        assert result["current_map_index"] == 2
        assert coordinator.app_maps_refresh_succeeded is True
        assert coordinator.app_maps_refreshed_at is not None
    asyncio.run(scenario())


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
        assert (await coordinator._async_refresh_runtime_map_index(force=True))[:2] == (
            True, index
        )
        assert coordinator.app_maps_refreshed_at is None
        cached = await coordinator._async_refresh_runtime_map_index(force=False)
        assert cached[:2] == (True, index)
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
        assert await first == await second
        assert (await first)[:2] == (True, 2)
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


@pytest.mark.parametrize("new_outcome", [None, 3, TimeoutError("MAPL unavailable")])
def test_new_identity_outcome_supersedes_pending_telemetry(new_outcome):
    async def scenario():
        coordinator = _coordinator()
        started, release = asyncio.Event(), asyncio.Event()

        async def telemetry(**kwargs):
            started.set()
            await release.wait()
            return None

        coordinator.client.async_get_runtime_status_blob.side_effect = telemetry
        snapshot = SimpleNamespace(
            available=True, activity="mowing", mowing_session_active=True
        )
        first = asyncio.create_task(coordinator._async_refresh_active_runtime(
            DreameLawnMowerPerformanceTracker().start("test"), snapshot
        ))
        await started.wait()
        if isinstance(new_outcome, Exception):
            coordinator.client.async_get_current_app_map_index.side_effect = new_outcome
        else:
            coordinator.client.async_get_current_app_map_index.return_value = (
                new_outcome
            )
        await coordinator._async_refresh_runtime_map_index(force=True)
        release.set()
        assert await first is False
        coordinator.client.update_runtime_live_tracking.assert_not_called()
        assert coordinator._runtime_map_identity_verified is False

    asyncio.run(scenario())


def test_cancelled_identity_read_expires_its_previous_verification():
    async def scenario():
        coordinator = _coordinator()
        await coordinator._async_refresh_runtime_map_index(force=True)
        coordinator._runtime_map_identity_verified = True
        previous = coordinator._runtime_map_identity_generation
        started = asyncio.Event()

        async def read():
            started.set()
            await asyncio.Future()

        coordinator.client.async_get_current_app_map_index.side_effect = read
        task = asyncio.create_task(
            coordinator._async_refresh_runtime_map_index(force=True)
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert coordinator._runtime_map_identity_generation > previous
        assert coordinator._runtime_map_identity_verified is False
        assert coordinator._runtime_map_index_refreshed_at is None

    asyncio.run(scenario())


@pytest.mark.parametrize("command_fails", [False, True])
def test_identity_read_waits_for_map_switch_completion(command_fails):
    async def scenario():
        coordinator = _coordinator()
        coordinator.selected_map_index = 3
        coordinator.async_request_refresh = AsyncMock()
        coordinator.async_refresh_app_maps = AsyncMock()
        coordinator.async_refresh_vector_map_details = AsyncMock()
        coordinator.async_update_listeners = Mock()
        started, release = asyncio.Event(), asyncio.Event()

        async def switch(index):
            started.set()
            await release.wait()
            coordinator.client.async_get_current_app_map_index.return_value = index
            if command_fails:
                raise TimeoutError("Map switch response lost")

        coordinator.client.async_switch_current_map = switch
        command = asyncio.create_task(coordinator.async_switch_current_map(3))
        await started.wait()
        identity = asyncio.create_task(
            coordinator._async_refresh_runtime_map_index(force=True)
        )
        await asyncio.sleep(0)
        coordinator.client.async_get_current_app_map_index.assert_not_awaited()
        release.set()
        if command_fails:
            with pytest.raises(TimeoutError):
                await command
        else:
            await command
        assert (await identity)[:2] == (True, 3)

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["telemetry", "bluetooth"])
@pytest.mark.parametrize("fails", [False, True])
def test_realtime_read_cannot_publish_after_identity_invalidation(boundary, fails):
    async def scenario():
        coordinator = _coordinator()
        coordinator._runtime_map_identity_verified = True
        coordinator._runtime_active_map_index = 2
        coordinator._shutting_down = False
        coordinator._last_device_settings_event_at = None
        coordinator.async_set_updated_data = Mock()
        coordinator._schedule_metadata_refresh = Mock()
        snapshot = SimpleNamespace(
            available=True, activity="mowing", mowing_session_active=True
        )
        coordinator.client.async_get_cached_snapshot = AsyncMock(return_value=snapshot)
        coordinator.client.async_get_bluetooth_connected = AsyncMock(return_value=False)
        started, release = asyncio.Event(), asyncio.Event()

        async def read(**kwargs):
            started.set()
            await release.wait()
            if fails:
                raise TimeoutError("Delayed optional read failed")
            return None

        method = (
            coordinator.client.async_get_runtime_status_blob
            if boundary == "telemetry"
            else coordinator.client.async_get_bluetooth_connected
        )
        method.side_effect = read
        task = asyncio.create_task(coordinator._async_process_client_update())
        await started.wait()
        coordinator._invalidate_runtime_map_identity()
        release.set()
        await task
        coordinator.client.update_runtime_live_tracking.assert_not_called()
        coordinator.async_set_updated_data.assert_not_called()

    asyncio.run(scenario())
