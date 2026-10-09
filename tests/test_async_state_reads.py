"""State-read cache precedence and cancellation ownership contracts."""

from __future__ import annotations

import asyncio
import time
from threading import Event

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    app_protocol,
    client_state_reads,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerConnectionError,
)

from .test_async_app_preferences import make_client
from .test_async_cloud_session import cloud_strings, login_response, server

MOWER_BLUETOOTH_PROPERTY_KEY = app_protocol.MOWER_BLUETOOTH_PROPERTY_KEY
async_read_device_state = client_state_reads.async_read_device_state


def test_state_read_uses_callers_deadline_without_releasing_a_foreign_lock():
    async def scenario():
        async with ClientSession() as session:
            client = make_client(session)
            device = client._device
            locked, release, read_called = Event(), Event(), Event()

            def hold_state():
                with device._state_lock:
                    locked.set()
                    release.wait(3)

            holder = asyncio.create_task(asyncio.to_thread(hold_state))
            assert await asyncio.to_thread(locked.wait, 1)
            started = time.monotonic()
            try:
                with pytest.raises(DreameLawnMowerConnectionError, match="timed out"):
                    await asyncio.wait_for(async_read_device_state(
                        client, lambda _: read_called.set(), refresh=False,
                        deadline=started + 0.15,
                    ), 0.6)
                assert time.monotonic() - started < 0.5
                assert not read_called.is_set()
                assert not holder.done()
                assert not session.closed
            finally:
                release.set()
                await holder
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("cached,include_cloud,expected", [
    (False, True, False), (True, True, True),
    (None, False, None), (None, True, True),
])
def test_bluetooth_cache_precedes_native_cloud(
    monkeypatch, cached, include_cloud, expected,
):
    strings = cloud_strings("dreame")
    calls = []

    async def handler(request):
        calls.append(request.path)
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        assert request.path == "/dreame-user-iot/iotstatus/props"
        assert await request.json() == {
            "did": "42", "keys": MOWER_BLUETOOTH_PROPERTY_KEY,
        }
        return web.json_response({"code": 0, "data": [
            {"key": MOWER_BLUETOOTH_PROPERTY_KEY, "value": True},
        ]})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            client._device.realtime_properties[MOWER_BLUETOOTH_PROPERTY_KEY] = {
                "value": cached,
            }
            try:
                assert await client.async_get_bluetooth_connected(
                    include_cloud=include_cloud,
                ) is expected
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())
    assert len(calls) == (2 if cached is None and include_cloud else 0)


@pytest.mark.parametrize("close_client", [False, True])
@pytest.mark.parametrize("public_cached", [False, True])
def test_started_state_reader_is_drained_before_cancel_or_close(
    close_client, public_cached,
):
    started = Event()
    release = Event()
    finished = Event()

    def read(device):
        started.set()
        assert release.wait(5), "Test did not release the state reader"
        finished.set()
        return device.name

    async def scenario():
        async with ClientSession() as session:
            client = make_client(session)
            device = client._device
            client._snapshot_from_device = read
            task = asyncio.create_task(
                client.async_get_cached_snapshot() if public_cached
                else async_read_device_state(client, read, refresh=False),
            )
            close_task = None
            try:
                assert await asyncio.to_thread(started.wait, 5)
                if close_client:
                    close_task = asyncio.create_task(client.async_close())
                else:
                    task.cancel()
                await asyncio.sleep(0.05)
                assert not task.done()
                assert client._device is device
                if close_task is not None:
                    assert not close_task.done()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
                assert finished.is_set()
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                if close_task is not None:
                    await close_task
                await client.async_close()
            assert client._device is None
            assert not session.closed

    asyncio.run(scenario())


def test_state_lock_wait_can_be_cancelled_without_releasing_foreign_lock():
    acquired = Event()
    release = Event()
    read_called = Event()

    async def scenario():
        async with ClientSession() as session:
            client = make_client(session)
            device = client._device

            def hold_lock():
                with device._state_lock:
                    acquired.set()
                    assert release.wait(5)

            holder = asyncio.create_task(asyncio.to_thread(hold_lock))
            assert await asyncio.to_thread(acquired.wait, 5)
            task = asyncio.create_task(async_read_device_state(
                client, lambda device: read_called.set(), refresh=False,
            ))
            try:
                await asyncio.sleep(0.05)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
                assert not read_called.is_set()
                assert not holder.done()
            finally:
                release.set()
                await holder
                await client.async_close()

    asyncio.run(scenario())



@pytest.mark.parametrize("method", [
    "async_get_bluetooth_connected", "async_get_remote_control_support",
    "async_get_status_blob", "async_get_runtime_status_blob",
])
def test_support_and_status_refresh_use_native_rpc(monkeypatch, method):
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        device as device_module,
    )
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        device_types,
    )

    strings = cloud_strings("dreame")
    battery = device_types.DreameMowerProperty.BATTERY_LEVEL
    requests = []
    monkeypatch.setattr(
        device_module.DreameMowerDevice, "cloud_connected", property(lambda _: True),
    )
    monkeypatch.setattr(
        device_module.DreameMowerDevice, "device_connected", property(lambda _: False),
    )

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        payload = await request.json()
        requests.append(payload)
        assert payload["data"]["method"] == "get_properties"
        return web.json_response({"code": 0, "data": {"result": [
            {"did": str(battery.value), "code": 0, "value": 55},
        ]}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            device = client._device
            device._ready = True
            device.data = {battery.value: 20}
            device._last_settings_request = 10**20
            device.realtime_properties[MOWER_BLUETOOTH_PROPERTY_KEY] = {"value": False}
            for key in (app_protocol.MOWER_RAW_STATUS_PROPERTY_KEY,
                        app_protocol.MOWER_RUNTIME_STATUS_PROPERTY_KEY):
                device.realtime_properties[key] = {
                    "value": [206, 0, 206], "last_seen": 1_700_000_000,
                }
            try:
                sync_method = getattr(client, method.replace("async_", "_sync_", 1))
                expected = sync_method(refresh=False)
                result = await getattr(client, method)(refresh=True)
                assert result == expected
                assert device.data[battery.value] == 55
                assert len(requests) == 1
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())
