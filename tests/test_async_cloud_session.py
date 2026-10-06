"""Native cloud HTTP contracts against a local aiohttp server."""

from __future__ import annotations

import asyncio
import gzip
import json
from contextlib import asynccontextmanager

import pytest
from aiohttp import BasicAuth, ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client import (
    DreameLawnMowerClient,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.cloud_session import (
    MAX_CLOUD_RESPONSE_BYTES,
    DreameCloudSession,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.cloud_wire import (
    cloud_headers,
    cloud_login_data,
    cloud_strings,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerAuthError,
    DreameLawnMowerConnectionError,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
)

OPTIONS = {
    "username": "account@example.invalid",
    "password": "private-password",
    "country": "eu",
    "account_type": "dreame",
}


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("cached_host", [False, True])
def test_public_current_map_read_uses_native_http(
    monkeypatch, account_type, cached_host,
):
    strings = cloud_strings(account_type)
    seen = []

    async def handler(request):
        seen.append(request.path)
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        if request.path.endswith("/device/info"):
            return web.json_response({"code": 0, "data": {
                "did": "42", strings[8]: "user", strings[35]: "dreame.mower.g2408",
                strings[9]: "hub.example.invalid", strings[10]: "{}",
            }})
        assert request.path == f"/{strings[37]}-hub/{strings[27]}/{strings[38]}"
        body = await request.json()
        assert body["id"] == 101
        assert body["data"]["params"] == {
            "did": "42", "siid": 2, "aiid": 50, "in": [{"m": "g", "t": "MAPL"}],
        }
        return web.json_response({"code": 0, "data": {"result": {"out": [
            {"r": 0, "d": [[0, 0, 1, 1, 0], [1, 1, 1, 1, 0]]},
        ]}}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **{**OPTIONS, "account_type": account_type},
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type=account_type, country="eu",
                ), session=session,
            )
            device = client._ensure_device()
            protocol = device._protocol.cloud
            protocol._id = 100
            if cached_host:
                protocol._host = "hub.example.invalid"

            def unexpected_sync(*args, **kwargs):
                pytest.fail("Native map read used synchronous HTTP or MQTT startup")

            monkeypatch.setattr(protocol, "request", unexpected_sync)
            monkeypatch.setattr(protocol, "connect", unexpected_sync)
            try:
                assert await client.async_get_current_app_map_index() == 1
                assert protocol._id == 101
                assert len(seen) == (2 if cached_host else 3)
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "close", "deadline"])
@pytest.mark.parametrize("read_kind", ["map", "batch", "batch_hint", "plugin"])
def test_native_read_releases_ownership_when_interrupted(monkeypatch, stop, read_kind):
    import time

    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        client_app_reads,
    )

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        strings = cloud_strings("dreame")

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            entered.set()
            await release.wait()
            return web.json_response({"code": 0, "data": {"result": None}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **OPTIONS, session=session,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type="dreame", country="eu",
                ),
            )
            protocol = client._ensure_device()._protocol.cloud
            protocol._host = "hub.example.invalid"
            timeout = 0.5 if stop == "deadline" else 5
            if read_kind == "map":
                operation = client_app_reads.async_read_app_action(
                    client, {"m": "g", "t": "MAPL"},
                    deadline=time.monotonic() + timeout,
                )
            elif read_kind == "plugin":
                operation = client._async_cloud_read(
                    lambda cloud: cloud.async_get_app_plugin_version(
                        "dreame.mower.g2408", timeout=timeout,
                    )
                )
            else:
                operation = client.async_get_batch_schedules(
                    discover_map_index=read_kind == "batch_hint", timeout=timeout,
                )
            task = asyncio.create_task(operation)
            try:
                await asyncio.wait_for(entered.wait(), 3)
                if stop == "close":
                    await asyncio.wait_for(client.async_close(), 3)
                elif stop == "cancel":
                    task.cancel()
                expected = (
                    DreameLawnMowerConnectionError if stop == "deadline"
                    else asyncio.CancelledError
                )
                with pytest.raises(expected):
                    await task
                assert not client._cloud_read_tasks
                if read_kind in {"map", "batch_hint"}:
                    assert not protocol._async_rpc_gate.locked()

                def can_acquire():
                    acquired = protocol._operation_lock().acquire(blocking=False)
                    if acquired:
                        protocol._operation_lock().release()
                    return acquired

                assert await asyncio.to_thread(can_acquire)
                assert not session.closed
            finally:
                release.set()
                await client.async_close()

    asyncio.run(scenario())


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
        yield f"http://127.0.0.1:{port}"
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize(
    "hint_mode", ["explicit", "skip", "discover", "failed", "missing"],
)
def test_public_batch_schedule_read_uses_native_http(
    monkeypatch, account_type, hint_mode,
):
    strings = cloud_strings(account_type)
    seen = []
    schedule = json.dumps({"d": [], "v": 19383})

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        seen.append(request.path)
        body = await request.json()
        if "data" in body:
            assert body["data"]["params"]["in"] == [{"m": "g", "t": "MAPL"}]
            if hint_mode == "failed":
                return web.json_response({"code": 500})
            return web.json_response({"code": 0, "data": {"result": {"out": [
                {"r": 0, "d": [[2, 1, 1, 1, 0]]},
            ]}}})
        assert request.path == "/" + "/".join(strings[i] for i in (23, 26, 44))
        assert body == {
            "did": "42",
            strings[35]: [*(f"SCHEDULE.{i}" for i in range(10)), "SCHEDULE.info"],
        }
        return web.json_response({
            "code": 0,
            "data": None if hint_mode == "missing" else {"SCHEDULE.0": schedule},
        })

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **{**OPTIONS, "account_type": account_type}, session=session,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type=account_type, country="eu",
                ),
            )
            if hint_mode in {"discover", "failed"}:
                client._ensure_device()._protocol.cloud._host = "hub.example.invalid"
            try:
                result = await client.async_get_batch_schedules(
                    include_raw=True,
                    map_index_hint=0 if hint_mode == "explicit" else None,
                    discover_map_index=hint_mode not in {"skip", "missing"},
                )
                expected_hint = {"explicit": 0, "discover": 2}.get(hint_mode)
                if hint_mode == "missing":
                    assert result["schedules"] == []
                    assert result["available"] is False
                    assert result["errors"] == [{
                        "stage": "schedule",
                        "error": "Batch device data returned no schedule payload.",
                    }]
                else:
                    assert result["schedules"][0]["idx"] == expected_hint
                    assert result["active_schedule_version"] == 19383
                assert len(seen) == (2 if hint_mode in {"discover", "failed"} else 1)
                assert not client._cloud_read_tasks
                if hint_mode in {"explicit", "skip", "missing"}:
                    assert client._device is None
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


