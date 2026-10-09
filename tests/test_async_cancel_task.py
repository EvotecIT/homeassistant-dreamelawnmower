"""Native cancellation confirms task settlement without replaying STOP."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client as client_module,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_device_actions,
    device_commands,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerAction,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerCommandRejectedError,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_pause import configure
from .test_docking import _task_snapshot


def configure_stop(client):
    fake, changes, updates = configure(client)
    fake.status.fast_mapping = False
    fake.action_mapping = {DreameMowerAction.STOP: {"siid": 2, "aiid": 1}}
    fake._map_manager = None
    fake._update_status = lambda *args: changes.append(args)
    client._device._stop_plan = lambda **kwargs: (
        device_commands._DreameMowerDeviceCommandMixin._stop_plan(fake, **kwargs))
    return fake, changes, updates


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("reply", ["success", "disconnect", "missing", "timeout",
                                   "rejected"])
def test_native_cancel_settlement(monkeypatch, account, reply):
    monkeypatch.setattr(
        client_module, "_TASK_CANCEL_CONFIRMATION_INITIAL_DELAY_SECONDS", 0.001)
    if reply == "timeout":
        monkeypatch.setattr(client_device_actions, "_ACTION_TIMEOUT", 0.2)
    strings = cloud_strings(account)
    requests = []

    async def scenario():
        release = asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = (await request.json())["data"]
            requests.append(body)
            assert body["method"] == "action"
            assert body["params"] == {"did": "42", "siid": 2, "aiid": 1, "in": []}
            if reply == "disconnect":
                request.transport.close()
                return web.Response()
            if reply == "timeout":
                await release.wait()
            result = None if reply == "missing" else {
                "code": 7 if reply == "rejected" else 0}
            return web.json_response({"code": 0, "data": {"result": result}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            _, changes, _ = configure_stop(client)
            active = _task_snapshot(state="paused", active=True, paused=True)
            inactive = _task_snapshot(state="idle", active=False)
            client.async_refresh_authoritative_snapshot = AsyncMock(
                side_effect=[active, active if reply == "rejected" else inactive])
            try:
                if reply == "rejected":
                    with pytest.raises(DreameLawnMowerCommandRejectedError):
                        await client.async_cancel_current_task()
                else:
                    assert await client.async_cancel_current_task() is True
                assert len(requests) == 1
                assert client.async_refresh_authoritative_snapshot.await_count == 2
                transitions = [
                    change for change in changes if isinstance(change, tuple)]
                assert len(transitions) == (1 if reply == "success" else 0)
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await client.async_close()
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["preflight", "command", "confirmation"])
@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_cancel_task_lifetime(monkeypatch, stage, stop):
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
            return web.json_response({"code": 0, "data": {"result": {"code": 0}}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            configure_stop(client)

            async def preflight(**kwargs):
                if stage == "preflight":
                    entered.set()
                    await release.wait()
                return _task_snapshot(state="paused", active=True, paused=True)

            client.async_refresh_authoritative_snapshot = AsyncMock(
                side_effect=preflight)
            operation = asyncio.create_task(client.async_cancel_current_task())
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
                assert len(requests) == (0 if stage == "preflight" else 1)
                assert client.async_refresh_authoritative_snapshot.await_count == 1
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await asyncio.gather(operation, return_exceptions=True)
                await client.async_close()
    asyncio.run(scenario())
