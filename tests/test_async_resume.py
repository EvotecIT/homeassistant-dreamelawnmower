"""Native resume preserves session identity and uncertain-command confirmation."""
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
@pytest.mark.parametrize("reply", ["success", "disconnect", "missing", "rejected"])
def test_native_resume(monkeypatch, account, reply):
    strings = cloud_strings(account)
    actions = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        action = (await request.json())["data"]["params"]["in"][0]
        actions.append(action)
        assert action == {"m": "a", "p": 0, "o": 5}
        if reply == "disconnect":
            request.transport.close()
            return web.Response()
        result = {"out": []} if reply == "missing" else {
            "out": [{"r": 7 if reply == "rejected" else 0}]}
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            client.async_get_status_blob = AsyncMock(
                return_value=SimpleNamespace(task_resumable=True))
            client._async_refresh_authoritative_snapshot = AsyncMock(
                return_value=SimpleNamespace(
                    state="mowing", task_status="mowing", task_resumable=False,
                    started=True, mowing=True, mowing_session_active=True))
            try:
                if reply == "rejected":
                    with pytest.raises(DreameLawnMowerConnectionError):
                        await client.async_start_mowing()
                else:
                    assert await client.async_start_mowing() is False
                assert len(actions) == 1
                assert client._async_refresh_authoritative_snapshot.await_count == (
                    1 if reply in {"disconnect", "missing"} else 0)
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["command", "confirmation"])
@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_resume_cancellation_owns_confirmation_delay(monkeypatch, stage, stop):
    strings = cloud_strings("dreame")
    actions = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            actions.append(await request.json())
            entered.set()
            if stage == "command":
                await release.wait()
            return web.json_response({"code": 0, "data": {"result": {"out": []}}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            client.async_get_status_blob = AsyncMock(
                return_value=SimpleNamespace(task_resumable=True))
            client._async_refresh_authoritative_snapshot = AsyncMock()
            operation = asyncio.create_task(client.async_start_mowing())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if stage == "confirmation":
                    await asyncio.sleep(0.05)
                if stop == "close":
                    await asyncio.wait_for(client.async_close(), 2)
                else:
                    operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(operation, 0.5)
                assert len(actions) == 1
                client._async_refresh_authoritative_snapshot.assert_not_awaited()
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await asyncio.gather(operation, return_exceptions=True)
                await client.async_close()
    asyncio.run(scenario())
