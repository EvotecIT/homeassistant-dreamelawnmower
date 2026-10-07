"""Regression contracts for interleaved MQTT and map state application."""

import asyncio
import time
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_mqtt_messages,
    map_manager,
    map_types,
)

from .test_unknown_properties import _device_stub


@pytest.mark.parametrize("callback", ["message", "connected"])
def test_mqtt_capture_does_not_wait_for_a_worker_owned_device_lock(
    monkeypatch, callback
):
    async def scenario():
        device, _ = _device_stub()
        device._protocol = SimpleNamespace(
            cloud=SimpleNamespace(_shutdown_requested=False)
        )
        device._mqtt_generation = 0
        client = SimpleNamespace(
            _closing=False, _device=device, _cloud_read_tasks=set()
        )
        owner = client_mqtt_messages.NativeMqttMessages(client, device)
        device._native_connected_receiver = owner.request_connected
        device.schedule_update = Mock()

        async def read_cloud(read):
            return await read(None)

        client._async_cloud_read = read_cloud
        client._ensure_device = lambda **kwargs: device

        async def apply(*args, **kwargs):
            return None

        monkeypatch.setattr(client_mqtt_messages, "async_run_device_plan", apply)
        entered, release = Event(), Event()

        def worker():
            with device._state_lock:
                entered.set()
                release.wait(2)

        thread = Thread(target=worker)
        thread.start()
        assert await asyncio.to_thread(entered.wait, 1)
        # A separate thread releases even if the callback blocks the loop.
        safety_release = Thread(target=lambda: (time.sleep(0.3), release.set()))
        safety_release.start()
        started = time.monotonic()
        try:
            if callback == "message":
                owner.request({"method": "properties_changed", "params": []})
            else:
                device._connected_callback()
            await asyncio.sleep(0)
            assert time.monotonic() - started < 0.15
        finally:
            release.set()
            await asyncio.to_thread(thread.join)
            await asyncio.to_thread(safety_release.join)
            await asyncio.sleep(0)
            if owner._task is not None:
                await owner._task

    asyncio.run(scenario())


def test_native_reconnect_orders_new_messages_after_owned_state_reset(monkeypatch):
    async def scenario():
        device, _ = _device_stub()
        device._protocol = SimpleNamespace(
            cloud=SimpleNamespace(_shutdown_requested=False)
        )
        device._mqtt_generation = 0
        device.schedule_update = Mock()
        device._pending_property_callbacks = [(Mock(), "old")]

        async def read_cloud(read):
            return await read(None)

        client = SimpleNamespace(
            _closing=False,
            _device=device,
            _cloud_read_tasks=set(),
            _async_cloud_read=read_cloud,
            _ensure_device=lambda **kwargs: device,
        )
        owner = client_mqtt_messages.NativeMqttMessages(client, device)
        device._native_connected_receiver = owner.request_connected
        observed = []

        async def apply(client, plan, **kwargs):
            kwargs["require_device"](device)
            observed.append(device._mqtt_generation)

        monkeypatch.setattr(client_mqtt_messages, "async_run_device_plan", apply)
        owner.request({"method": "properties_changed", "params": []})
        device._connected_callback()
        device._connected_callback()
        owner.request({"method": "properties_changed", "params": []})
        await owner._task
        assert observed == [2]
        assert device._mqtt_generation == 2
        assert device._pending_property_callbacks == []

    asyncio.run(scenario())


def test_reconnect_discards_callbacks_retained_before_the_new_epoch():
    device, _ = _device_stub()
    device.schedule_update = Mock()
    observed = []

    def interrupted(previous):
        raise asyncio.CancelledError

    plan = device._deliver_property_callbacks_plan(
        [(interrupted, "started"), (observed.append, "unstarted")]
    )
    with pytest.raises(asyncio.CancelledError):
        next(plan)
    assert len(device._pending_property_callbacks) == 1
    device._connected_callback()
    device._handle_properties([])
    assert observed == []


def test_suspended_saved_list_refresh_cannot_restore_an_older_iframe(monkeypatch):
    manager = map_manager.DreameMapMowerMapManager(SimpleNamespace())
    manager._latest_map_id = 7
    manager._dispatch_map_update = Mock()
    older, newer, saved = (map_types.MapData() for _ in range(3))
    for frame, number, timestamp in ((older, 1, 100), (newer, 2, 200)):
        frame.map_id, frame.frame_id, frame.timestamp_ms = 7, number, timestamp
        frame.empty_map = False
    saved.map_id = 8
    saved.saved_map = True
    partials = [map_types.MapDataPartial() for _ in range(2)]
    for partial, frame in zip(partials, (older, newer), strict=True):
        partial.map_id, partial.frame_id = frame.map_id, frame.frame_id
        partial.timestamp_ms = frame.timestamp_ms
        partial.frame_type = map_types.MapFrameType.I.value
    monkeypatch.setattr(
        map_manager.DreameMowerMapDecoder,
        "decode_map_data_from_partial",
        lambda partial, _vslam: (
            (older, saved) if partial.frame_id == 1 else (newer, None)
        ),
    )
    suspended = manager._add_map_data_plan(partials[0])
    try:
        assert next(suspended).kind == "list"
        manager._add_map_data(partials[1])
        assert manager._current_frame_id == 2
        list(suspended)
        assert manager._current_frame_id == 2
        assert manager._map_data is newer
    finally:
        suspended.close()
