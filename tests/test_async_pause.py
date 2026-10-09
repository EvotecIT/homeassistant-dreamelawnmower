"""Native pause shares device action policy and owns delays and confirmation."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_device_actions,
    device_commands,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerAction,
    DreameMowerProperty,
    DreameMowerState,
    DreameMowerStatus,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import (
    DreameLawnMowerConnectionError,
    cloud_strings,
    login_response,
    server,
)


def configure(client, delay=0):
    changes, updates = [], []
    fake = SimpleNamespace(
        status=SimpleNamespace(paused=False, started=True, cruising=False,
                               go_to_zone=None),
        capability=SimpleNamespace(cruising=False),
        action_mapping={DreameMowerAction.PAUSE: {"siid": 2, "aiid": 2}},
        _map_select_time=time.time() - 5 + delay if delay else None,
        _consumable_change=False, _protocol=SimpleNamespace(dreame_cloud=True),
        schedule_update=lambda *args: updates.append(args),
        _update_property=lambda *args: changes.append(args),
        _property_changed=lambda: changes.append("notify"),
    )
    client._device._pause_plan = lambda: (
        device_commands._DreameMowerDeviceCommandMixin._pause_plan(fake))
    client._async_refresh_authoritative_snapshot = AsyncMock(
        return_value=SimpleNamespace(paused=True, task_status="paused"))
    return fake, changes, updates


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("reply", ["success", "disconnect", "missing", "rejected"])
def test_native_pause_policy(monkeypatch, account, reply):
    strings = cloud_strings(account)
    requests = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        body = (await request.json())["data"]
        requests.append(body)
        assert body["method"] == "action"
        assert body["params"] == {"did": "42", "siid": 2, "aiid": 2, "in": []}
        if reply == "disconnect":
            request.transport.close()
            return web.Response()
        result = None if reply == "missing" else {
            "code": 7 if reply == "rejected" else 0}
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            fake, changes, updates = configure(client)
            try:
                if reply == "rejected":
                    with pytest.raises(DreameLawnMowerConnectionError):
                        await client.async_pause()
                else:
                    assert await client.async_pause() is None
                assert len(requests) == 1
                assert changes == [
                    (DreameMowerProperty.STATE, DreameMowerState.PAUSED.value),
                    (DreameMowerProperty.STATUS, DreameMowerStatus.PAUSED.value),
                    "notify",
                ]
                assert updates[:2] == [(10, True), (10, True)]
                assert updates[-1] == (
                    (1, True) if reply == "disconnect" else (6, True)
                )
                assert hasattr(fake, "_last_change") == (reply == "success")
                assert client._async_refresh_authoritative_snapshot.await_count == (
                    1 if reply in {"disconnect", "missing"} else 0)
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["delay", "command", "confirmation"])
@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_pause_lifetime(monkeypatch, stage, stop):
    strings = cloud_strings("dreame")
    requests = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            requests.append(await request.json())
            entered.set()
            if stage == "command":
                await release.wait()
            return web.json_response({"code": 0, "data": {"result": None}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            fake, changes, _ = configure(client, delay=4 if stage == "delay" else 0)
            operation = asyncio.create_task(client.async_pause())
            try:
                if stage == "delay":
                    await asyncio.sleep(0.05)
                    assert fake._map_select_time is None
                    assert "notify" not in changes
                else:
                    await asyncio.wait_for(entered.wait(), 2)
                    if stage == "confirmation":
                        await asyncio.sleep(0.05)
                if stop == "close":
                    await asyncio.wait_for(client.async_close(), 2)
                else:
                    operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(operation, 0.5)
                assert len(requests) == (0 if stage == "delay" else 1)
                client._async_refresh_authoritative_snapshot.assert_not_awaited()
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await asyncio.gather(operation, return_exceptions=True)
                await client.async_close()
    asyncio.run(scenario())


@pytest.mark.parametrize("before_dispatch", [False, True])
def test_pause_deadline_reconciles_only_attempted_command(monkeypatch, before_dispatch):
    monkeypatch.setattr(client_device_actions, "_ACTION_TIMEOUT", 0.2)
    strings = cloud_strings("dreame")
    requests = []

    async def scenario():
        release = asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            requests.append(await request.json())
            await release.wait()
            return web.json_response({"code": 0, "data": {"result": {"code": 0}}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            configure(client, delay=4 if before_dispatch else 0)
            try:
                if before_dispatch:
                    with pytest.raises(DreameLawnMowerConnectionError):
                        await client.async_pause()
                else:
                    assert await client.async_pause() is None
                assert len(requests) == (0 if before_dispatch else 1)
                assert client._async_refresh_authoritative_snapshot.await_count == (
                    0 if before_dispatch else 1)
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await client.async_close()
    asyncio.run(scenario())
