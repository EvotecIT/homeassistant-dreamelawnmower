"""Realtime callbacks must not keep map ownership alive beyond its lease."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.dreame_lawn_mower.coordinator import DreameLawnMowerCoordinator
from custom_components.dreame_lawn_mower.performance import (
    DreameLawnMowerPerformanceTracker,
)
from tests.test_runtime_map_identity import _coordinator


@pytest.mark.parametrize("age", [None, 0, 61])
@pytest.mark.parametrize("expire_during_read", [False, True])
def test_realtime_identity_expires_without_a_poll(age, expire_during_read) -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        snapshot = SimpleNamespace(
            available=True, mowing_session_active=True, activity="mowing"
        )
        blob = SimpleNamespace()
        coordinator._runtime_map_identity_verified = True
        coordinator._runtime_active_map_index = 2
        coordinator._runtime_map_index_refreshed_at = (
            None if age is None else datetime.now(UTC) - timedelta(seconds=age)
        )
        coordinator.app_maps = {"current_map_index": 2}
        coordinator.selected_map_index = 2
        coordinator.runtime_status_blob = None
        coordinator.runtime_telemetry_cache = SimpleNamespace(update=Mock())
        coordinator.bluetooth_connected = None
        coordinator._schedule_metadata_refresh = Mock()
        coordinator.async_set_updated_data = Mock()

        async def read_blob(**kwargs):
            if expire_during_read:
                coordinator._runtime_map_index_refreshed_at = (
                    datetime.now(UTC) - timedelta(seconds=61)
                )
            return blob

        coordinator.client = SimpleNamespace(
            async_get_cached_snapshot=AsyncMock(return_value=snapshot),
            async_get_runtime_status_blob=AsyncMock(side_effect=read_blob),
            async_get_bluetooth_connected=AsyncMock(return_value=True),
            update_runtime_live_tracking=Mock(),
        )
        expired = age != 0 or expire_during_read
        # Repeated callbacks postpone polling; each must remain unscoped until
        # the existing background owner verifies MAPL again.
        for _ in range(3):
            await coordinator._async_process_client_update()
            assert coordinator.client.update_runtime_live_tracking.call_args.kwargs[
                "map_index"
            ] == (None if expired else 2)
        assert coordinator._runtime_map_identity_verified is not expired
        if expired:
            coordinator._schedule_metadata_refresh.assert_called_with(
                refresh_map_and_runtime=True
            )
        else:
            coordinator._schedule_metadata_refresh.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize("fails", [False, True])
def test_poll_read_cannot_publish_after_identity_expires(fails) -> None:
    async def scenario() -> None:
        coordinator = _coordinator()
        snapshot = SimpleNamespace(
            available=True, mowing_session_active=True, activity="mowing"
        )

        async def read_blob(**kwargs):
            coordinator._runtime_map_index_refreshed_at -= timedelta(seconds=61)
            if fails:
                raise TimeoutError("Delayed telemetry")
            return SimpleNamespace()

        coordinator.client.async_get_runtime_status_blob.side_effect = read_blob
        result = await coordinator._async_refresh_active_runtime(
            DreameLawnMowerPerformanceTracker().start("test"), snapshot
        )
        assert result is False
        assert coordinator._runtime_map_identity_verified is False
        assert coordinator.client.update_runtime_live_tracking.call_args.kwargs[
            "map_index"
        ] is None

    asyncio.run(scenario())


def test_docked_mqtt_updates_resume_pending_switch_recovery() -> None:
    async def scenario():
        coordinator = _coordinator()
        snapshot = SimpleNamespace(
            available=True, mowing_session_active=False, activity="docked",
            map_switch_event_at=100,
        )
        coordinator._last_map_switch_event_at = 100
        coordinator._map_switch_inventory_pending_event = 100
        coordinator._runtime_map_identity_verified = False
        coordinator._schedule_metadata_refresh = Mock()
        coordinator.async_set_updated_data = Mock()
        coordinator.bluetooth_connected = None
        coordinator.client.async_get_cached_snapshot = AsyncMock(return_value=snapshot)
        coordinator.client.async_get_bluetooth_connected = AsyncMock(return_value=True)
        for _ in range(2):
            await coordinator._async_process_client_update()
        assert coordinator._schedule_metadata_refresh.call_count == 2
        coordinator._schedule_metadata_refresh.assert_called_with(
            refresh_map_and_runtime=True
        )

    asyncio.run(scenario())


def test_shutdown_drains_preview_after_stopping_snapshot_producers() -> None:
    async def scenario():
        coordinator = _coordinator()
        started, release = asyncio.Event(), asyncio.Event()
        snapshot = SimpleNamespace(available=True, map_switch_event_at=100)

        async def cached():
            started.set()
            await release.wait()
            return snapshot

        async def stop_connectivity():
            release.set()
            await asyncio.sleep(0)

        coordinator.client.async_get_cached_snapshot = cached
        coordinator.client.set_update_callback = Mock()
        coordinator._record_device_snapshot = Mock()
        coordinator._async_shutdown_connectivity_recovery = stop_connectivity
        flushed = []

        async def flush():
            flushed.append(update_task.done())

        coordinator.map_restart_preview = SimpleNamespace(async_flush_invalidated=flush)
        coordinator._client_update_task = asyncio.create_task(
            coordinator._async_process_client_update()
        )
        update_task = coordinator._client_update_task
        await started.wait()
        await coordinator._async_stop_owned_tasks()
        coordinator._record_device_snapshot.assert_not_called()
        assert flushed == [True]

    asyncio.run(scenario())


def test_background_refresh_recovers_expired_identity() -> None:
    async def scenario() -> None:
        coordinator = _coordinator()
        coordinator.data = SimpleNamespace(
            available=True, mowing_session_active=True, activity="mowing"
        )
        await coordinator._async_refresh_runtime_map_index(force=True)
        coordinator._runtime_map_identity_verified = True
        coordinator._runtime_map_index_refreshed_at -= timedelta(seconds=61)
        coordinator.client.async_get_current_app_map_index.return_value = 3
        coordinator.async_refresh_app_maps = AsyncMock()

        await coordinator._async_refresh_background_map_runtime(
            DreameLawnMowerPerformanceTracker().start("test")
        )
        assert coordinator._runtime_map_identity_verified is True
        assert coordinator._runtime_map_index() == 3
        assert coordinator.client.update_runtime_live_tracking.call_args.kwargs[
            "map_index"
        ] == 3
        coordinator.async_refresh_app_maps.assert_awaited_once_with(force=False)

    asyncio.run(scenario())
