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
@pytest.mark.parametrize("read_kind", ["info", "page", "properties"])
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


def test_cancelled_device_info_cannot_apply_after_device_lock_releases(monkeypatch):
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
            task = asyncio.create_task(client.async_get_cloud_device_info())
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                lock.release()
                await asyncio.gather(task, return_exceptions=True)
                assert await asyncio.to_thread(finished.wait, 2)
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


def test_native_device_read_rejects_failed_rpc_even_with_result(monkeypatch):
    strings = cloud_strings("dreame")

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response({"code": 500, "data": {"result": [{"value": 7}]}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **OPTIONS)
            with pytest.raises(DreameLawnMowerConnectionError, match="rejected"):
                await cloud.async_read_device_properties("42", None, 1, [])

    asyncio.run(scenario())