def login_response(strings, token="access-secret"):
    return {
        strings[18]: token,
        strings[19]: "refresh-secret",
        strings[20]: 3600,
        strings[22]: "tenant-value",
    }


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("kind", ["features", "otc", "firmware"])
@pytest.mark.parametrize("rejected", [False, True])
def test_public_device_metadata_reads_preserve_endpoint_contracts(
    monkeypatch, account_type, kind, rejected,
):
    strings = cloud_strings(account_type)
    paths = {
        "features": "/dreame-user-iot/iotuserbind/queryDevicePermit",
        "otc": "/dreame-user-iot/iotstatus/devOTCInfo",
        "firmware": "/dreame-user-iot/iotuserbind/checkDeviceVersion",
    }
    payload = {"curVersion": "1.0", "newVersion": "1.1", "hasNewFirmware": True}
    response = {"code": 17, "msg": "unavailable"} if rejected else {
        "code": 0, "data": payload,
    }
    language = None if rejected else "pl"
    requests = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        requests.append(request.path)
        assert request.path == paths[kind]
        assert request.method == "POST"
        assert await request.json() == (
            {"did": "42", "lang": "pl"} if language else {"did": "42"}
        )
        return web.json_response(response)

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **{**OPTIONS, "account_type": account_type}, session=session,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type=account_type, country="eu",
                ),
            )
            try:
                if kind == "features":
                    result = await client.async_get_cloud_user_features(
                        language=language,
                    )
                elif kind == "otc":
                    result = await client.async_get_cloud_device_otc_info(
                        language=language,
                    )
                else:
                    result = await client.async_get_cloud_firmware_check(
                        language=language, include_raw=True,
                    )
                if kind != "firmware":
                    assert result == (None if rejected else payload)
                else:
                    assert result["source"] == "cloud_check_device_version"
                    assert result["available"] is not rejected
                    assert result["raw"] == (response if rejected else payload)
                    if rejected:
                        assert result["errors"][0]["code"] == 17
                        assert result["errors"][0]["error"] == "cloud_error"
                    else:
                        assert result["current_version"] == "1.0"
                        assert result["latest_version"] == "1.1"
                        assert result["update_available"] is True
                assert requests == [paths[kind]]
                assert client._device is None
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("mode", ["normal", "reauth", "disconnect", "rejected"])
def test_public_plugin_metadata_uses_get_with_owned_retries(
    monkeypatch, account_type, mode,
):
    strings = cloud_strings(account_type)
    logins = []
    queries = []

    async def handler(request):
        if request.path == strings[17]:
            assert request.method == "POST"
            logins.append(request.path)
            return web.json_response(login_response(strings, f"token-{len(logins)}"))
        assert request.method == "GET"
        assert request.path == "/dreame-product/upgrades/appplugin"
        assert await request.read() == b""
        query = dict(request.query)
        assert query == {
            "model": "dreame.mower.g2408", "appVer": "123456", "os": "2",
        }
        queries.append(query)
        if len(queries) == 1:
            if mode == "reauth":
                return web.Response(status=401, text="expired")
            if mode == "disconnect":
                request.transport.close()
                return web.Response()
        return web.json_response({
            "code": 5 if mode == "rejected" else 0,
            "data": {"version": 123, "url": "https://example.invalid/plugin"},
        })

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **{**OPTIONS, "account_type": account_type}, session=session,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type=account_type, country="eu",
                ),
            )
            try:
                result = await client.async_get_app_plugin_version(
                    app_version_code=123456, os=2,
                )
                assert result == (None if mode == "rejected" else {
                    "version": 123, "url": "https://example.invalid/plugin",
                })
                assert len(queries) == (2 if mode in {"reauth", "disconnect"} else 1)
                assert len(logins) == (2 if mode == "reauth" else 1)
                assert client._device is None
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


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


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("oversized", "size limit"),
        ("gzip_oversized", "size limit"),
        ("malformed", "not valid JSON"),
        ("wrong_shape", "not an object"),
        ("redirect", "not valid JSON"),
    ],
)
def test_bad_responses_fail_closed_without_closing_session(
    monkeypatch, mode, message, caplog
):
    calls = []

    async def handler(request):
        calls.append(request.path)
        if mode == "oversized":
            return web.Response(body=b"x" * (MAX_CLOUD_RESPONSE_BYTES + 1))
        if mode == "gzip_oversized":
            return web.Response(
                body=gzip.compress(b"x" * (MAX_CLOUD_RESPONSE_BYTES + 1)),
                headers={"Content-Encoding": "gzip"},
            )
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
            with pytest.raises(DreameLawnMowerConnectionError, match=message):
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


@pytest.mark.parametrize("status", [400, 401, 403])
@pytest.mark.parametrize(
    "body",
    [b'{"error_description":"private-password"}', b"", b"private-password", b"[]"],
)
def test_auth_rejection_does_not_expose_server_secrets(
    monkeypatch, caplog, status, body
):
    async def handler(request):
        return web.Response(body=body, status=status)

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            with pytest.raises(
                DreameLawnMowerAuthError, match=f"HTTP {status}"
            ) as error:
                await DreameCloudSession(session, **OPTIONS).async_login()
            assert "private-password" not in str(error.value)
            assert not session.closed

    asyncio.run(scenario())
    assert "private-password" not in caplog.text


