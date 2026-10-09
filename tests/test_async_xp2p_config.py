"""Signed XP2P configuration HTTP contracts over a real local socket."""

from __future__ import annotations

import asyncio
import gzip

import pytest
from aiohttp import BasicAuth, ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import xp2p_config

from .test_async_cloud_session import server
from .test_xp2p_host_runtime import _inputs


@pytest.mark.parametrize("default_auth", [False, True])
def test_async_config_preserves_signature_and_borrowed_session(
    monkeypatch, tmp_path, default_auth,
):
    netrc = tmp_path / "netrc"
    netrc.write_text("machine 127.0.0.1 login test-user password test-password\n")
    monkeypatch.setenv("NETRC", str(netrc))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async def handler(request):
        assert "Authorization" not in request.headers
        assert "Cookie" not in request.headers
        assert "X-Account-Token" not in request.headers
        body = await request.json()
        assert body["Signature"] == xp2p_config.sign_xp2p_app_request(
            body, "app-secret-1"
        )
        assert body["Timestamp"] == 123
        assert body["Nonce"] == 456
        assert body["RequestId"] == "request-1"
        assert "app-secret-1" not in body.values()
        return web.json_response(
            {
                "code": 0,
                "data": {
                    "Config": {"StunHost": "stun.example.test", "StunPort": 20003}
                },
            }
        )

    async def run():
        async with (
            server(monkeypatch, handler) as url,
            ClientSession(
                auth=BasicAuth("borrowed", "private") if default_auth else None,
                headers={"Cookie": "private=1", "X-Account-Token": "private"},
                trust_env=True,
            ) as session,
        ):
            monkeypatch.setattr(xp2p_config, "TENCENT_XP2P_APP_API", url)
            config = await xp2p_config.async_fetch_xp2p_device_config(
                _inputs(),
                session=session,
                timestamp=123,
                nonce=456,
                request_id="request-1",
            )
            assert config.server == "stun.example.test"
            assert config.port == 20003
            assert not session.closed
            assert session.connector is not None and not session.connector.closed
            assert session.headers["X-Account-Token"] == "private"

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", ["redirect", "compressed_limit", "json", "rejected", "timeout"]
)
def test_async_config_failure_keeps_sdk_defaults(monkeypatch, failure):
    paths = []

    async def handler(request):
        paths.append(request.path)
        if failure == "redirect":
            return web.Response(status=307, headers={"Location": "/unexpected"})
        if failure == "compressed_limit":
            return web.Response(
                body=gzip.compress(b"x" * (1024 * 1024 + 1)),
                headers={"Content-Encoding": "gzip"},
            )
        if failure == "json":
            return web.Response(text="private invalid response")
        if failure == "timeout":
            await asyncio.sleep(0.1)
        return web.json_response({"code": 401, "message": "private error"})

    async def run():
        async with server(monkeypatch, handler) as url, ClientSession() as session:
            monkeypatch.setattr(xp2p_config, "TENCENT_XP2P_APP_API", url)
            result = await xp2p_config.async_resolve_xp2p_device_config(
                _inputs(), session=session, timeout=0.02 if failure == "timeout" else 10
            )
            assert result == xp2p_config.DreameLawnMowerXp2pDeviceConfig()
            assert not session.closed
            assert paths == ["/"]

    asyncio.run(run())


def test_async_config_cancellation_is_not_sdk_fallback(monkeypatch):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            entered.set()
            await release.wait()
            return web.json_response({"code": 0, "data": {"Config": {}}})

        async with server(monkeypatch, handler) as url, ClientSession() as session:
            monkeypatch.setattr(xp2p_config, "TENCENT_XP2P_APP_API", url)
            task = asyncio.create_task(
                xp2p_config.async_resolve_xp2p_device_config(_inputs(), session=session)
            )
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not session.closed
                assert session.connector is not None and not session.connector.closed
            finally:
                release.set()

    asyncio.run(run())


def test_async_config_honors_proxy_without_origin_credentials(monkeypatch):
    from aiohttp import BasicAuth

    seen = []

    async def handler(request):
        seen.append(request.raw_path)
        assert "Authorization" not in request.headers
        assert "Cookie" not in request.headers
        assert (
            request.headers["Proxy-Authorization"] == BasicAuth("proxy", "pw").encode()
        )
        body = await request.json()
        assert body["Signature"] == xp2p_config.sign_xp2p_app_request(
            body, "app-secret-1"
        )
        return web.json_response(
            {"code": 0, "data": {"Config": {"StunHost": "proxied.example.test"}}}
        )

    async def run():
        async with server(monkeypatch, handler) as proxy:
            monkeypatch.setenv(
                "http_proxy", proxy.replace("http://", "http://proxy:pw@")
            )
            monkeypatch.setenv("no_proxy", "")
            monkeypatch.setenv("NO_PROXY", "")
            monkeypatch.setattr(
                xp2p_config,
                "TENCENT_XP2P_APP_API",
                "http://config.example.invalid/config",
            )
            async with ClientSession(
                trust_env=True,
                headers={"Authorization": "private", "Cookie": "private=1"},
            ) as session:
                result = await xp2p_config.async_fetch_xp2p_device_config(
                    _inputs(), session=session
                )
                assert result.server == "proxied.example.test"
                assert not session.closed
                assert session.connector is not None and not session.connector.closed

    asyncio.run(run())
    assert seen == ["http://config.example.invalid/config"]
