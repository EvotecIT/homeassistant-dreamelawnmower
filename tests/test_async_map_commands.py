"""Native map/task commands preserve guards, uncertain replies and readback."""

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


def idle():
    return SimpleNamespace(
        activity="docked",
        state="idle",
        mowing_session_active=False,
        task_resumable=False,
        mowing=False,
        paused=False,
        returning=False,
        raw_attributes={},
    )


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("kind", ["map", "maintenance"])
@pytest.mark.parametrize(
    "reply", ["success", "disconnect", "missing", "rejected", "blocked"]
)
def test_native_map_commands(monkeypatch, account_type, kind, reply):
    strings = cloud_strings(account_type)
    actions = []

    async def handler(request):
        if request.path == strings[17]:
            assert reply != "blocked"
            return web.json_response(login_response(strings))
        action = (await request.json())["data"]["params"]["in"][0]
        actions.append(action)
        if action["m"] == "a":
            assert action == {
                "m": "a",
                "p": 0,
                "o": 200 if kind == "map" else 109,
                "d": {"idx": 1} if kind == "map" else {"point": [301]},
            }
            if reply == "disconnect":
                request.transport.close()
                return web.Response()
            result = (
                {"out": []}
                if reply == "missing"
                else {"out": [{"r": 7 if reply == "rejected" else 0, "d": {}}]}
            )
        else:
            assert action == {"m": "g", "t": "MAPL"}
            result = {"out": [{"r": 0, "d": [[0, 0, 1, 1, 0], [1, 1, 1, 1, 0]]}]}
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account_type)
            snapshot = idle()
            if reply == "blocked":
                snapshot.activity = snapshot.state = "mowing"
                snapshot.mowing_session_active = True
            client.async_refresh_authoritative_snapshot = AsyncMock(
                return_value=snapshot
            )
            try:
                operation = (
                    client.async_switch_current_map(1)
                    if kind == "map"
                    else (client.async_go_to_maintenance_point(301))
                )
                if reply in {"blocked", "rejected"} or (
                    kind == "maintenance" and reply in {"disconnect", "missing"}
                ):
                    with pytest.raises(DreameLawnMowerConnectionError):
                        await operation
                else:
                    result = await operation
                    assert result == ({"r": 0, "d": {}} if reply == "success" else None)
                client.async_refresh_authoritative_snapshot.assert_awaited_once_with()
                writes = [a for a in actions if a["m"] == "a"]
                assert len(writes) == (0 if reply == "blocked" else 1)
                reads = [a for a in actions if a["m"] == "g"]
                assert len(reads) == (
                    1 if kind == "map" and reply not in {"blocked", "rejected"} else 0
                )
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind,stage", [("map", "command"), ("map", "readback"), ("maintenance", "command")]
)
@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_native_map_command_cancellation_stops_followups(
    monkeypatch, kind, stage, stop
):
    strings = cloud_strings("dreame")
    actions = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            action = (await request.json())["data"]["params"]["in"][0]
            actions.append(action)
            if (stage == "command" and action["m"] == "a") or (
                stage == "readback" and action["m"] == "g"
            ):
                entered.set()
                await release.wait()
            result = {"out": [{"r": 0, "d": {}}]}
            return web.json_response({"code": 0, "data": {"result": result}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            client.async_refresh_authoritative_snapshot = AsyncMock(return_value=idle())
            operation = asyncio.create_task(
                client.async_switch_current_map(1)
                if kind == "map"
                else client.async_go_to_maintenance_point(301)
            )
            try:
                await asyncio.wait_for(entered.wait(), 2)
                count = len(actions)
                if stop == "close":
                    await asyncio.wait_for(client.async_close(), 2)
                else:
                    operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
                release.set()
                await client.async_close()
                assert len(actions) == count
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await asyncio.gather(operation, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())