@pytest.mark.parametrize("body", [b'{"code":401}', b"", b"Unauthorized", b"[]"])
@pytest.mark.parametrize("raise_status", [False, True])
def test_inventory_reauthentication_uses_new_token_and_tenant(
    monkeypatch, body, raise_status
):
    strings = cloud_strings("dreame")
    login_payloads = []
    inventory_headers = []

    async def handler(request):
        if request.path == strings[17]:
            login_payloads.append(await request.text())
            response = login_response(strings, token=f"token-{len(login_payloads)}")
            response[strings[22]] = f"tenant-{len(login_payloads)}"
            return web.json_response(response)
        inventory_headers.append(
            (request.headers[strings[46]], request.headers[strings[50]])
        )
        if len(inventory_headers) == 1:
            return web.Response(body=body, status=401)
        return web.json_response({"code": 0, "data": [{"did": "42"}]})

    async def scenario():
        async with (
            server(monkeypatch, handler),
            ClientSession(raise_for_status=raise_status) as session,
        ):
            result = await DreameCloudSession(session, **OPTIONS).async_get_devices()
            assert result == [{"did": "42"}]
            assert not session.closed

    asyncio.run(scenario())
    assert inventory_headers == [("token-1", "tenant-1"), ("token-2", "tenant-2")]
    assert login_payloads == [
        cloud_login_data(strings, OPTIONS["username"], OPTIONS["password"], None),
        cloud_login_data(strings, "", "", "refresh-secret"),
    ]


@pytest.mark.parametrize("oversized", [False, True])
def test_borrowed_session_cannot_disable_decoded_response_contract(
    monkeypatch, oversized
):
    strings = cloud_strings("dreame")

    async def handler(request):
        body = (
            b"x" * (MAX_CLOUD_RESPONSE_BYTES + 1)
            if oversized else json.dumps(login_response(strings)).encode()
        )
        return web.Response(
            body=gzip.compress(body), headers={"Content-Encoding": "gzip"}
        )

    async def scenario():
        async with (
            server(monkeypatch, handler),
            ClientSession(auto_decompress=False) as session,
        ):
            cloud = DreameCloudSession(session, **OPTIONS)
            if oversized:
                with pytest.raises(DreameLawnMowerConnectionError, match="size limit"):
                    await cloud.async_login()
            else:
                await cloud.async_login()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("reject", [False, True])
def test_standalone_discovery_closes_only_its_owned_session(monkeypatch, reject):
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import client

    strings = cloud_strings("dreame")
    sessions = []

    def create_session(**kwargs):
        session = ClientSession(**kwargs)
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


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
def test_borrowed_default_auth_is_overridden_without_mutating_session(
    monkeypatch, account_type
):
    strings = cloud_strings(account_type)
    expected = cloud_headers(strings, "eu", None)["Authorization"]
    seen = []

    async def handler(request):
        seen.append(request.headers.get("Authorization") == expected)
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response({"code": 0, "data": []})

    async def scenario():
        default_auth = BasicAuth("unrelated-user", "unrelated-password")
        async with (
            server(monkeypatch, handler),
            ClientSession(auth=default_auth) as session,
        ):
            assert await DreameLawnMowerClient.async_discover_devices(
                **{**OPTIONS, "account_type": account_type}, session=session
            ) == []
            assert session.auth == default_auth
            assert not session.closed

    asyncio.run(scenario())
    assert seen == [True, True]


@pytest.mark.parametrize(
    ("session_options", "message"),
    [
        ({"base_url": "http://127.0.0.1:1"}, "without base_url"),
        ({"headers": {"Authorization": "Bearer unrelated-token"}},
         "without a default Authorization header"),
    ],
)
def test_incompatible_borrowed_session_is_rejected_before_http(
    monkeypatch, session_options, message
):
    calls = []
    strings = cloud_strings("dreame")

    async def handler(request):
        calls.append(request.path)
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response({"code": 0, "data": []})

    async def scenario():
        async with (
            server(monkeypatch, handler),
            ClientSession(**session_options) as session,
        ):
            with pytest.raises(ValueError, match=message):
                await DreameLawnMowerClient.async_discover_devices(
                    **OPTIONS, session=session
                )
            assert not session.closed
            assert calls == []

    asyncio.run(scenario())


def test_owned_session_uses_environment_proxy_and_vendor_auth(monkeypatch, tmp_path):
    strings = cloud_strings("dreame")
    expected = cloud_headers(strings, "eu", None)["Authorization"]
    seen = []
    netrc = tmp_path / "test.netrc"
    netrc.write_text(
        "machine 127.0.0.2 login unrelated-user password unrelated-password\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NETRC", str(netrc))
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "")

    async def handler(request):
        seen.append((request.host, request.headers.get("Authorization") == expected))
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response({"code": 0, "data": []})

    async def scenario():
        async with server(monkeypatch, handler) as proxy_url:
            for name in ("HTTP_PROXY", "http_proxy"):
                monkeypatch.setenv(name, proxy_url)
            monkeypatch.setattr(
                DreameCloudSession, "_base_url",
                property(lambda _: "http://127.0.0.2:1"),
            )
            assert await DreameLawnMowerClient.async_discover_devices(**OPTIONS) == []

    asyncio.run(scenario())
    assert seen == [("127.0.0.2", True), ("127.0.0.2", True)]


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("wrapped", [True, False])
def test_device_page_preserves_filters_and_reuses_auth(
    monkeypatch, account_type, wrapped,
):
    strings = cloud_strings(account_type)
    calls = []
    page = {"records": [{"did": "42"}], "current": 2, "pages": 3}

    async def handler(request):
        calls.append(request.path)
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        assert request.path == "/dreame-user-iot/iotuserbind/device/listV2"
        assert await request.json() == {
            "current": 2, "size": 5, "lang": "pl", "master": False,
            "sharedStatus": 0,
        }
        assert request.headers[strings[46]] == "access-secret"
        return web.json_response(
            {"code": 0, "data": {"page": page} if wrapped else page}
        )

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(
                session, **{**OPTIONS, "account_type": account_type}
            )
            for _ in range(2):
                assert await cloud.async_get_device_list_page(
                    current=2, size=5, language="pl", master=False, shared_status=0,
                ) == page
            assert not session.closed

    asyncio.run(scenario())
    assert calls.count(strings[17]) == 1
    assert len(calls) == 3


