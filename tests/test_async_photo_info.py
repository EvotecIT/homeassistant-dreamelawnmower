"""Photo metadata actions retain guards, envelopes and no-replay lifetime."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession, web

from .test_async_camera_features import camera_client
from .test_async_cloud_session import (
    DreameLawnMowerConnectionError,
    cloud_strings,
    login_response,
    server,
)


def photo_client(session, account="dreame", supported=True):
    client, device = camera_client(session, account)
    original = device.disconnect.__self__
    device._protocol = original._protocol
    device._consumable_change = False
    device.schedule_update = lambda *args: None
    if not supported:
        device.action_mapping = {}
        device.property_mapping = {}
    return client


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("reply", ["success", "rejected", "missing", "disconnect",
                                   "unsupported"])
def test_native_photo_request(monkeypatch, account, reply):
    strings = cloud_strings(account)
    requests = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        body = (await request.json())["data"]
        requests.append(body)
        assert body["method"] == "action"
        assert body["params"] == {
            "did": "42", "siid": 4, "aiid": 6, "in": [{"id": 17}]
        }
        if reply == "disconnect":
            request.transport.close()
            return web.Response()
        result = None if reply == "missing" else {
            "code": 7 if reply == "rejected" else 0,
            "out": [{"url": "https://example.invalid/photo"}],
        }
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = photo_client(session, account, reply != "unsupported")
            try:
                if reply == "success":
                    result = await client.async_request_photo_info([{"id": 17}])
                    assert result == {
                        "code": 0, "out": [{"url": "https://example.invalid/photo"}]
                    }
                else:
                    with pytest.raises(DreameLawnMowerConnectionError):
                        await client.async_request_photo_info([{"id": 17}])
                assert len(requests) == (0 if reply == "unsupported" else 1)
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("close_client", [False, True])
def test_photo_request_cancellation(monkeypatch, close_client):
    strings = cloud_strings("dreame")

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        writes = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            writes.append(await request.json())
            entered.set()
            await release.wait()
            return web.json_response({"code": 0, "data": {"result": {"code": 0}}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = photo_client(session)
            task = asyncio.create_task(client.async_request_photo_info())
            try:
                await asyncio.wait_for(entered.wait(), 3)
                if close_client:
                    await asyncio.wait_for(client.async_close(), 3)
                else:
                    task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert len(writes) == 1
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())
