"""Native settings keep preserved values, exact readback and owned lifetime."""
from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession, web

from .test_async_app_commands import client_for
from .test_async_cloud_session import (
    DreameLawnMowerConnectionError,
    cloud_strings,
    login_response,
    server,
)
from .test_device_settings import _cfg


def operation(client, setting):
    if setting == "BAT":
        return client.async_set_charging_period(enabled=True)
    if setting == "WRP":
        return client.async_set_rain_protection(enabled=False)
    return client.async_set_anti_theft_settings(lift_alarm_enabled=True)


def apply_action(config, action):
    kind, data = action["t"], action["d"]
    if kind == "BAT":
        config[kind][3:] = data["value"]
    elif kind == "WRP":
        config[kind] = [data["value"], data["time"], data["sen"]]
    else:
        config[kind] = data["value"]


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("setting", ["BAT", "WRP", "ATA"])
@pytest.mark.parametrize("reply", ["success", "disconnect", "rejected", "ignored",
                                   "preserved_changed", "unavailable"])
def test_native_device_settings(monkeypatch, account, setting, reply):
    strings = cloud_strings(account)
    config = _cfg(anti_theft=[0, 1, 0])["d"]
    if reply == "unavailable":
        config.pop(setting)
    actions = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        action = (await request.json())["data"]["params"]["in"][0]
        actions.append(action)
        if action["m"] == "s":
            assert action["t"] == setting
            expected = ({"type": "charging", "value": [1, 1080, 480]}
                        if setting == "BAT" else {"value": 0, "time": 8, "sen": 1}
                        if setting == "WRP" else {"value": [1, 1, 0]})
            assert action["d"] == expected
            if reply == "disconnect":
                request.transport.close()
                return web.Response()
            if reply not in {"rejected", "ignored"}:
                apply_action(config, action)
            if reply == "preserved_changed":
                index = 0 if setting == "BAT" else 2 if setting == "WRP" else 1
                config[setting][index] = 0
            result = {"r": 7 if reply == "rejected" else 0, "d": {}}
        else:
            result = {"r": 0, "d": config if action["t"] == "CFG" else {"endTime": 0}}
        return web.json_response({"code": 0, "data": {"result": {"out": [result]}}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            try:
                if reply == "success":
                    result = await operation(client, setting)
                    assert result["available"] is True
                else:
                    with pytest.raises(DreameLawnMowerConnectionError):
                        await operation(client, setting)
                assert sum(a["m"] == "s" for a in actions) == (
                    0 if reply == "unavailable" else 1)
                assert len(actions) == (
                    1 if reply == "unavailable" else 2 if reply in {
                        "disconnect", "rejected"} else 4 if setting == "WRP" else 3)
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["preflight", "write", "readback", "queue"])
@pytest.mark.parametrize("shutdown", [False, True])
def test_settings_lifetime(monkeypatch, stage, shutdown):
    strings = cloud_strings("dreame")
    actions = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            action = (await request.json())["data"]["params"]["in"][0]
            actions.append(action)
            if len(actions) == {"preflight": 1, "write": 2, "readback": 3}[stage]:
                entered.set()
                await release.wait()
            result = _cfg(charging_enabled=1 if len(actions) > 1 else 0)
            if action["m"] == "s":
                result = {"r": 0, "d": {}}
            return web.json_response({"code": 0, "data": {"result": {"out": [result]}}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            if stage == "queue":
                await client._device_settings_write_lock.acquire()
            task = asyncio.create_task(operation(client, "BAT"))
            try:
                if stage == "queue":
                    await asyncio.sleep(0.05)
                else:
                    await asyncio.wait_for(entered.wait(), 2)
                if shutdown:
                    await asyncio.wait_for(client.async_close(), 2)
                else:
                    task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 0.5)
                assert len(actions) == {
                    "queue": 0, "preflight": 1, "write": 2, "readback": 3}[stage]
                assert not client._cloud_read_tasks
            finally:
                if stage == "queue":
                    client._device_settings_write_lock.release()
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                await client.async_close()
    asyncio.run(scenario())


def test_concurrent_partial_updates_read_latest_confirmed_settings(monkeypatch):
    strings = cloud_strings("dreame")
    config = _cfg()["d"]
    actions = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        action = (await request.json())["data"]["params"]["in"][0]
        actions.append(action)
        await asyncio.sleep(0.01)
        if action["m"] == "s":
            apply_action(config, action)
            result = {"r": 0, "d": {}}
        else:
            result = {"r": 0, "d": config}
        return web.json_response({"code": 0, "data": {"result": {"out": [result]}}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            try:
                await asyncio.gather(
                    client.async_set_charging_period(enabled=True),
                    client.async_set_charging_period(start_minutes=120),
                )
                assert config["BAT"] == [15, 95, 1, 1, 120, 480]
                assert [a["m"] for a in actions] == ["g", "s", "g", "g", "s", "g"]
            finally:
                await client.async_close()
    asyncio.run(scenario())
