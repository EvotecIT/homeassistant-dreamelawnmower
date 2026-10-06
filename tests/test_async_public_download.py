"""Anonymous HTTP boundary, catalog decoding and native read lifetime."""

from __future__ import annotations

import asyncio
import gzip
import time
from unittest.mock import AsyncMock

import pytest
from aiohttp import BasicAuth, ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_public_reads,
    public_download,
)

from .test_async_app_preferences import make_client
from .test_async_cloud_session import (
    DreameLawnMowerConnectionError,
    cloud_strings,
    login_response,
    server,
)


@pytest.mark.parametrize("default_auth", [False, True])
def test_download_isolates_credentials_redirects_and_shared_pool(
    monkeypatch, tmp_path, default_auth,
):
    netrc = tmp_path / "netrc"
    netrc.write_text("machine 127.0.0.1 login netrc-user password netrc-password\n")
    monkeypatch.setenv("NETRC", str(netrc))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    seen = []

    async def handler(request):
        seen.append(dict(request.headers))
        for key in ("Authorization", "Cookie", "X-Account-Token"):
            assert key not in request.headers
        if request.path == "/redirect":
            response = web.Response(status=302, headers={"Location": "/payload"})
            response.set_cookie("download-cookie", "private")
            return response
        return web.Response(body=b"public data")

    async def scenario():
        async with server(monkeypatch, handler) as url, ClientSession(
            auth=BasicAuth("borrowed", "private") if default_auth else None,
            headers={"X-Account-Token": "private", "Cookie": "private=1"},
            cookies={"account": "private"}, trust_env=True,
        ) as session:
            connector = session.connector
            for _ in range(2):
                assert await public_download.async_download_public_file(
                    session, url + "/redirect", deadline=time.monotonic() + 5,
                ) == b"public data"
            assert not connector.closed
            assert not session.closed
            assert session.headers["X-Account-Token"] == "private"
            assert not session.cookie_jar.filter_cookies(url).get("download-cookie")

    asyncio.run(scenario())
    assert len(seen) == 4


@pytest.mark.parametrize("mode", [
    "compressed", "deadline", "retry", "invalid_redirect",
])
def test_download_limits_deadlines_retry_and_redirect_credentials(monkeypatch, mode):
    calls = []

    async def scenario():
        release = asyncio.Event()

        async def handler(request):
            calls.append(request.path)
            if mode == "compressed":
                return web.Response(body=gzip.compress(b"x" * 1025),
                                    headers={"Content-Encoding": "gzip"})
            if mode == "deadline":
                await release.wait()
            if mode == "invalid_redirect":
                return web.Response(status=302, headers={
                    "Location": "http://user:private@127.0.0.1/forbidden",
                })
            if mode == "retry" and len(calls) == 1:
                return web.Response(status=503)
            return web.Response(body=b"ok")

        async with server(monkeypatch, handler) as url, ClientSession() as session:
            try:
                operation = public_download.async_download_public_file(
                    session, url, deadline=time.monotonic() + (
                        0.1 if mode == "deadline" else 5
                    ), max_bytes=1024, attempts=2,
                )
                if mode == "retry":
                    assert await operation == b"ok"
                    assert len(calls) == 2
                else:
                    with pytest.raises(DreameLawnMowerConnectionError) as error:
                        await operation
                    assert "private" not in str(error.value)
                    assert len(calls) == 1
                assert not session.closed
                assert not session.connector.closed
            finally:
                release.set()

    asyncio.run(scenario())


def test_public_catalog_preserves_summary_without_vendor_login(monkeypatch):
    calls = []
    payload = {"data": {"BUILD": [{"version": "1.0"}], "FEATURE": [], "PREBUILD": []}}

    async def handler(request):
        calls.append(request.path)
        assert "Authorization" not in request.headers
        return web.json_response(payload)

    async def scenario():
        async with server(monkeypatch, handler) as url, ClientSession() as session:
            monkeypatch.setattr(client_public_reads, "build_debug_ota_catalog_url",
                                lambda model: url + "/" + model)
            client = make_client(session)
            try:
                result = await client.async_get_debug_ota_catalog(
                    current_version="1.0", include_raw=True,
                )
                assert result["model_name"] == "g2408"
                assert result["current_version_present"] is True
                assert result["raw"] == payload
                assert result["warnings"] == [
                    "debug_catalog_unverified", "debug_catalog_not_device_approved",
                    "debug_catalog_has_no_changelog",
                ]
                assert calls == ["/g2408"]
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_firmware_catalog_download_cancels_without_retained_workers(monkeypatch, stop):
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(request):
            entered.set()
            await release.wait()
            return web.json_response({"data": {}})

        async with server(monkeypatch, handler) as url, ClientSession() as session:
            monkeypatch.setattr(client_public_reads, "build_debug_ota_catalog_url",
                                lambda model: url)
            client = make_client(session)
            client.async_get_batch_ota_info = AsyncMock(return_value={})
            client._device.info = type("Info", (), {"firmware_version": "1.0"})()
            task = asyncio.create_task(client.async_get_firmware_update_support(
                include_cloud=False, include_debug_ota_catalog=True,
            ))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if stop == "close":
                    await asyncio.wait_for(client.async_close(), 2)
                else:
                    task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["info", "list", "missing", "malformed", "failure"])
