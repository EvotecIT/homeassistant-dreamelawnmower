"""Map-switch recovery must not reuse pre-switch metadata or failure state."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.dreame_lawn_mower import map_switch_refresh as refresh
from custom_components.dreame_lawn_mower.schedule_cache import ScheduleActionReadBackoff
from tests.test_runtime_map_identity import _coordinator


def _switch(coordinator):
    coordinator._schedule_metadata_refresh = Mock()
    with patch.object(refresh, "MAP_SWITCH_SETTLE_SECONDS", 0):
        coordinator._record_device_snapshot(SimpleNamespace(map_switch_event_at=100))


def test_switch_expires_previously_fresh_3d_objects():
    async def scenario():
        coordinator = _coordinator()
        coordinator.app_map_objects = {"app_map_objects": {"object_count": 7}}
        coordinator.app_map_objects_refreshed_at = datetime.now(UTC)
        coordinator.client.async_get_app_map_objects = AsyncMock(
            return_value={"object_count": 2}
        )
        coordinator.client.async_get_current_app_map_index.return_value = 3
        _switch(coordinator)
        assert coordinator.app_map_objects_refreshed_at is None
        result = await coordinator.async_refresh_app_map_objects()
        assert result["app_map_objects"]["object_count"] == 2
        coordinator.client.async_get_app_map_objects.assert_awaited_once()

    asyncio.run(scenario())


def test_switch_rejects_inflight_3d_object_result():
    async def scenario():
        coordinator = _coordinator()
        retained = {"app_map_objects": {"object_count": 7}}
        coordinator.app_map_objects = retained
        coordinator.app_map_objects_refreshed_at = None

        async def read(**kwargs):
            _switch(coordinator)
            return {"object_count": 9}

        coordinator.client.async_get_app_map_objects = read
        assert await coordinator.async_refresh_app_map_objects() is retained
        assert coordinator.app_map_objects_refreshed_at is None

    asyncio.run(scenario())


@pytest.mark.parametrize("stale_runtime_index", [None, 1])
def test_docked_map_refresh_retries_unchanged_inventory_before_publishing(
    stale_runtime_index,
):
    async def scenario():
        coordinator = _coordinator()
        coordinator._runtime_active_map_index = stale_runtime_index
        coordinator.client.async_get_current_app_map_index.side_effect = [2, 3]
        coordinator.client.async_get_app_maps = AsyncMock(
            return_value={
                "current_map_index": 3,
                "map_list_valid": True,
                "maps": [{"idx": 2, "created": True}, {"idx": 3, "created": True}],
            }
        )
        _switch(coordinator)
        with patch.object(refresh, "MAP_SWITCH_RETRY_DELAYS", (0, 0, 0)):
            result = await coordinator.async_refresh_app_maps()
        assert coordinator.client.async_get_current_app_map_index.await_count == 2
        assert result["current_map_index"] == coordinator.selected_map_index == 3
        coordinator.client.update_runtime_map_identity.assert_called_once()
        assert coordinator.client.update_runtime_map_identity.call_args.args[0] == 3

    asyncio.run(scenario())


def test_unchanged_map_announcement_has_bounded_readback():
    async def scenario():
        coordinator = _coordinator()
        _switch(coordinator)
        with patch.object(refresh, "MAP_SWITCH_RETRY_DELAYS", (0, 0, 0)):
            result = await coordinator._async_refresh_runtime_map_index(force=True)
        assert result[:2] == (True, 2)
        assert coordinator.client.async_get_current_app_map_index.await_count == 4

    asyncio.run(scenario())


@pytest.mark.parametrize("cancelled", [False, True], ids=["timeout", "cancellation"])
def test_failed_switch_readback_does_not_renew_old_identity(cancelled):
    async def scenario():
        coordinator = _coordinator()
        failure = asyncio.CancelledError() if cancelled else TimeoutError()
        coordinator.client.async_get_current_app_map_index.side_effect = [2, failure]
        _switch(coordinator)
        with patch.object(refresh, "MAP_SWITCH_RETRY_DELAYS", (0, 0, 0)):
            if cancelled:
                with pytest.raises(asyncio.CancelledError):
                    await coordinator._async_refresh_runtime_map_index(force=True)
            else:
                result = await coordinator._async_refresh_runtime_map_index(force=True)
                assert result[:2] == (False, None)
        coordinator.client.update_runtime_map_identity.assert_not_called()
        assert coordinator._runtime_map_index_refreshed_at is None
        assert coordinator._map_switch_pending_event == 100
        coordinator.client.async_get_current_app_map_index.side_effect = None
        coordinator.client.async_get_current_app_map_index.return_value = 3
        assert (await coordinator._async_refresh_runtime_map_index(force=True))[:2] == (
            True,
            3,
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("fails", [False, True], ids=["stale-success", "stale-failure"])
def test_switch_discards_inflight_schedule_outcome_and_cooldown(fails):
    async def scenario():
        coordinator = _coordinator()
        retained = {
            "source": "app_action_schedule",
            "available": True,
            "schedules": [{"idx": 2, "available": True, "version": 11, "plans": []}],
        }
        coordinator.schedules = retained
        coordinator.schedules_refreshed_at = None
        backoff = ScheduleActionReadBackoff(
            consecutive_failures=1,
            retry_not_before=datetime.now(UTC) + timedelta(minutes=5),
        )
        coordinator._schedule_action_read_backoff = backoff
        started, release = asyncio.Event(), asyncio.Event()

        async def read(**kwargs):
            started.set()
            await release.wait()
            if fails:
                raise TimeoutError("old map read failed")
            return {
                "source": "app_action_schedule",
                "available": True,
                "schedules": [{"idx": 2, "available": True, "version": 9, "plans": []}],
            }

        coordinator.client.async_get_app_schedules = read
        coordinator._async_get_shared_batch_schedules = AsyncMock(return_value={})
        pending = asyncio.create_task(coordinator.async_refresh_schedules(force=True))
        await started.wait()
        _switch(coordinator)
        release.set()
        assert await pending is retained
        assert coordinator.schedules_refreshed_at is None
        assert coordinator._schedule_action_read_backoff.retry_not_before is None
        coordinator._async_get_shared_batch_schedules.assert_not_awaited()

    asyncio.run(scenario())


def test_new_switch_does_not_reuse_shared_batch_request_with_same_map_hint():
    async def scenario():
        coordinator = _coordinator()
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def read(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
                return {"version": 9}
            return {"version": 11}

        coordinator.client.async_get_batch_schedules = read
        first = asyncio.create_task(
            coordinator._async_get_shared_batch_schedules(
                map_index_hint=2,
                force=False,
            )
        )
        await started.wait()
        _switch(coordinator)
        second = asyncio.create_task(
            coordinator._async_get_shared_batch_schedules(
                map_index_hint=2,
                force=False,
            )
        )
        await asyncio.sleep(0)
        release.set()
        old, new = await asyncio.gather(first, second)
        assert old == {"version": 9}
        assert new == {"version": 11}
        assert calls == 2
        await coordinator._async_wait_batch_schedule_reads()
        assert not coordinator._batch_schedule_read_tasks

    asyncio.run(scenario())
