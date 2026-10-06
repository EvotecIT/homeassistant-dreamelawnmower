"""Native preference transactions preserve fresh revisions and exact readback."""

from __future__ import annotations

import asyncio
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_preference_writes,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    attempted_write_fields,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import (
    DreameLawnMowerConnectionError,
    cloud_strings,
    login_response,
    server,
)
from .test_mowing_preferences import _FakePreferenceCloud


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize(
    "mode",
    [
        "execute",
        "dry_run",
        "unconfirmed",
        "mode_and_settings",
        "disconnect",
        "reject",
        "unconfirmed_readback",
        "deadline",
    ],
)
def test_native_preference_write(monkeypatch, account_type, mode):
    if mode == "deadline":
        first = True

        def clock():
            nonlocal first
            value = time.monotonic() - (119.8 if first else 0)
            first = False
            return value

        monkeypatch.setattr(
            client_preference_writes, "time", SimpleNamespace(monotonic=clock)
        )
    peer = _FakePreferenceCloud()
    if mode == "mode_and_settings":
        peer.modes[0] = 0
    strings = cloud_strings(account_type)
    actions = []

    async def handler(request):
        if request.path == strings[17]:
            assert mode != "unconfirmed"
            return web.json_response(login_response(strings))
        action = (await request.json())["data"]["params"]["in"][0]
        actions.append(deepcopy(action))
        if action["m"] == "s":
            if mode == "disconnect":
                request.transport.close()
                return web.Response()
            if mode == "reject":
                result = {"out": [{"r": 2}]}
            elif mode in {"unconfirmed_readback", "deadline"}:
                result = {"out": [{"r": 0, "d": {"r": 0}}]}
            else:
                result = peer.call_app_action(action)
            if action["t"] == "PREP":
                # The accepted mode change gives the area a new revision and
                # unrelated settings. The following PRE must preserve both.
                value = peer.call_app_action(
                    {"m": "g", "t": "PRE", "d": {"idx": 0, "region": 11}}
                )["out"][0]["d"]
                value[0], value[6] = 20, 180
                peer.versions[11] = 20
                peer.preference_payloads[(0, 11)] = value
        else:
            result = peer.call_app_action(action)
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account_type)
            changes = {"mowing_height_cm": 5.0}
            if mode == "mode_and_settings":
                changes["preference_mode"] = "custom"
            try:
                operation = client.async_plan_app_mowing_preference_update(
                    map_index=0,
                    area_id=11,
                    changes=changes,
                    execute=mode != "dry_run",
                    confirm_write=mode != "unconfirmed",
                )
                if mode in {
                    "disconnect",
                    "reject",
                    "unconfirmed_readback",
                    "deadline",
                    "unconfirmed",
                }:
                    error = (
                        ValueError
                        if mode == "unconfirmed"
                        else (DreameLawnMowerConnectionError)
                    )
                    with pytest.raises(error) as caught:
                        await asyncio.wait_for(
                            operation, 1 if mode == "deadline" else 10
                        )
                    if mode in {"disconnect", "unconfirmed_readback", "deadline"}:
                        assert "mowing_height_cm" in attempted_write_fields(
                            caught.value
                        )
                    elif mode == "reject":
                        assert not attempted_write_fields(caught.value)
                else:
                    result = await operation
                    assert result["executed"] is (mode != "dry_run")
                    assert result["request_verified"] is (mode != "dry_run")
                    if mode != "dry_run":
                        assert result["readback"]["preference"]["mowing_height_cm"] == 5
                writes = [a for a in actions if a["m"] == "s"]
                if mode in {"dry_run", "unconfirmed"}:
                    assert not writes
                elif mode == "mode_and_settings":
                    assert [a["t"] for a in writes] == ["PREP", "PRE"]
                    assert writes[1]["d"][0] == 20
                    assert writes[1]["d"][6] == 180
                else:
                    assert len(writes) == 1
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["read", "write", "delay"])
@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_preference_cancellation_stops_requests_and_readback_delay(
    monkeypatch, stage, stop
):
    peer = _FakePreferenceCloud()
    strings = cloud_strings("dreame")
    actions = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def pause(seconds):
            assert seconds == 1.0
            entered.set()
            await release.wait()

        if stage == "delay":
            monkeypatch.setattr(
                client_preference_writes,
                "asyncio",
                SimpleNamespace(sleep=pause, timeout=asyncio.timeout),
            )

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            action = (await request.json())["data"]["params"]["in"][0]
            actions.append(deepcopy(action))
            if (stage == "read" and action["m"] == "g") or (
                stage == "write" and action["m"] == "s"
            ):
                entered.set()
                await release.wait()
            result = (
                {"out": [{"r": 0, "d": {"r": 0}}]}
                if (stage == "delay" and action["m"] == "s")
                else peer.call_app_action(action)
            )
            return web.json_response({"code": 0, "data": {"result": result}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            operation = asyncio.create_task(
                client.async_plan_app_mowing_preference_update(
                    map_index=0,
                    area_id=11,
                    changes={"mowing_height_cm": 5.0},
                    execute=True,
                    confirm_write=True,
                )
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
