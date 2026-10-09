"""Native app video toggles retain enable guards and unconditional stop access."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web

from .test_async_app_commands import client_for
from .test_async_cloud_session import (
    DreameLawnMowerConnectionError,
    cloud_strings,
    login_response,
    server,
)


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("mode", ["success", "blocked", "rejected", "missing",
                                  "disconnect"])
def test_native_stream_toggle(monkeypatch, account, enabled, mode):
    strings = cloud_strings(account)
    calls = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        action = (await request.json())["data"]["params"]["in"][0]
        calls.append(action)
        assert action == {"m": "a", "p": 0, "o": 400, "d": {"on": enabled}}
        if mode == "disconnect":
            request.transport.close()
            return web.Response()
        result = None if mode == "missing" else {
            "r": 1 if mode == "rejected" else 0, "d": {"accepted": True}}
        return web.json_response({"code": 0, "data": {"result": {"out": [result]}}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            client._async_update_device = AsyncMock(return_value=client._device)
            client._snapshot_from_device = lambda device: SimpleNamespace(
                state="docked" if mode == "blocked" else "paused",
                raw_attributes={},
            )
            try:
                if mode in {"rejected", "missing", "disconnect"} or (
                    enabled and mode == "blocked"
                ):
                    with pytest.raises(DreameLawnMowerConnectionError):
                        await client.async_set_camera_stream_enabled(enabled)
                else:
                    assert await client.async_set_camera_stream_enabled(enabled) == {
                        "r": 0, "d": {"accepted": True}}
                assert client._async_update_device.await_count == int(enabled)
                assert len(calls) == (0 if enabled and mode == "blocked" else 1)
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["refresh", "command"])
@pytest.mark.parametrize("close_client", [False, True])
def test_stream_toggle_cancellation(monkeypatch, phase, close_client):
    strings = cloud_strings("dreame")

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            calls.append(await request.json())
            entered.set()
            await release.wait()
            return web.json_response({"code": 0, "data": {
                "result": {"out": [{"r": 0}]}}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)

            async def refresh():
                if phase == "refresh":
                    entered.set()
                    await release.wait()
                return client._device

            client._async_update_device = refresh
            client._snapshot_from_device = lambda device: SimpleNamespace(
                state="paused", raw_attributes={})
            task = asyncio.create_task(client.async_set_camera_stream_enabled(True))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                if close_client:
                    await asyncio.wait_for(client.async_close(), 3)
                else:
                    task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert len(calls) == (1 if phase == "command" else 0)
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())
