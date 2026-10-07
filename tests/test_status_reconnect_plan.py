"""OTA status reconnects retain ordering and native cancellation ownership."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from aiohttp import ClientSession

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_device_actions,
    client_startup,
    device_action_plan,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_state import (
    _DreameMowerDeviceStateMixin,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerProperty,
    DreameMowerStatus,
    DreameMowerTaskStatus,
)

from .test_async_app_commands import client_for


def configure_status(device, events):
    device._remote_control = False
    device._ready = True
    device.get_property = lambda prop: DreameMowerStatus.STANDBY.value
    device.status = SimpleNamespace(started=False, cleanup_started=False)
    device.capability = SimpleNamespace(cruising=False)
    device._map_manager = Mock()
    device._map_manager.editor.refresh_map.side_effect = lambda: events.append("map")


def test_sync_ota_reconnect_precedes_map_refresh():
    device = object.__new__(_DreameMowerDeviceStateMixin)
    events = []
    configure_status(device, events)

    def connect():
        assert device._ready is False
        events.append("connect")

    device.connect_device = connect
    device._status_changed(DreameMowerStatus.OTA.value)
    assert events == ["connect", "map"]


@pytest.mark.parametrize("interrupted", [False, True])
def test_nested_task_plan_finishes_before_external_listener(interrupted):
    device = object.__new__(_DreameMowerDeviceStateMixin)
    events = []
    configure_status(device, events)
    device.status.cleanup_started = True
    device.status.cleanup_completed = False
    device.status.status = DreameMowerStatus.BACK_HOME
    device.status.task_status = DreameMowerTaskStatus.COMPLETED
    rows = [{"did": "2", "siid": 3, "piid": 1}]

    def task_plan(previous):
        assert previous == DreameMowerTaskStatus.COMPLETED.value
        result = yield device_action_plan.PropertyReadRequest(rows)
        assert result == []
        events.append("task applied")

    def read(properties):
        assert properties == rows
        events.append("read")
        return []

    device._task_status_changed_plan = task_plan
    device._protocol = SimpleNamespace(get_properties=read)
    device._property_update_callback = {
        DreameMowerProperty.TASK_STATUS.value: [
            device._task_status_changed,
            lambda previous: events.append("external"),
        ],
    }
    device._property_changed = lambda: events.append("notify")
    if interrupted:
        plan = device._status_changed_plan(DreameMowerStatus.STANDBY.value)
        assert isinstance(next(plan), device_action_plan.PropertyReadRequest)
        plan.close()
        pending = device._pending_property_callbacks
        assert len(pending) == 1
        assert pending[0][1] == DreameMowerTaskStatus.COMPLETED.value
        assert list(device._deliver_property_callbacks_plan(pending)) == []
        assert events == ["external"]
    else:
        device._status_changed(DreameMowerStatus.STANDBY.value)
        assert events == ["read", "task applied", "external", "notify", "map"]


@pytest.mark.parametrize("cancel", [False, True])
def test_native_ota_reconnect_is_owned_until_completion(monkeypatch, cancel):
    async def scenario():
        events = []
        entered, release = asyncio.Event(), asyncio.Event()
        async with ClientSession() as session:
            client = client_for(session)
            device = client._device
            configure_status(device, events)
            monkeypatch.setattr(
                device,
                "connect_device",
                Mock(side_effect=AssertionError("Blocking reconnect")),
            )

            async def start(
                current_client, current_device, cloud, *, deadline, cancelled
            ):
                assert current_client is client and current_device is device
                assert device._ready is False
                assert time.monotonic() < deadline <= time.monotonic() + 20
                assert not cancelled.is_set()
                assert device._state_lock.acquire(blocking=False)
                device._state_lock.release()
                events.append("connect")
                entered.set()
                await release.wait()
                events.append("connected")

            monkeypatch.setattr(client_startup, "async_start_device", start)
            task = asyncio.create_task(
                client_device_actions.async_run_device_plan(
                    client,
                    lambda current: current._status_changed_plan(
                        DreameMowerStatus.OTA.value
                    ),
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), 5)
                if cancel:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    assert events == ["connect"]
                else:
                    release.set()
                    await task
                    assert events == ["connect", "connected", "map"]
                assert not session.closed
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())
