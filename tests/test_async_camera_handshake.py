"""Native stream handshake retains cleanup after uncertain start and shutdown."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DeviceException,
)

from .test_async_cloud_session import (
    DreameLawnMowerConnectionError,
    cloud_strings,
    login_response,
    server,
)
from .test_async_photo_info import photo_client
from .test_camera_features import DreameMowerProperty


def handshake_client(session, account):
    client = photo_client(session, account)
    device = client._device
    device._ready = True
    device._update_running = False
    device.cloud_connected = True
    device.device_connected = False
    device._select_update_properties = lambda: [DreameMowerProperty.STREAM_STATUS]
    device._finish_update = lambda: None
    client._snapshot_from_device = lambda current: SimpleNamespace(
        state="paused", raw_attributes={},
    )

    def apply(rows):
        for row in rows:
            if row.get("piid") == 1:
                device.status.stream_session = row["value"]
        return True

    device._handle_properties = apply
    return client


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("mode", ["app_action", "with_session", "no_session",
                                  "empty_session"])
@pytest.mark.parametrize("outcome", ["success", "rejected"])
def test_native_handshake_sequence(monkeypatch, account, mode, outcome):
    strings = cloud_strings(account)
    calls = []
    ended = False

    async def handler(request):
        nonlocal ended
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        if request.path.endswith("/getUserFeatures"):
            return web.json_response({"code": 0, "data": {}})
        body = await request.json()
        if "data" not in body:
            return web.json_response({"code": 0, "data": {}})
        rpc = body["data"]
        if rpc["method"] == "get_properties":
            calls.append("refresh")
            result = [{"siid": 10001, "piid": 1, "code": 0,
                       "value": "" if ended else "session-1"}]
        else:
            params = rpc["params"]
            assert params["did"] == "42"
            value = params["in"][0]
            if mode == "app_action":
                assert value["m"] == "a" and value["o"] == 400
                phase = "start" if value["d"]["on"] else "end"
                result = {"out": [{"r": 1 if outcome == "rejected"
                                   and phase == "start" else 0}]}
            else:
                assert params["siid"] == 10001 and params["aiid"] == 1
                payload = json.loads(value["value"])
                phase = payload["operType"]
                expected = {"operType": phase, "operation": "monitor"}
                if mode != "no_session":
                    expected["session"] = "" if mode == "empty_session" else "session-1"
                assert payload == expected
                rejected = outcome == "rejected" and phase == "start"
                result = {"code": 7 if rejected else 0}
            calls.append(phase)
            ended = phase == "end"
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = handshake_client(session, account)
            protocol = client._device._protocol.cloud
            monkeypatch.setattr(
                protocol, "request", lambda *a, **k: pytest.fail("sync HTTP"),
            )
            try:
                if outcome == "rejected":
                    with pytest.raises((
                        DeviceException, DreameLawnMowerConnectionError,
                    )):
                        await client.async_probe_camera_stream_handshake(
                            timeout=0, payload_mode=mode,
                        )
                    assert calls == ["refresh", "start", "end", "refresh"]
                else:
                    result = await client.async_probe_camera_stream_handshake(
                        timeout=0, payload_mode=mode,
                    )
                    assert result["cleanup_error"] is None
                    assert not result["after"]["stream_session_present"]
                    assert calls == ["refresh", "start", "refresh", "end", "refresh"]
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "close"])
@pytest.mark.parametrize("mode", ["app_action", "with_session"])
def test_handshake_cleanup_survives_interruption(monkeypatch, stop, mode):
    strings = cloud_strings("dreame")

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            if "data" not in body:
                return web.json_response({"code": 0, "data": {}})
            rpc = body["data"]
            if rpc["method"] == "get_properties":
                calls.append("refresh")
                result = [{"siid": 10001, "piid": 1, "code": 0, "value": "session-1"}]
            else:
                value = rpc["params"]["in"][0]
                phase = (
                    ("start" if value["d"]["on"] else "end")
                    if mode == "app_action" else json.loads(value["value"])["operType"]
                )
                calls.append(phase)
                if phase == "start":
                    entered.set()
                    await release.wait()
                result = {"out": [{"r": 0}]} if mode == "app_action" else {"code": 0}
            return web.json_response({"code": 0, "data": {"result": result}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = handshake_client(session, "dreame")
            task = asyncio.create_task(client.async_probe_camera_stream_handshake(
                timeout=0, payload_mode=mode,
            ))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                if stop == "close":
                    await asyncio.wait_for(client.async_close(), 3)
                else:
                    task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
                assert calls == ["refresh", "start", "end", "refresh"]
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())

@pytest.mark.parametrize("stop", ["cancel", "close"])
@pytest.mark.parametrize("finish", ["release", "deadline"])
def test_handshake_drains_bounded_cleanup(monkeypatch, stop, finish):
    from functools import partial

    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        client_camera_handshake,
    )

    if finish == "deadline":
        monkeypatch.setattr(
            client_camera_handshake, "finish_owned_cleanup",
            partial(client_camera_handshake.finish_owned_cleanup, timeout=0.15),
        )
    strings = cloud_strings("dreame")

    async def scenario():
        started, ending, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            if "data" not in body:
                return web.json_response({"code": 0, "data": {}})
            rpc = body["data"]
            if rpc["method"] == "get_properties":
                calls.append("refresh")
                result = [{"siid": 10001, "piid": 1, "code": 0, "value": "session-1"}]
            else:
                phase = "start" if rpc["params"]["in"][0]["d"]["on"] else "end"
                calls.append(phase)
                (started if phase == "start" else ending).set()
                await release.wait()
                result = {"out": [{"r": 0}]}
            return web.json_response({"code": 0, "data": {"result": result}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = handshake_client(session, "dreame")
            device = client._device
            task = asyncio.create_task(
                client.async_probe_camera_stream_handshake(timeout=0)
            )
            closer = None
            try:
                await asyncio.wait_for(started.wait(), 3)
                if stop == "close":
                    closer = asyncio.create_task(client.async_close())
                else:
                    task.cancel()
                await asyncio.wait_for(ending.wait(), 3)
                assert client._device is device and not session.closed
                assert not task.done()
                if closer is not None:
                    assert not closer.done()
                    with pytest.raises(DreameLawnMowerConnectionError, match="closing"):
                        await client.async_get_camera_stream_inputs()
                else:
                    # A second cancellation must not abandon the in-flight end call.
                    task.cancel()
                if finish == "release":
                    release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
                if closer is not None:
                    await asyncio.wait_for(closer, 3)
                assert calls == ["refresh", "start", "end"] + (
                    ["refresh"] if finish == "release" else []
                )
                assert not client._cloud_read_tasks and not session.closed
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                if closer is not None:
                    await asyncio.gather(closer, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())
