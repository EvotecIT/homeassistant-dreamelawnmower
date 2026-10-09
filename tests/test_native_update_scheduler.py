"""Scheduled device updates use native HTTP and retain availability/close policy."""

import asyncio

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDevice,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerProperty,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import cloud_strings, login_response, server


def configure_device(monkeypatch, client):
    monkeypatch.setattr(DreameMowerDevice, "cloud_connected", property(lambda _: True))
    monkeypatch.setattr(
        DreameMowerDevice, "device_connected", property(lambda _: False)
    )
    monkeypatch.setattr(DreameMowerDevice, "_update_interval", property(lambda _: 3600))
    device = client._ensure_device()
    device._ready = True
    battery = DreameMowerProperty.BATTERY_LEVEL
    device.data = {battery.value: 20}
    device._select_update_properties = lambda: [battery]
    return device, battery


async def activate(client):
    async def ready(_cloud):
        return None

    await client._async_cloud_read(ready)
    client._ensure_device()


@pytest.mark.parametrize("account", ["dreame", "mova"])
def test_schedule_coalesces_and_applies_native_result(monkeypatch, account):
    strings = cloud_strings(account)
    calls = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        body = await request.json()
        calls.append(body["data"])
        assert body["data"]["method"] == "get_properties"
        return web.json_response(
            {
                "code": 0,
                "data": {
                    "result": [
                        {
                            "did": str(DreameMowerProperty.BATTERY_LEVEL.value),
                            "code": 0,
                            "value": 55,
                        },
                    ]
                },
            }
        )

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            device, battery = configure_device(monkeypatch, client)
            finished = asyncio.Event()
            loop = asyncio.get_running_loop()
            complete = device._complete_scheduled_update

            def completed(error):
                complete(error)
                loop.call_soon_threadsafe(finished.set)

            device._complete_scheduled_update = completed
            monkeypatch.setattr(
                device, "update", lambda *a: pytest.fail("sync refresh")
            )
            await activate(client)
            try:
                await asyncio.to_thread(device.schedule_update, 10, True)
                await asyncio.to_thread(device.schedule_update, 0, False)
                await asyncio.wait_for(finished.wait(), 3)
                assert len(calls) == 1
                assert device.data[battery.value] == 55
                assert device.available and device._update_fail_count == 0
                assert device._update_timer is None
            finally:
                await client.async_close()
            assert not client._cloud_read_tasks and not session.closed

    asyncio.run(scenario())


def test_scheduled_failures_keep_existing_availability_policy(monkeypatch):
    strings = cloud_strings("dreame")

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response({"code": 5})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            device, _ = configure_device(monkeypatch, client)
            device.available = True
            failures = []
            device.listen_error(failures.append)
            finished = asyncio.Queue()
            loop = asyncio.get_running_loop()
            complete = device._complete_scheduled_update

            def completed(error):
                complete(error)
                loop.call_soon_threadsafe(
                    finished.put_nowait, device._update_fail_count
                )

            device._complete_scheduled_update = completed
            await activate(client)
            try:
                for expected in range(1, 5):
                    device.schedule_update(0)
                    assert await asyncio.wait_for(finished.get(), 3) == expected
                    assert device.available is (expected < 4)
                assert len(failures) == 1
                assert device._last_update_failed > 0
            finally:
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("in_flight", [False, True])
def test_close_stops_schedules_and_drains_native_request(monkeypatch, in_flight):
    strings = cloud_strings("dreame")

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            calls.append(request.path)
            entered.set()
            await release.wait()
            return web.json_response({"code": 0, "data": {"result": []}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            device, _ = configure_device(monkeypatch, client)
            await activate(client)
            try:
                device.schedule_update(0 if in_flight else 10)
                if in_flight:
                    await asyncio.wait_for(entered.wait(), 3)
                await asyncio.wait_for(client.async_close(), 3)
                await asyncio.to_thread(device.schedule_update, 0)
                await asyncio.sleep(0)
                assert len(calls) == int(in_flight)
                assert not client._cloud_read_tasks
                assert client._native_updates._timer is None
                assert device._update_timer is None
                assert not session.closed
            finally:
                release.set()
                await client.async_close()

    asyncio.run(scenario())


def test_reconnect_routes_forced_update_to_native_http(monkeypatch):
    strings = cloud_strings("dreame")

    async def scenario():
        received = asyncio.Event()
        requests = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            requests.append(body["data"])
            received.set()
            return web.json_response({"code": 0, "data": {"result": []}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            device, _ = configure_device(monkeypatch, client)
            await activate(client)
            device.realtime_properties["stale"] = {"value": 1}
            monkeypatch.setattr(
                device, "update", lambda *a: pytest.fail("sync reconnect refresh")
            )
            try:
                await asyncio.to_thread(device._connected_callback)
                assert "stale" not in device.realtime_properties
                await asyncio.wait_for(received.wait(), 4)
                assert requests[0]["method"] == "get_properties"
                assert {"did": "100001", "siid": 1, "piid": 1} in requests[0]["params"]
                assert device._update_timer is None
            finally:
                await client.async_close()

    asyncio.run(scenario())


def test_negative_delay_cancels_coalesced_schedule(monkeypatch):
    async def scenario():
        async with ClientSession() as session:
            client = client_for(session)
            device, _ = configure_device(monkeypatch, client)
            await activate(client)
            try:
                device.schedule_update(10, True)
                device.schedule_update(-1)
                await asyncio.sleep(0)
                assert client._native_updates._timer is None
                assert device._update_timer is None
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("close_during_recovery", [False, True])
def test_completion_timeout_preserves_polling_or_close(
    monkeypatch, caplog, close_during_recovery
):
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        client_update_scheduler as scheduler,
    )
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        exceptions,
    )

    async def scenario():
        async with ClientSession() as session:
            client = client_for(session)
            device, _ = configure_device(monkeypatch, client)
            await activate(client)
            failed, recovered = asyncio.Event(), asyncio.Event()
            reads = 0
            original_read = scheduler.async_read_device_state
            original_later = asyncio.get_running_loop().call_later

            async def refresh(*args, **kwargs):
                return device

            async def read(*args, **kwargs):
                nonlocal reads
                reads += 1
                if reads == 1:
                    failed.set()
                    raise exceptions.DreameLawnMowerConnectionError(
                        "Device state read timed out"
                    )
                result = await original_read(*args, **kwargs)
                recovered.set()
                return result

            def later(delay, callback, *args, **kwargs):
                if delay == 30 and not close_during_recovery:
                    delay = 0.01
                return original_later(delay, callback, *args, **kwargs)

            monkeypatch.setattr(scheduler, "async_update_device", refresh)
            monkeypatch.setattr(scheduler, "async_read_device_state", read)
            monkeypatch.setattr(asyncio.get_running_loop(), "call_later", later)
            try:
                device.schedule_update(0)
                await asyncio.wait_for(failed.wait(), 3)
                if close_during_recovery:
                    await client.async_close()
                    assert client._native_updates._timer is None
                    assert reads == 1
                else:
                    await asyncio.wait_for(recovered.wait(), 3)
                    assert reads == 2
                    assert client._native_updates._timer is not None
                    assert device.available
                assert "Scheduled device update failed" in caplog.text
            finally:
                await client.async_close()
            assert not client._cloud_read_tasks
            assert not session.closed

    asyncio.run(scenario())