@pytest.mark.parametrize("payload", [[], {"page": []}, {"page": None}])
def test_device_page_rejects_malformed_page(monkeypatch, payload):
    strings = cloud_strings("dreame")

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response({"code": 0, "data": payload})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **OPTIONS)
            with pytest.raises(DreameLawnMowerConnectionError, match="page is invalid"):
                await cloud.async_get_device_list_page()

    asyncio.run(scenario())


def test_cancelled_page_releases_response_and_allows_next_read(monkeypatch):
    strings = cloud_strings("dreame")

    async def scenario():
        started = asyncio.Event()
        finish = asyncio.Event()
        page_calls = 0

        async def handler(request):
            nonlocal page_calls
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            page_calls += 1
            if page_calls == 1:
                response = web.StreamResponse()
                await response.prepare(request)
                started.set()
                await finish.wait()
                return response
            return web.json_response({"code": 0, "data": {"records": []}})

        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **OPTIONS)
            first = asyncio.create_task(cloud.async_get_device_list_page())
            await started.wait()
            first.cancel()
            try:
                with pytest.raises(asyncio.CancelledError):
                    await first
                assert await cloud.async_get_device_list_page(timeout=1) == {
                    "records": []
                }
                assert not session.closed
            finally:
                finish.set()

    asyncio.run(scenario())


@pytest.mark.parametrize("borrowed", [True, False])
def test_client_page_reads_own_only_standalone_session(monkeypatch, borrowed):
    strings = cloud_strings("dreame")
    calls = []

    async def handler(request):
        calls.append(request.path)
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        assert await request.json() == {"current": 3, "size": 7, "lang": "en"}
        return web.json_response({"code": 0, "data": {"page": {"records": []}}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as shared:
            client = DreameLawnMowerClient(
                **OPTIONS,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type="dreame", country="eu",
                ),
                session=shared if borrowed else None,
            )
            try:
                for _ in range(2):
                    assert await client.async_get_cloud_device_list_page(
                        current=3, size=7,
                    ) == {"records": []}
                used_session = client._http_session
                if borrowed:
                    assert used_session is shared
                else:
                    assert used_session is not shared
            finally:
                await client.async_close()
            assert used_session.closed is (not borrowed)
            assert not shared.closed
            with pytest.raises(DreameLawnMowerConnectionError, match="closing"):
                await client.async_get_cloud_device_list_page()
            await client.async_close()

    asyncio.run(scenario())
    assert calls.count(strings[17]) == 1
    assert len(calls) == 3


@pytest.mark.parametrize("borrowed", [True, False])
@pytest.mark.parametrize("caller_continues", [True, False])
@pytest.mark.parametrize("read_kind", ["info", "page", "properties", "rpc"])
def test_client_close_cancels_active_native_read(
    monkeypatch, borrowed, caller_continues, read_kind,
):
    strings = cloud_strings("dreame")

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        caller_release = asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            response = web.StreamResponse()
            await response.prepare(request)
            started.set()
            await release.wait()
            return response

        async with server(monkeypatch, handler), ClientSession() as shared:
            client = DreameLawnMowerClient(
                **OPTIONS,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type="dreame", country="eu",
                ),
                session=shared if borrowed else None,
            )
            async def read_then_continue():
                try:
                    if read_kind == "info":
                        await client.async_get_cloud_device_info()
                    elif read_kind == "properties":
                        await client.async_get_cloud_properties("1.1")
                    elif read_kind == "rpc":
                        device = await asyncio.to_thread(client._ensure_device)
                        await client._async_read_device_properties(
                            device, [],
                            deadline=asyncio.get_running_loop().time() + 2,
                        )
                    else:
                        await client.async_get_cloud_device_list_page()
                except asyncio.CancelledError:
                    if not caller_continues:
                        raise
                    await caller_release.wait()

            task = asyncio.create_task(read_then_continue())
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                used_session = client._http_session
                await asyncio.wait_for(client.async_close(), timeout=1)
                if caller_continues:
                    assert not task.done()
                else:
                    assert task.cancelled()
                assert used_session.closed is (not borrowed)
                assert not shared.closed
            finally:
                release.set()
                caller_release.set()
                await asyncio.gather(task, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("borrowed", [True, False])
def test_cancelled_close_still_disconnects_legacy_device(monkeypatch, borrowed):
    async def scenario():
        started = asyncio.Event()
        cleanup_started = asyncio.Event()
        cleanup_release = asyncio.Event()
        device_events = []

        async def stalled_read(self, **kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleanup_started.set()
                await cleanup_release.wait()

        class Device:
            def listen(self, callback):
                assert callback is None
                device_events.append("detached")

            def disconnect(self):
                device_events.append("disconnected")

        monkeypatch.setattr(
            DreameCloudSession, "async_get_device_list_page", stalled_read,
        )
        async with ClientSession() as shared:
            client = DreameLawnMowerClient(
                **OPTIONS,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type="dreame", country="eu",
                ),
                session=shared if borrowed else None,
            )
            client._device = Device()
            read = asyncio.create_task(client.async_get_cloud_device_list_page())
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
                used_session = client._http_session
                closing = asyncio.create_task(client.async_close())
                await asyncio.wait_for(cleanup_started.wait(), timeout=1)
                closing.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await closing
                assert device_events == ["detached", "disconnected"]
                assert used_session.closed is (not borrowed)
                assert not shared.closed
            finally:
                cleanup_release.set()
                await asyncio.gather(read, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [400, 403, 500])
@pytest.mark.parametrize("paged", [False, True])
def test_inventory_http_failure_stays_a_connection_error(monkeypatch, status, paged):
    strings = cloud_strings("dreame")
    calls = []

    async def handler(request):
        calls.append(request.path)
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.Response(status=status, text="private-response")

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **OPTIONS)
            read = (
                cloud.async_get_device_list_page if paged else cloud.async_get_devices
            )
            with pytest.raises(
                DreameLawnMowerConnectionError, match=f"HTTP {status}"
            ) as error:
                await read()
            assert "private-response" not in str(error.value)
            assert not session.closed

    asyncio.run(scenario())
    assert len(calls) == 2


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("language", [None, "pl"])
def test_device_info_preserves_identity_language_and_borrowed_session(
    monkeypatch, account_type, language,
):
    strings = cloud_strings(account_type)
    calls = []
    info = {"did": "42", "online": True}

    async def handler(request):
        calls.append(request.path)
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        assert request.path == "/dreame-user-iot/iotuserbind/device/info"
        expected = {"did": "42"}
        if language:
            expected["lang"] = language
        assert await request.json() == expected
        return web.json_response({"code": 0, "data": info})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(
                session, **{**OPTIONS, "account_type": account_type}
            )
            for _ in range(2):
                result = await cloud.async_get_device_info("42", language=language)
                assert result == info
            assert not session.closed

    asyncio.run(scenario())
    assert calls.count(strings[17]) == 1
    assert len(calls) == 3


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("borrowed", [True, False])
def test_public_device_info_uses_native_http_and_updates_mqtt(
    monkeypatch, account_type, borrowed,
):
    import requests

    strings = cloud_strings(account_type)
    info = {
        "did": "42", strings[8]: "owner", strings[35]: "dreame.mower.g2408",
        strings[9]: "mqtt.example.invalid",
        strings[10]: json.dumps({strings[11]: "stream-key"}),
    }
    calls = []

    def reject_sync_http(*args, **kwargs):
        pytest.fail("Public native read attempted synchronous HTTP")

    monkeypatch.setattr(requests.Session, "request", reject_sync_http)

    async def handler(request):
        calls.append(request.path)
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        assert await request.json() == {"did": "42", "lang": "pl"}
        return web.json_response({"code": 0, "data": info})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as shared:
            client = DreameLawnMowerClient(
                **{**OPTIONS, "account_type": account_type},
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type=account_type, country="eu",
                ),
                session=shared if borrowed else None,
            )
            try:
                for _ in range(2):
                    result = await client.async_get_cloud_device_info(language="pl")
                    assert result == info
                cloud = client._device._protocol.cloud
                assert (cloud._uid, cloud._did, cloud._model, cloud._host) == (
                    "owner", "42", "dreame.mower.g2408", "mqtt.example.invalid",
                )
                assert cloud._stream_key == "stream-key"
                assert not cloud._logged_in
                used_session = client._http_session
            finally:
                await client.async_close()
            assert used_session.closed is (not borrowed)
            assert not shared.closed
            with pytest.raises(DreameLawnMowerConnectionError, match="closing"):
                await client.async_get_cloud_device_info()

    asyncio.run(scenario())
    assert calls.count(strings[17]) == 1
    assert len(calls) == 3


