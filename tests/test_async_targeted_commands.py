"""Native targeted writes keep exact task confirmation and operation lifetime."""
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

KINDS = {"zone": (102, "region", [2]), "edge": (101, "edge", [[3, 0]]),
         "spot": (103, "area", [4])}


def snapshot(kind=None):
    return SimpleNamespace(
        state="mowing" if kind else "charging",
        task_status={"zone": "zone_cleaning", "edge": "segment_cleaning",
                     "spot": "spot_cleaning"}.get(kind, "idle"),
        task_operation=KINDS[kind][0] if kind else None,
        task_region_ids=(2,) if kind == "zone" else None,
        task_area_ids=(4,) if kind == "spot" else None,
        current_zone_id=2 if kind == "zone" else None,
        active_segment_count=1 if kind else 0,
        mowing_session_active=bool(kind), started=bool(kind), mowing=bool(kind),
        docked=not kind, task_resumable=False,
    )


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("reply", ["success", "disconnect", "missing", "rejected"])
def test_native_targeted_command(monkeypatch, account, kind, reply):
    strings = cloud_strings(account)
    actions = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        action = (await request.json())["data"]["params"]["in"][0]
        actions.append(action)
        operation, field, values = KINDS[kind]
        assert action == {"m": "a", "p": 0, "o": operation,
                          "d": {field: values}}
        if reply == "disconnect":
            request.transport.close()
            return web.Response()
        result = {"out": []} if reply == "missing" else {
            "out": [{"r": 7 if reply == "rejected" else 0, "d": {}}]}
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            client._async_refresh_authoritative_snapshot = AsyncMock(
                side_effect=[snapshot(), snapshot(kind)])
            try:
                command = getattr(client, f"async_start_{kind}_mowing")
                if reply == "rejected":
                    with pytest.raises(DreameLawnMowerConnectionError):
                        await command(KINDS[kind][2])
                else:
                    result = await command(KINDS[kind][2])
                    assert result == (
                        {"r": 0, "d": {}} if reply == "success" else None)
                assert len(actions) == 1
                assert client._async_refresh_authoritative_snapshot.await_count == (
                    1 if reply == "rejected" else 2)
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("stage", ["command", "confirmation"])
@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_targeted_command_lifetime(monkeypatch, kind, stage, stop):
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
            return web.json_response({"code": 0, "data": {"result": {
                "out": [{"r": 0, "d": {}}]}}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            client._async_refresh_authoritative_snapshot = AsyncMock(
                return_value=snapshot())
            operation = asyncio.create_task(
                getattr(client, f"async_start_{kind}_mowing")(KINDS[kind][2]))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if stage == "confirmation":
                    await asyncio.sleep(0.1)  # Within the first 0.5s readback delay.
                if stop == "close":
                    await asyncio.wait_for(client.async_close(), 2)
                else:
                    operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(operation, 0.3)
                assert len(actions) == 1
                client._async_refresh_authoritative_snapshot.assert_awaited_once()
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await asyncio.gather(operation, return_exceptions=True)
                await client.async_close()
    asyncio.run(scenario())
