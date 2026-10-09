"""TX video HTTP payload, response, and bounded diagnostic read contracts."""

import asyncio

import pytest
from aiohttp import ClientSession, web

from .test_async_cloud_session import (
    OPTIONS,
    DreameCloudSession,
    DreameLawnMowerConnectionError,
    cloud_strings,
    login_response,
    server,
)


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("kind,path", [
    ("access_token", "user/accesstoken"),
    ("identity", "mgr/dev/getIdentity"),
    ("p2p", "dev/getP2PInfo"),
    ("eligibility", "dev/isDevUser"),
])
def test_video_read_wire_contract(monkeypatch, account, kind, path):
    strings = cloud_strings(account)
    requests = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        requests.append((request.path, await request.json()))
        return web.json_response({"data": {"vendor": "result"}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **{**OPTIONS, "account_type": account})
            result = await cloud.async_get_video_data(
                kind, "42", access_token="video-token", uid="owner", model="mower",
            )
            assert result == {"vendor": "result"}
            expected = {"os": 1} if kind == "access_token" else {
                "did": "42", "os": 1, "accesstoken": "video-token",
                "accessToken": "video-token",
            }
            if kind == "identity":
                expected.update(uid="owner", model="mower")
            assert requests == [("/dreame-third-video/tx/" + path, expected)]
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [401, 503])
def test_eligibility_does_not_retry_or_reauthenticate(monkeypatch, status):
    strings = cloud_strings("dreame")
    calls = []

    async def handler(request):
        calls.append(request.path)
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response({}, status=status)

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **OPTIONS)
            with pytest.raises(DreameLawnMowerConnectionError):
                await cloud.async_get_video_data("eligibility", "42")
            assert calls == [strings[17], "/dreame-third-video/tx/dev/isDevUser"]
            if status == 401:
                assert cloud._token is None

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_video_read_releases_request_and_lock(monkeypatch, stop):
    strings = cloud_strings("dreame")
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        calls.append(request.path)
        entered.set()
        await release.wait()
        return web.json_response({"code": 0, "data": {}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **OPTIONS)
            deadline = (
                asyncio.get_running_loop().time() + 0.2
                if stop == "deadline" else None
            )
            task = asyncio.create_task(cloud.async_get_video_data(
                "eligibility", "42", deadline=deadline,
            ))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if stop == "cancel":
                    task.cancel()
                with pytest.raises(asyncio.CancelledError if stop == "cancel"
                                   else DreameLawnMowerConnectionError):
                    await task
                assert not cloud._lock.locked()
                assert len(calls) == 1
                assert not session.closed
            finally:
                release.set()

    asyncio.run(scenario())

@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("incomplete", [False, True])
def test_native_camera_runtime_workflow(monkeypatch, account, incomplete):
    import json

    from .test_async_app_commands import client_for
    from .test_cloud_key_definition import ENCRYPTED_APP_ID, ENCRYPTED_APP_SECRET

    strings = cloud_strings(account)
    calls = []

    async def handler(request):
        calls.append(request.path)
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        if request.path.endswith("/device/info"):
            return web.json_response({"code": 0, "data": {
                "did": "42", strings[8]: "owner", strings[35]: "mower",
            }})
        body = await request.json()
        if request.path.endswith("/accesstoken"):
            result = {"accessToken": "private-video-token"}
        else:
            assert body["accesstoken"] == body["accessToken"] == "private-video-token"
            if request.path.endswith("/getIdentity"):
                assert body["uid"] == "owner"
                assert body["model"] == "mower"
                result = {} if incomplete else {
                    "channelId": "private-channel", "productId": "private-product",
                    "deviceName": "private-camera", "secretId": ENCRYPTED_APP_ID,
                    "secretKey": ENCRYPTED_APP_SECRET,
                }
            elif request.path.endswith("/getP2PInfo"):
                result = {"p2pInfo": "private-p2p"}
            else:
                assert request.path.endswith("/isDevUser") and incomplete
                return web.json_response({}, status=503)
        return web.json_response({"code": 0, "data": result})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            protocol = client._device._protocol.cloud
            protocol._uid = None

            def unexpected(*args, **kwargs):
                pytest.fail("Camera provisioning used synchronous networking")

            monkeypatch.setattr(protocol, "request", unexpected)
            monkeypatch.setattr(protocol, "login", unexpected)
            try:
                result = await client.async_get_camera_stream_runtime_inputs()
                assert result.ready is not incomplete
                assert result.diagnostics["completed"] is True
                if not incomplete:
                    assert result.xp2p_id == "private-product/private-camera"
                    assert result.as_dict()["p2p_info"] == "private-p2p"
                diagnostics = json.dumps(result.diagnostics)
                for secret in ("private-video-token", "private-channel",
                               "private-product", "private-camera", "private-p2p",
                               ENCRYPTED_APP_ID, ENCRYPTED_APP_SECRET):
                    assert secret not in diagnostics
                stages = [path.rsplit("/", 1)[-1] for path in calls[1:]]
                expected = ["accesstoken", "info", "getIdentity", "getP2PInfo"]
                assert stages == expected + (
                    ["isDevUser"] if incomplete else []
                )
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_camera_credentials_lifetime(monkeypatch, stop):
    from .test_async_app_commands import client_for

    strings = cloud_strings("dreame")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        entered.set()
        await release.wait()
        return web.json_response({"code": 0, "data": {}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            task = asyncio.create_task(client.async_get_camera_stream_runtime_inputs())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if stop == "cancel":
                    task.cancel()
                else:
                    await client.async_close()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                await client.async_close()

    asyncio.run(scenario())