@pytest.mark.parametrize("entrypoint", ["direct", "firmware"])
@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_cancelled_device_info_drains_before_return(monkeypatch, entrypoint, stop):
    from threading import Event

    strings = cloud_strings("dreame")
    info = {
        "did": "late-device", strings[8]: "owner",
        strings[35]: "dreame.mower.g2408", strings[9]: "mqtt.example.invalid",
        strings[10]: "",
    }

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response({"code": 0, "data": info})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **OPTIONS,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type="dreame", country="eu",
                ),
                session=session,
            )
            device = await asyncio.to_thread(client._ensure_device)
            cloud = device._protocol.cloud
            entered = Event()
            finished = Event()
            apply_info = client._sync_apply_cloud_device_info

            def apply_and_signal(*args):
                entered.set()
                try:
                    apply_info(*args)
                finally:
                    finished.set()

            monkeypatch.setattr(
                client, "_sync_apply_cloud_device_info", apply_and_signal,
            )
            lock = cloud._operation_lock()
            lock.acquire()
            task = asyncio.create_task(
                client.async_get_cloud_device_info() if entrypoint == "direct"
                else client.async_get_firmware_update_support()
            )
            close = None
            released = False
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                if stop == "cancel":
                    task.cancel()
                else:
                    close = asyncio.create_task(client.async_close())
                await asyncio.sleep(0.02)
                assert not task.done()
                assert client._cloud_read_tasks
                if close is not None:
                    assert not close.done()
                lock.release()
                released = True
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert finished.is_set()
            finally:
                if not released:
                    lock.release()
                await asyncio.gather(task, return_exceptions=True)
                if close is not None:
                    await asyncio.gather(close, return_exceptions=True)
                await client.async_close()
            assert cloud._did == "42"
            assert cloud._host is None
            assert not session.closed

    asyncio.run(scenario())


