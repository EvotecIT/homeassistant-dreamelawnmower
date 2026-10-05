"""Native cloud HTTP contracts against a local aiohttp server."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client import (
    DreameLawnMowerClient,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.cloud_session import (
    MAX_CLOUD_RESPONSE_BYTES,
    DreameCloudSession,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.cloud_wire import (
    cloud_login_data,
    cloud_strings,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerAuthError,
    DreameLawnMowerConnectionError,
)

OPTIONS = {
    "username": "account@example.invalid",
    "password": "private-password",
    "country": "eu",
    "account_type": "dreame",
}


@asynccontextmanager
async def server(monkeypatch, handler):
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    monkeypatch.setattr(
        DreameCloudSession,
        "_base_url",
        property(lambda _: f"http://127.0.0.1:{port}"),
    )
    try:
        yield
    finally:
        await runner.cleanup()


def login_response(strings, token="access-secret"):
    return {
        strings[18]: token,
        strings[19]: "refresh-secret",
        strings[20]: 3600,
        strings[22]: "tenant-value",
    }


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
def test_discovery_uses_borrowed_session_and_shared_wire(monkeypatch, account_type):
    strings = cloud_strings(account_type)
    seen = []

    async def handler(request):
        seen.append(request.path)
        if request.path == strings[17]:
            assert await request.text() == cloud_login_data(
                strings,
                OPTIONS["username"],
                OPTIONS["password"],
                None,
            )
            return web.json_response(login_response(strings))
        assert request.headers[strings[46]] == "access-secret"
        assert request.headers[strings[50]] == "tenant-value"
        return web.json_response(
            {
                "code": 0,
                "data": {
                    "page": {
                        "records": [
                            {
                                "did": "42",
                                "model": "dreame.mower.p2255",
                                "customName": "Garden",
                            },
                        ]
                    }
                },
            }
        )

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            result = await DreameLawnMowerClient.async_discover_devices(
                **{**OPTIONS, "account_type": account_type},
                session=session,
            )
            assert [item.did for item in result] == ["42"]
            assert result[0].account_type == account_type
            assert not session.closed
            assert len(seen) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["oversized", "malformed", "wrong_shape", "redirect"])
def test_bad_responses_fail_closed_without_closing_session(monkeypatch, mode, caplog):
    calls = []

    async def handler(request):
        calls.append(request.path)
        if mode == "oversized":
            return web.Response(body=b"x" * (MAX_CLOUD_RESPONSE_BYTES + 1))
        if mode == "wrong_shape":
            return web.json_response(["private-response"])
        if mode == "redirect":
            return web.Response(
                status=302, headers={"Location": "/other"}, text="private"
            )
        return web.Response(text="private-response")

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **OPTIONS)
            with pytest.raises(DreameLawnMowerConnectionError):
                await cloud.async_login()
            assert not session.closed

    asyncio.run(scenario())
    assert len(calls) == 1
    assert "private-response" not in caplog.text


def test_cancelled_login_releases_response_and_operation_lock(monkeypatch):
    strings = cloud_strings("dreame")

    async def scenario():
        started = asyncio.Event()
        finish = asyncio.Event()
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                response = web.StreamResponse()
                await response.prepare(request)
                started.set()
                await finish.wait()
                return response
            return web.json_response(login_response(strings))

        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **OPTIONS)
            task = asyncio.create_task(cloud.async_login())
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            finish.set()
            await cloud.async_login(timeout=1)
            assert not session.closed

    asyncio.run(scenario())


def test_timeout_includes_waiting_for_another_operation(monkeypatch):
    strings = cloud_strings("dreame")

    async def scenario():
        started = asyncio.Event()
        finish = asyncio.Event()
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            started.set()
            await finish.wait()
            return web.json_response(login_response(strings))

        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **OPTIONS)
            first = asyncio.create_task(cloud.async_login())
            await started.wait()
            try:
                with pytest.raises(DreameLawnMowerConnectionError, match="timed out"):
                    await cloud.async_login(timeout=0.02)
                assert calls == 1
            finally:
                finish.set()
                await first

    asyncio.run(scenario())


def test_rejected_refresh_token_falls_back_once_to_credentials(monkeypatch):
    strings = cloud_strings("dreame")
    payloads = []

    async def handler(request):
        data = await request.text()
        payloads.append(data)
        if len(payloads) == 2:
            return web.json_response(
                {"error_description": "invalid refresh token"},
                status=401,
            )
        return web.json_response(login_response(strings))

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **OPTIONS)
            await cloud.async_login()
            await cloud.async_login()

    asyncio.run(scenario())
    password_data = cloud_login_data(
        strings,
        OPTIONS["username"],
        OPTIONS["password"],
        None,
    )
    assert payloads == [
        password_data,
        cloud_login_data(strings, "", "", "refresh-secret"),
        password_data,
    ]


def test_auth_rejection_does_not_expose_server_secrets(monkeypatch, caplog):
    async def handler(request):
        return web.json_response({"error_description": "private-password"}, status=403)

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            with pytest.raises(DreameLawnMowerAuthError, match="HTTP 403") as error:
                await DreameCloudSession(session, **OPTIONS).async_login()
            assert "private-password" not in str(error.value)
            assert not session.closed

    asyncio.run(scenario())
    assert "private-password" not in caplog.text


@pytest.mark.parametrize("reject", [False, True])
def test_standalone_discovery_closes_only_its_owned_session(monkeypatch, reject):
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import client

    strings = cloud_strings("dreame")
    sessions = []

    def create_session():
        session = ClientSession()
        sessions.append(session)
        return session

    async def handler(request):
        if reject:
            return web.json_response({"error": "rejected"}, status=403)
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response({"code": 0, "data": []})

    async def scenario():
        async with server(monkeypatch, handler):
            monkeypatch.setattr(client, "_ClientSession", create_session)
            if reject:
                with pytest.raises(DreameLawnMowerAuthError):
                    await DreameLawnMowerClient.async_discover_devices(**OPTIONS)
            else:
                assert (
                    await DreameLawnMowerClient.async_discover_devices(**OPTIONS) == []
                )
            assert len(sessions) == 1
            assert sessions[0].closed

    asyncio.run(scenario())


def test_read_only_inventory_retries_connection_failure(monkeypatch):
    strings = cloud_strings("dreame")
    calls = 0

    async def handler(request):
        nonlocal calls
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        calls += 1
        if calls == 1:
            request.transport.close()
            return web.Response()
        return web.json_response({"code": 0, "data": []})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            result = await DreameCloudSession(session, **OPTIONS).async_get_devices()
            assert result == []
            assert calls == 2

    asyncio.run(scenario())


def test_slow_response_body_obeys_total_deadline(monkeypatch):
    async def scenario():
        finish = asyncio.Event()

        async def handler(request):
            response = web.StreamResponse()
            await response.prepare(request)
            await finish.wait()
            return response

        async with server(monkeypatch, handler), ClientSession() as session:
            try:
                with pytest.raises(DreameLawnMowerConnectionError, match="timed out"):
                    await DreameCloudSession(session, **OPTIONS).async_login(
                        timeout=0.05
                    )
                assert not session.closed
            finally:
                finish.set()

    asyncio.run(scenario())