def test_native_key_definition_keeps_discovery_and_result_contract(monkeypatch, mode):
    strings = cloud_strings("dreame")
    downloads = []
    inventory = []

    async def scenario():
        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            definition = {"url": base + "/definition", "ver": 10}
            if request.path.endswith("/device/info"):
                return web.json_response({"code": 0, "data": {
                    "did": "42", strings[8]: "user", strings[35]: "dreame.mower.g2408",
                    strings[9]: "hub.example.invalid", strings[10]: "{}",
                    "keyDefine": {} if mode in ("list", "missing") else definition,
                }})
            if request.path == "/definition":
                downloads.append(request.path)
                assert "Authorization" not in request.headers
                assert "Cookie" not in request.headers
                if mode == "failure":
                    return web.Response(status=404)
                if mode == "malformed":
                    return web.Response(body=b"bad JSON")
                return web.json_response({"keyDefine": {"2.1": "Charging"}})
            inventory.append(request.path)
            return web.json_response({"code": 0, "data": {"page": {"records": [
                {"did": "42", "keyDefine": definition} if mode == "list" else {},
            ]}}})

        async with server(monkeypatch, handler) as base, ClientSession() as session:
            client = make_client(session)
            try:
                result = await client.async_get_cloud_key_definition(language="en")
                assert result["url_present"] is (mode != "missing")
                assert result["fetched"] is (mode in ("info", "list"))
                assert len(inventory) == (1 if mode in ("list", "missing") else 0)
                assert len(downloads) == (0 if mode == "missing" else 1)
                if mode == "missing":
                    assert result["source"] is None
                    assert result["error"] == "key_define_url_missing"
                else:
                    assert result["source"] == (
                        "device_list_v2" if mode == "list" else "device_info"
                    )
                    assert result["ver"] == 10
                    if mode in ("info", "list"):
                        assert result["payload"] == {"keyDefine": {"2.1": "Charging"}}
                    else:
                        assert result["error"] == (
                            "key_definition_parse_failed" if mode == "malformed"
                            else "key_definition_fetch_failed"
                        )
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


def test_download_honors_environment_proxy_without_origin_credentials(monkeypatch):
    seen = []

    async def handler(request):
        seen.append(request.raw_path)
        assert "Authorization" not in request.headers
        assert request.headers["Proxy-Authorization"] == (
            BasicAuth("proxy", "pw").encode()
        )
        return web.Response(body=b"proxied")

    async def scenario():
        async with server(monkeypatch, handler) as proxy:
            monkeypatch.setenv("http_proxy", proxy.replace("http://", "http://proxy:pw@"))
            monkeypatch.setenv("no_proxy", "")
            monkeypatch.setenv("NO_PROXY", "")
            async with ClientSession(trust_env=True) as session:
                assert await public_download.async_download_public_file(
                    session, "http://download.example.invalid/file",
                    deadline=time.monotonic() + 5,
                ) == b"proxied"
                assert not session.connector.closed

    asyncio.run(scenario())
    assert seen == ["http://download.example.invalid/file"]



def test_public_download_response_preserves_file_identity(monkeypatch):
    async def handler(request):
        return web.Response(
            body=b"file bytes", content_type="application/pcd", headers={
            "ETag": "version-2", "Last-Modified": "Tue, 06 Oct 2026 12:00:00 GMT",
        })

    async def scenario():
        async with server(monkeypatch, handler) as url, ClientSession() as session:
            response = await public_download.async_download_public_response(
                session, url, deadline=time.monotonic() + 2,
            )
            assert response.content == b"file bytes"
            assert response.content_type == "application/pcd"
            assert response.etag == "version-2"
            assert response.last_modified == "Tue, 06 Oct 2026 12:00:00 GMT"
            assert not session.closed

    asyncio.run(scenario())


def test_https_only_download_rejects_cleartext_before_dispatch(monkeypatch):
    seen = []

    async def handler(request):
        seen.append(request.path)
        return web.Response(body=b"unexpected")

    async def scenario():
        async with server(monkeypatch, handler) as url, ClientSession() as session:
            with pytest.raises(public_download.PublicDownloadError) as failure:
                await public_download.async_download_public_response(
                    session, url, deadline=time.monotonic() + 2, https_only=True,
                )
            assert failure.value.reason == "https_redirect"
            assert not seen
            assert not session.closed

    asyncio.run(scenario())
