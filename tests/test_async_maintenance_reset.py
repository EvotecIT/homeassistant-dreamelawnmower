"""Native maintenance reset guards and evidence across network outcomes."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerCommandRejectedError,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import (
    DreameLawnMowerConnectionError,
    cloud_strings,
    login_response,
    server,
)


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize(
    "mode",
    [
        "dry_run",
        "execute",
        "unconfirmed",
        "bad_preflight",
        "rejected",
        "disconnect",
        "bad_refresh",
    ],
)
def test_native_maintenance_reset(monkeypatch, account_type, mode):
    strings = cloud_strings(account_type)
    actions = []
    values = [100, 200, 300, -1]

    async def handler(request):
        if request.path == strings[17]:
            assert mode != "unconfirmed"
            return web.json_response(login_response(strings))
        body = await request.json()
        action = body["data"]["params"]["in"][0]
        actions.append(action)
        if action["m"] == "s":
            if mode == "disconnect":
                request.transport.close()
                return web.Response()
            if mode != "rejected":
                values[:] = action["d"]["value"]
            result = {"r": 1 if mode == "rejected" else 0, "d": {"value": values}}
        elif mode == "bad_preflight" or (
            mode == "bad_refresh" and any(a["m"] == "s" for a in actions)
        ):
            result = {"r": 0, "d": {}}
        else:
            result = {"r": 0, "d": {"value": values.copy()}}
        return web.json_response({"code": 0, "data": {"result": {"out": [result]}}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account_type)
            try:
                operation = client.async_plan_maintenance_reset(
                    item="blade",
                    execute=mode != "dry_run",
                    confirm_write=mode != "unconfirmed",
                )
                if mode in {"unconfirmed", "bad_preflight", "rejected", "disconnect"}:
                    error = (
                        ValueError
                        if mode == "unconfirmed"
                        else DreameLawnMowerCommandRejectedError
                        if mode == "rejected"
                        else DreameLawnMowerConnectionError
                    )
                    with pytest.raises(error):
                        await operation
                else:
                    result = await operation
                    assert result["previous_cms"] == [100, 200, 300, -1]
                    assert result["updated_cms"] == [0, 200, 300, -1]
                    assert result["executed"] is (mode != "dry_run")
                    assert result["dry_run"] is (mode == "dry_run")
                    if mode == "execute":
                        assert result["refreshed_cms"] == [0, 200, 300, -1]
                    if mode == "bad_refresh":
                        assert result["refreshed_cms"] is None
                        assert result["executed"]
                writes = [a for a in actions if a["m"] == "s"]
                assert len(writes) == (
                    0 if mode in {"dry_run", "unconfirmed", "bad_preflight"} else 1
                )
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["preflight", "write", "refresh"])
def test_cancelled_reset_stops_later_operations(monkeypatch, stage):
    strings = cloud_strings("dreame")

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        actions = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            action = (await request.json())["data"]["params"]["in"][0]
            actions.append(action)
            if len(actions) == {"preflight": 1, "write": 2, "refresh": 3}[stage]:
                entered.set()
                await release.wait()
            return web.json_response(
                {
                    "code": 0,
                    "data": {
                        "result": {
                            "out": [{"r": 0, "d": {"value": [100, 200, 300, -1]}}]
                        }
                    },
                }
            )

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            operation = asyncio.create_task(
                client.async_plan_maintenance_reset(
                    item="blade", execute=True, confirm_write=True
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), 2)
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(operation, 2)
                count = len(actions)
                release.set()
                await asyncio.wait_for(client.async_close(), 2)
                assert len(actions) == count
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await asyncio.gather(operation, return_exceptions=True)
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


def test_reset_deadline_includes_preflight_and_prevents_write(monkeypatch):
    import time
    from types import SimpleNamespace

    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        client_maintenance_reset,
    )

    monkeypatch.setattr(
        client_maintenance_reset,
        "time",
        SimpleNamespace(monotonic=lambda: time.monotonic() - 19.9),
    )
    strings = cloud_strings("dreame")
    actions = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        actions.append((await request.json())["data"]["params"]["in"][0])
        await asyncio.sleep(0.2)
        return web.json_response(
            {
                "code": 0,
                "data": {
                    "result": {"out": [{"r": 0, "d": {"value": [100, 200, 300, -1]}}]}
                },
            }
        )

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            try:
                with pytest.raises(DreameLawnMowerConnectionError):
                    await client.async_plan_maintenance_reset(
                        item="blade", execute=True, confirm_write=True
                    )
                assert all(action["m"] == "g" for action in actions)
                assert len(actions) <= 1
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()

    asyncio.run(scenario())