def test_device_info_deadline_includes_executor_queue(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    strings = cloud_strings("dreame")
    release_worker = Event()

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response({"code": 0, "data": {"did": "42"}})

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        occupied = loop.run_in_executor(None, release_worker.wait)
        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **OPTIONS,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type="dreame", country="eu",
                ),
                session=session,
            )
            try:
                with pytest.raises(
                    DreameLawnMowerConnectionError, match="timed out",
                ):
                    await asyncio.wait_for(client.async_get_cloud_device_info(), 22)
                assert not occupied.done()
                assert client._device is None
            finally:
                release_worker.set()
                await occupied
                await client.async_close()
            assert client._device is None
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("keys,expected", [
    ("1.1,2.2", "1.1,2.2"), ([" 1.1 ", "", "2.2"], "1.1,2.2"), ([], ""),
])
def test_public_cloud_properties_preserves_wire_and_raw_values(
    monkeypatch, account_type, keys, expected,
):
    strings = cloud_strings(account_type)
    values = [{"key": "1.1", "value": [1, 2], "time": 42}]
    calls = []

    async def handler(request):
        calls.append(request.path)
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        assert request.path == "/dreame-user-iot/iotstatus/props"
        assert await request.json() == {"did": "42", "keys": expected}
        return web.json_response({"code": 0, "data": values})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **{**OPTIONS, "account_type": account_type},
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type=account_type, country="eu",
                ),
                session=session,
            )
            try:
                for _ in range(2):
                    assert await client.async_get_cloud_properties(keys) == values
                assert client._device is None
            finally:
                await client.async_close()
            assert not session.closed
            with pytest.raises(DreameLawnMowerConnectionError, match="closing"):
                await client.async_get_cloud_properties(keys)

    asyncio.run(scenario())
    assert calls.count(strings[17]) == 1
    assert len(calls) == 3


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("response,expected", [
    ({"code": 0, "data": {"result": [{"code": 0, "value": 7}]}},
     [{"code": 0, "value": 7}]),
    ({"code": 80001, "data": {"result": [{"value": "stale"}]}}, None),
    ({"code": 0, "success": True, "data": ""}, None),
    ({"code": 0, "data": {}}, None),
])
def test_native_device_read_preserves_rpc_envelope_and_absent_results(
    monkeypatch, account_type, response, expected,
):
    strings = cloud_strings(account_type)
    properties = [{"did": "1", "siid": 2, "piid": 1}]

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        assert request.path == f"/{strings[37]}-hub/{strings[27]}/{strings[38]}"
        assert await request.json() == {
            "did": "42", "id": 19,
            "data": {
                "did": "42", "id": 19,
                "method": "get_properties", "params": properties,
            },
        }
        return web.json_response(response)

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(
                session, **{**OPTIONS, "account_type": account_type},
            )
            assert await cloud.async_read_device_properties(
                "42", "hub.example.invalid", 19, properties,
            ) == expected
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("read_kind", ["properties", "app_action"])
def test_native_device_read_rejects_failed_rpc_even_with_result(monkeypatch, read_kind):
    strings = cloud_strings("dreame")

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response({"code": 500, "data": {"result": [{"value": 7}]}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **OPTIONS)
            with pytest.raises(DreameLawnMowerConnectionError, match="rejected"):
                if read_kind == "properties":
                    await cloud.async_read_device_properties("42", None, 1, [])
                else:
                    await cloud.async_read_app_action(
                        "42", None, 1, {"m": "g", "t": "MAPL"},
                    )

    asyncio.run(scenario())


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("result,expected", [
    ({"out": [{"r": 0, "d": [1, 2]}]}, {"r": 0, "d": [1, 2]}),
    ({"out": []}, None),
    ({"r": 5}, {"r": 5}),
    (None, None),
])
def test_native_app_read_preserves_wire_and_unwraps_result(
    monkeypatch, account_type, result, expected,
):
    strings = cloud_strings(account_type)
    action = {"m": "g", "t": "SCHDT", "d": {"t": 0}}

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        assert request.path == f"/{strings[37]}-hub/{strings[27]}/{strings[38]}"
        assert await request.json() == {
            "did": "42", "id": 19,
            "data": {
                "did": "42", "id": 19, "method": "action",
                "params": {"did": "42", "siid": 2, "aiid": 50, "in": [action]},
            },
        }
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(
                session, **{**OPTIONS, "account_type": account_type},
            )
            assert await cloud.async_read_app_action(
                "42", "hub.example.invalid", 19, action,
            ) == expected
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("action", [{}, {"m": "s", "t": "SCHDT"}, {"m": "a"}])
def test_native_app_read_rejects_mutation_before_http(monkeypatch, action):
    async def unexpected_request(*args, **kwargs):
        pytest.fail("Mutation reached retryable HTTP transport")

    monkeypatch.setattr(DreameCloudSession, "_async_read_response", unexpected_request)

    async def scenario():
        async with ClientSession() as session:
            cloud = DreameCloudSession(session, **OPTIONS)
            with pytest.raises(ValueError, match="requires"):
                await cloud.async_read_app_action("42", None, 1, action)
            assert cloud._token is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "result_code", [0, 80001, 5, "missing_did", "bad_did", "callback"],
)
def test_public_refresh_uses_native_rpc_and_applies_real_device_state(
    monkeypatch, result_code,
):
    from unittest.mock import AsyncMock

    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        device_types,
    )
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
        DreameMowerDevice,
    )


    strings = cloud_strings("dreame")
    battery = device_types.DreameMowerProperty.BATTERY_LEVEL
    requests = []
    monkeypatch.setattr(
        DreameMowerDevice, "cloud_connected", property(lambda _: True),
    )
    monkeypatch.setattr(
        DreameMowerDevice, "device_connected", property(lambda _: False),
    )

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        payload = await request.json()
        requests.append(payload)
        row = {"did": str(battery.value), "code": 0, "value": 55}
        if result_code == "missing_did":
            del row["did"]
        elif result_code == "bad_did":
            row["did"] = "invalid"
        return web.json_response({
            "code": result_code if isinstance(result_code, int) else 0,
            "data": {"result": [row]},
        })

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **OPTIONS,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type="dreame", country="eu",
                ), session=session,
            )
            device = client._ensure_device()
            device._ready = True
            device.data = {battery.value: 20}
            device._last_settings_request = 10**20
            client.async_get_status_blob = AsyncMock(return_value=None)
            client._async_get_cached_cloud_device_info = AsyncMock(return_value=None)
            client._snapshot_from_device = lambda mower: mower.data[battery.value]
            if result_code == "callback":
                def fail_callback(_previous):
                    raise ValueError("callback rejected state")

                device._property_update_callback[battery.value] = [fail_callback]
            try:
                if result_code not in (0, 80001):
                    with pytest.raises(DreameLawnMowerConnectionError):
                        await client.async_refresh()
                    if result_code != "callback":
                        assert device.data[battery.value] == 20
                else:
                    assert await client.async_refresh() == (
                        55 if result_code == 0 else 20
                    )
                assert len(requests) == 1
                assert requests[0]["data"]["method"] == "get_properties"
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_refresh_keeps_state_callback_owned_through_shutdown(monkeypatch, stop):
    from threading import Event, get_ident
    from unittest.mock import AsyncMock

    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        device as device_module,
    )
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        device_types,
    )

    strings = cloud_strings("dreame")
    battery = device_types.DreameMowerProperty.BATTERY_LEVEL
    monkeypatch.setattr(
        device_module.DreameMowerDevice,
        "cloud_connected", property(lambda _: True),
    )
    monkeypatch.setattr(
        device_module.DreameMowerDevice,
        "device_connected", property(lambda _: False),
    )

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response({"code": 0, "data": {"result": [{
            "did": str(battery.value), "code": 0, "value": 55,
        }]}})

    async def scenario():
        loop = asyncio.get_running_loop()
        loop_thread = get_ident()
        started = asyncio.Event()
        release = Event()
        callback_finished = Event()
        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **OPTIONS,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type="dreame", country="eu",
                ), session=session,
            )
            device = client._ensure_device()
            device._ready = True
            device.data = {battery.value: 20}
            device._last_settings_request = 10**20
            client.async_get_status_blob = AsyncMock(return_value=None)
            client._async_get_cached_cloud_device_info = AsyncMock(return_value=None)
            client._snapshot_from_device = lambda mower: mower.data[battery.value]

            def callback(_previous):
                assert get_ident() != loop_thread
                lock = device._protocol.cloud._operation_lock()
                assert lock.acquire(blocking=False)
                lock.release()
                loop.call_soon_threadsafe(started.set)
                assert release.wait(3)
                callback_finished.set()

            device._property_update_callback[battery.value] = [callback]
            refresh = asyncio.create_task(client.async_refresh())
            close = None
            try:
                await asyncio.wait_for(started.wait(), 2)
                if stop == "close":
                    close = asyncio.create_task(client.async_close())
                else:
                    refresh.cancel()
                await asyncio.sleep(0.02)
                assert not refresh.done()
                assert client._cloud_read_tasks
                if close is not None:
                    assert not close.done()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(refresh, 2)
                if close is not None:
                    await asyncio.wait_for(close, 2)
                assert callback_finished.is_set()
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                await asyncio.gather(refresh, return_exceptions=True)
                if close is not None:
                    await asyncio.gather(close, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", [None, "missing", "duplicate", "rejected", "offline"],
)
def test_authoritative_native_read_rejects_incomplete_evidence(monkeypatch, failure):
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        device as device_module,
    )
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        device_property_read,
    )

    strings = cloud_strings("dreame")
    required = device_property_read.TASK_DECISION_PROPERTIES
    requests = []
    monkeypatch.setattr(
        device_module.DreameMowerDevice,
        "cloud_connected", property(lambda _: True),
    )
    monkeypatch.setattr(
        device_module.DreameMowerDevice,
        "device_connected", property(lambda _: True),
    )

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        requests.append(await request.json())
        rows = [{"did": str(prop.value), "code": 0, "value": 0}
                for prop in required]
        if failure == "missing":
            rows.pop()
        elif failure == "duplicate":
            rows.append(dict(rows[-1]))
        elif failure == "rejected":
            rows[-1]["code"] = -1
        return web.json_response({
            "code": 80001 if failure == "offline" else 0,
            "data": {"result": rows},
        })

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **OPTIONS,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type="dreame", country="eu",
                ), session=session,
            )
            device = client._ensure_device()
            device._ready = True
            device.data = {prop.value: 0 for prop in required}
            device._last_settings_request = 10**20
            previous = {"legacy_task_status": 6, "received_at": 0}
            device._fresh_task_state = dict(previous)
            client._snapshot_from_device = (
                lambda mower, **kwargs: dict(mower._fresh_task_state)
            )
            try:
                if failure:
                    with pytest.raises(DreameLawnMowerConnectionError):
                        await client.async_refresh_authoritative_snapshot()
                    assert device._fresh_task_state == previous
                else:
                    evidence = await client.async_refresh_authoritative_snapshot()
                    assert evidence["legacy_task_status"] == 0
                    assert evidence["received_at"] > 0
                assert len(requests) == 1
                requested = requests[0]["data"]["params"]
                assert {str(prop.value) for prop in required} <= {
                    row["did"] for row in requested
                }
                assert {"did": "100001", "siid": 1, "piid": 1} in requested
            finally:
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("blocked", ["gate", "busy", "expired"])
def test_authoritative_read_fails_closed_before_network_when_blocked(blocked):
    async def scenario():
        async with ClientSession() as session:
            client = DreameLawnMowerClient(
                **OPTIONS,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type="dreame", country="eu",
                ), session=session,
            )
            if blocked == "gate":
                await client._refresh_lock.acquire()
            elif blocked == "busy":
                client._ensure_device()._update_running = True
            deadline = asyncio.get_running_loop().time() + (
                -1 if blocked == "expired" else 0.05
            )
            try:
                with pytest.raises(DreameLawnMowerConnectionError):
                    await asyncio.wait_for(
                        client.async_refresh_authoritative_snapshot(deadline=deadline),
                        1,
                    )
                if blocked != "busy":
                    assert client._device is None
                assert not client._cloud_read_tasks
            finally:
                if blocked == "gate":
                    client._refresh_lock.release()
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_authoritative_snapshot_worker_remains_owned_until_finished(stop):
    from threading import Event
    from unittest.mock import AsyncMock

    async def scenario():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = Event()
        async with ClientSession() as session:
            client = DreameLawnMowerClient(
                **OPTIONS,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type="dreame", country="eu",
                ), session=session,
            )
            device = client._ensure_device()
            client._async_update_device = AsyncMock(return_value=device)

            def snapshot(_device, **_kwargs):
                loop.call_soon_threadsafe(started.set)
                assert release.wait(3)
                return object()

            client._snapshot_from_device = snapshot
            refresh = asyncio.create_task(client.async_refresh_authoritative_snapshot())
            close = None
            try:
                await asyncio.wait_for(started.wait(), 1)
                if stop == "close":
                    close = asyncio.create_task(client.async_close())
                else:
                    refresh.cancel()
                await asyncio.sleep(0.02)
                assert not refresh.done()
                assert client._cloud_read_tasks
                if close is not None:
                    assert not close.done()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(refresh, 1)
                if close is not None:
                    await asyncio.wait_for(close, 1)
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                await asyncio.gather(refresh, return_exceptions=True)
                if close is not None:
                    await asyncio.gather(close, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
def test_native_login_retains_mqtt_identity_atomically(monkeypatch, account_type):
    strings = cloud_strings(account_type)
    payload = login_response(strings)
    payload.update({"uid": 123, strings[21]: "eu"})

    async def handler(_request):
        return web.json_response(payload)

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(
                session, **{**OPTIONS, "account_type": account_type},
            )
            await cloud.async_login()
            assert cloud._user_id == "123"
            assert cloud._region == "eu"
            previous = (cloud._token, cloud._refresh_token, cloud._expires_at,
                        cloud._user_id, cloud._region)
            payload.update({strings[18]: "replacement-access", "uid": []})
            with pytest.raises(DreameLawnMowerAuthError):
                await cloud.async_login()
            assert (cloud._token, cloud._refresh_token, cloud._expires_at,
                    cloud._user_id, cloud._region) == previous

    asyncio.run(scenario())


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("kind", ["ota", "preferences"])
@pytest.mark.parametrize("missing", [False, True])
def test_public_batch_metadata_reads_use_native_http(
    monkeypatch, account_type, kind, missing,
):
    strings = cloud_strings(account_type)
    seen = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        seen.append(request.path)
        assert request.path == "/" + "/".join(strings[i] for i in (23, 26, 44))
        keys = ([*(f"OTA_INFO.{i}" for i in range(4)), "OTA_INFO.info",
                 "prop.s_auto_upgrade"] if kind == "ota" else
                [*(f"SETTINGS.{i}" for i in range(10)), "SETTINGS.info"])
        assert await request.json() == {"did": "42", strings[35]: keys}
        data = ({"OTA_INFO.0": "[2,35]", "prop.s_auto_upgrade": "1"}
                if kind == "ota" else {"SETTINGS.0": "[]"})
        return web.json_response({"code": 0, "data": None if missing else data})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **{**OPTIONS, "account_type": account_type}, session=session,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type=account_type, country="eu",
                ),
            )
            try:
                result = (await client.async_get_batch_ota_info(include_raw=True)
                          if kind == "ota" else
                          await client.async_get_batch_mowing_preferences(
                              include_raw=True,
                          ))
                if missing:
                    assert result["available"] is False
                    label = "OTA" if kind == "ota" else "settings"
                    assert result["errors"] == [{
                        "stage": "ota" if kind == "ota" else "settings",
                        "error": f"Batch device data returned no {label} payload.",
                    }]
                elif kind == "ota":
                    assert result["ota_state"] == 2
                    assert result["ota_progress"] == 35
                    assert result["auto_upgrade_enabled"] is True
                    assert result["raw_text"] == "[2,35]"
                else:
                    assert result["maps"] == []
                    assert result["errors"] == []
                assert len(seen) == 1
                assert client._device is None
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("mode", ["normal", "partial", "batch_only"])
def test_firmware_support_uses_native_metadata(monkeypatch, account_type, mode):
    strings = cloud_strings(account_type)
    seen = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        seen.append(request.path)
        if request.path.endswith("device/info"):
            if mode == "partial":
                return web.Response(status=403)
            return web.json_response({"code": 0, "data": None})
        if request.path.endswith("device/listV2"):
            return web.json_response({"code": 0, "data": None})
        if request.path.endswith("checkDeviceVersion"):
            return web.json_response({"code": 0, "data": {
                "curVersion": "1.0", "newVersion": "1.1", "hasNewFirmware": True,
            }})
        assert request.path == "/" + "/".join(strings[i] for i in (23, 26, 44))
        return web.json_response({"code": 0, "data": {
            "OTA_INFO.0": "[2,35]", "prop.s_auto_upgrade": "1",
        }})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **{**OPTIONS, "account_type": account_type}, session=session,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type=account_type, country="eu",
                ),
            )
            try:
                result = await client.async_get_firmware_update_support(
                    include_cloud=mode != "batch_only",
                )
                assert result.ota_state == 2
                assert result.ota_progress == 35
                assert result.auto_upgrade_enabled is True
                assert result.debug_catalog_available is None
                assert len(seen) == (1 if mode == "batch_only" else 4)
                if mode != "batch_only":
                    assert result.latest_version == "1.1"
                    assert result.cloud_check_update_available is True
                if mode == "partial":
                    assert "cloud_device_info:" in result.cloud_error
                else:
                    assert result.cloud_error is None
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "close"])
@pytest.mark.parametrize("stage", ["snapshot", "debug_catalog"])
def test_firmware_support_drains_started_workers(monkeypatch, stop, stage):
    from threading import Event
    from unittest.mock import AsyncMock

    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        client_firmware_reads,
    )

    async def scenario():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = Event()
        finished = Event()
        async with ClientSession() as session:
            client = DreameLawnMowerClient(
                **OPTIONS, session=session,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type="dreame", country="eu",
                ),
            )
            client.async_get_batch_ota_info = AsyncMock(return_value={})

            def blocked(*args, **kwargs):
                loop.call_soon_threadsafe(started.set)
                assert release.wait(3)
                finished.set()
                return {}

            if stage == "snapshot":
                monkeypatch.setattr(
                    client_firmware_reads,
                    "firmware_update_support_from_device", blocked,
                )
            else:
                client._sync_get_debug_ota_catalog = blocked
            task = asyncio.create_task(client.async_get_firmware_update_support(
                include_cloud=False, include_debug_ota_catalog=stage == "debug_catalog",
            ))
            close = None
            try:
                await asyncio.wait_for(started.wait(), 2)
                if stop == "close":
                    close = asyncio.create_task(client.async_close())
                else:
                    task.cancel()
                await asyncio.sleep(0.02)
                assert not task.done()
                assert client._cloud_read_tasks
                if close is not None:
                    assert not close.done()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
                if close is not None:
                    await asyncio.wait_for(close, 2)
                assert finished.is_set()
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                if close is not None:
                    await asyncio.gather(close, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())
