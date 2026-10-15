"""App-originated switches invalidate scope and coalesce map inventory reads."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.dreame_lawn_mower import coordinator_refresh
from custom_components.dreame_lawn_mower import map_switch_refresh as refresh
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_switch_events import (  # noqa: E501
    is_map_switch_announcement,
    map_switch_event_at,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
    snapshot_from_device,
)
from tests.test_person_detection_events import _device
from tests.test_runtime_map_identity import _coordinator


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("1.50", {}, True),
        ("1.50", {"other": 1}, False),
        ("2.50", {"t": "TASK", "d": {"o": 200, "exe": True}}, True),
        ("2.50", '{"t":"TASK","d":{"o":200}}', True),
        ("2.50", {"t": "TASK", "d": {"o": 100}}, False),
        ("2.50", {"t": "other", "d": {"o": 200}}, False),
        ("2.50", "not json", False),
    ],
)
def test_switch_signals_do_not_confuse_mowing_tasks(key, value, expected):
    assert is_map_switch_announcement(key, value) is expected


def test_repeated_identical_switches_publish_fresh_receipts():
    device = _device()
    descriptor = DreameLawnMowerDescriptor(
        did="test",
        name="Mower",
        model="mova.mower.g2529b",
        display_model="Mower",
        account_type="mova",
        country="DE",
    )
    message = {
        "method": "properties_changed",
        "params": [{"siid": 1, "piid": 50, "value": {}}],
    }
    receipts = []
    with patch("time.time", side_effect=[100.0, 200.0]):
        for _ in range(2):
            device._message_callback(message)
            receipts.append(
                snapshot_from_device(descriptor, device).map_switch_event_at
            )
    assert receipts == [100, 200]
    assert device._property_changed.call_count == 2


def test_failed_switch_properties_do_not_expire_map_scope():
    assert (
        map_switch_event_at(
            {
                "1.50": {"value": {}, "last_seen": 100, "code": 5},
                "2.50": {
                    "value": {"t": "TASK", "d": {"o": 200}},
                    "last_seen": float("nan"),
                },
            }
        )
        is None
    )


def test_switch_expires_map_scoped_caches_once_and_preserves_manual_selection():
    coordinator = _coordinator()
    coordinator._invalidate_runtime_map_identity = Mock()
    coordinator._schedule_metadata_refresh = Mock()
    snapshot = SimpleNamespace(map_switch_event_at=100)
    coordinator._record_device_snapshot(snapshot)
    coordinator._record_device_snapshot(snapshot)
    assert coordinator.app_maps_refreshed_at is None
    assert coordinator.app_maps_refresh_succeeded is False
    assert coordinator.schedules_refreshed_at is None
    assert coordinator.batch_device_data_refreshed_at is None
    assert coordinator.selected_map_index == 2
    assert coordinator._schedule_cache_generation == 1
    coordinator._invalidate_runtime_map_identity.assert_called_once()
    coordinator._schedule_metadata_refresh.assert_called_once_with(
        refresh_map_and_runtime=True
    )


def test_switch_pair_shares_a_settled_authoritative_map_read():
    async def scenario():
        coordinator = _coordinator()
        coordinator.client.async_get_current_app_map_index.return_value = 3
        coordinator._schedule_metadata_refresh = Mock()
        with patch.object(refresh, "monotonic", return_value=10):
            refresh.observe_map_switch(
                coordinator, SimpleNamespace(map_switch_event_at=100)
            )
            refresh.observe_map_switch(
                coordinator, SimpleNamespace(map_switch_event_at=101)
            )
        started, release = asyncio.Event(), asyncio.Event()

        async def sleep(delay):
            assert delay == 2
            started.set()
            await release.wait()

        with (
            patch.object(refresh, "monotonic", side_effect=[10, 12]),
            patch.object(refresh.asyncio, "sleep", side_effect=sleep),
        ):
            read = asyncio.create_task(
                coordinator._async_refresh_runtime_map_index(force=True)
            )
            await started.wait()
            coordinator.client.async_get_current_app_map_index.assert_not_awaited()
            release.set()
            assert (await read)[:2] == (True, 3)
        coordinator.client.async_get_current_app_map_index.assert_awaited_once()

    asyncio.run(scenario())


def test_switch_rejects_an_inflight_identity_read_from_the_previous_map():
    async def scenario():
        coordinator = _coordinator()
        coordinator._schedule_metadata_refresh = Mock()
        started, release = asyncio.Event(), asyncio.Event()

        async def read():
            started.set()
            await release.wait()
            return 2

        coordinator.client.async_get_current_app_map_index.side_effect = read
        pending = asyncio.create_task(
            coordinator._async_refresh_runtime_map_index(force=True)
        )
        await started.wait()
        coordinator._record_device_snapshot(SimpleNamespace(map_switch_event_at=100))
        release.set()
        refreshed, index, _ = await pending
        assert refreshed is False
        assert index is None
        assert coordinator._runtime_map_identity_verified is False
        coordinator.client.update_runtime_map_identity.assert_not_called()

    asyncio.run(scenario())


def test_switch_wait_cancellation_does_not_dispatch_a_map_read():
    async def scenario():
        coordinator = _coordinator()
        coordinator._map_switch_settle_until = float("inf")
        with patch.object(
            refresh.asyncio, "sleep", new=AsyncMock(side_effect=asyncio.CancelledError)
        ):
            with pytest.raises(asyncio.CancelledError):
                await coordinator._async_refresh_runtime_map_index(force=True)
        coordinator.client.async_get_current_app_map_index.assert_not_awaited()

    asyncio.run(scenario())


def test_identity_reader_queued_before_switch_waits_after_acquiring_ownership():
    async def scenario():
        coordinator = _coordinator()
        coordinator._schedule_metadata_refresh = Mock()
        reading, finish_read = asyncio.Event(), asyncio.Event()
        settling, settled = asyncio.Event(), asyncio.Event()

        async def wait(owner):
            if getattr(owner, "_last_map_switch_event_at", None) is not None:
                settling.set()
                await settled.wait()

        async def read():
            reading.set()
            await finish_read.wait()
            return 3 if getattr(coordinator, "_last_map_switch_event_at", None) else 2

        coordinator.client.async_get_current_app_map_index.side_effect = read
        with patch.object(coordinator_refresh, "async_wait_for_map_switch", new=wait):
            first = asyncio.create_task(
                coordinator._async_refresh_runtime_map_index(force=True)
            )
            await reading.wait()
            queued = asyncio.create_task(
                coordinator._async_refresh_runtime_map_index(force=True)
            )
            await asyncio.sleep(0)
            coordinator._record_device_snapshot(
                SimpleNamespace(map_switch_event_at=100)
            )
            finish_read.set()
            await first
            await asyncio.wait_for(settling.wait(), timeout=1)
            assert coordinator.client.async_get_current_app_map_index.await_count == 1
            settled.set()
            assert (await queued)[:2] == (True, 3)
            assert coordinator.client.async_get_current_app_map_index.await_count == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["vector", "batch", "preferences"])
def test_switch_fences_inflight_map_metadata_and_keeps_its_timestamp_expired(kind):
    async def scenario():
        coordinator = _coordinator()
        coordinator._schedule_metadata_refresh = Mock()
        coordinator.vector_map_details = {"retained": True}
        coordinator.vector_map_details_refreshed_at = None
        coordinator.batch_device_data = None
        coordinator.batch_device_data_refreshed_at = None
        coordinator._preference_write_lock = asyncio.Lock()

        async def read(**kwargs):
            coordinator._record_device_snapshot(
                SimpleNamespace(map_switch_event_at=100)
            )
            if kind == "vector":
                return {"stale": True}
            if kind == "batch":
                return None, None, None, 0, None, None
            return None, None, None

        if kind == "vector":
            coordinator.client.async_get_vector_map_details = read
            result = await coordinator.async_refresh_vector_map_details()
            assert result == {"retained": True}
            assert coordinator.vector_map_details_refreshed_at is None
        elif kind == "batch":
            coordinator._async_fetch_batch_device_data = read
            assert await coordinator.async_refresh_batch_device_data() is None
            assert coordinator.batch_device_data_refreshed_at is None
        else:
            coordinator._async_get_current_mowing_preferences = read
            assert (
                await coordinator.async_refresh_mowing_preferences(source="test")
                is None
            )
            assert coordinator.batch_device_data_refreshed_at is None

    asyncio.run(scenario())
