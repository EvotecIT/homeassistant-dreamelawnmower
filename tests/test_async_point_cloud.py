"""Native point-cloud transport against a local TLS vendor and object server."""

from __future__ import annotations

import asyncio
import ipaddress
import ssl
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import ClientSession, TCPConnector, web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.cloud_session import (
    DreameCloudSession,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.point_cloud import (
    DreameLawnMowerPointCloudError,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import cloud_strings, login_response
from .test_point_cloud import _binary_pcd


@asynccontextmanager
async def tls_server(monkeypatch, tmp_path, handler):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "server.pem", tmp_path / "server-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    client_context = ssl.create_default_context(cafile=str(cert_path))
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_context)
    await site.start()
    url = f"https://127.0.0.1:{runner.addresses[0][1]}"
    monkeypatch.setattr(DreameCloudSession, "_base_url", property(lambda _: url))
    try:
        yield url, client_context
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize(
    "route", ["stored", "announcement", "legacy", "lost_reply", "rejected"]
)
def test_public_point_cloud_uses_native_tls_without_replaying_generation(
    monkeypatch, tmp_path, account_type, route
):
    strings = cloud_strings(account_type)
    content = _binary_pcd((1.0, 2.0, 3.0, 1))
    seen = []
    generated = False
    base_url = ""

    async def handler(request):
        nonlocal generated
        if request.path == "/object.pcd":
            seen.append("download")
            for key in ("Authorization", "Cookie", strings[46]):
                assert key not in request.headers
            return web.Response(
                body=content,
                content_type="application/pcd",
                headers={"ETag": "object-v1"},
            )
        if request.path == strings[17]:
            seen.append("login")
            return web.json_response(login_response(strings))
        if request.path == "/" + "/".join(strings[i] for i in (23, 39, 55)):
            seen.append("sign")
            return web.json_response({"code": 0, "data": base_url + "/object.pcd"})
        if request.path == "/" + "/".join(strings[i] for i in (23, 25, 41)):
            seen.append("property")
            rows = (
                []
                if route in {"legacy", "lost_reply"}
                else [
                    {
                        "key": "99.20",
                        "value": "generated.pcd" if generated else "",
                        "updateDate": int(time.time() * 1000) + 100,
                    }
                ]
            )
            return web.json_response({"code": 0, "data": rows})
        action = (await request.json())["data"]["params"]["in"][0]
        if action["m"] == "a":
            seen.append("generate")
            assert not generated, "Generation was replayed"
            generated = True
            if route == "rejected":
                return web.json_response({"code": 80001})
            if route == "lost_reply":
                request.transport.close()
                return web.Response()
            result = {"r": 0}
        else:
            assert action == {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}}
            seen.append("objects")
            result = {"r": 0, "d": {"name": ["generated.pcd" if generated else None]}}
        return web.json_response({"code": 0, "data": {"result": {"out": [result]}}})

    async def scenario():
        nonlocal base_url
        async with tls_server(monkeypatch, tmp_path, handler) as (url, context):
            base_url = url
            async with ClientSession(connector=TCPConnector(ssl=context)) as session:
                client = client_for(session, account_type)
                if route == "stored":
                    client._latest_app_map_inventory_identity = "inventory"
                    client._latest_app_map_object_inventory_identity = "inventory"
                    client._latest_app_map_object_names = ("stored.pcd",)

                def no_sync(*args, **kwargs):
                    pytest.fail("Native point-cloud path used a synchronous transport")

                monkeypatch.setattr(client, "_sync_get_cloud_protocol", no_sync)
                monkeypatch.setattr(
                    client, "_sync_download_app_map_point_cloud", no_sync
                )
                monkeypatch.setattr(client._device._protocol.cloud, "request", no_sync)
                try:
                    if route == "rejected":
                        with pytest.raises(DreameLawnMowerPointCloudError) as failure:
                            await client.async_download_app_map_point_cloud(timeout=2)
                        assert (
                            failure.value.code == "point_cloud_mower_request_rejected"
                        )
                        assert failure.value.vendor_error_code == 80001
                        assert seen.count("generate") == 1
                        assert "download" not in seen
                        return
                    result = await client.async_download_app_map_point_cloud(
                        allow_stored=route == "stored",
                        timeout=2,
                        poll_interval=0.01,
                    )
                    assert result.content == content
                    assert result.content_type == "application/pcd"
                    assert result.source == (
                        "stored" if route == "stored" else "generated"
                    )
                    assert seen.count("generate") == (0 if route == "stored" else 1)
                    assert seen.count("login") == 1
                    assert not client._point_cloud_generation_lock.locked()
                    assert not client._cloud_read_tasks
                finally:
                    await client.async_close()
                assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["login", "property", "generate", "download"])
def test_native_point_cloud_cancel_stops_requests(monkeypatch, tmp_path, phase):
    strings = cloud_strings("dreame")
    content = _binary_pcd((1.0, 2.0, 3.0, 1))

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []
        generated = False
        base_url = ""

        async def handler(request):
            nonlocal generated
            if request.path == strings[17]:
                step, payload = "login", login_response(strings)
            elif request.path == "/object.pcd":
                step, payload = "download", None
            elif request.path == "/" + "/".join(strings[i] for i in (23, 39, 55)):
                step, payload = "sign", {"code": 0, "data": base_url + "/object.pcd"}
            elif request.path == "/" + "/".join(strings[i] for i in (23, 25, 41)):
                step, payload = (
                    "property",
                    {
                        "code": 0,
                        "data": [
                            {
                                "key": "99.20",
                                "value": "generated.pcd" if generated else "",
                                "updateDate": int(time.time() * 1000) + 100,
                            }
                        ],
                    },
                )
            else:
                generated = True
                step, payload = (
                    "generate",
                    {"code": 0, "data": {"result": {"out": [{"r": 0}]}}},
                )
            seen.append(step)
            if step == phase:
                entered.set()
                await release.wait()
            return (
                web.Response(body=content)
                if payload is None
                else web.json_response(payload)
            )

        async with tls_server(monkeypatch, tmp_path, handler) as (url, context):
            base_url = url
            async with ClientSession(connector=TCPConnector(ssl=context)) as session:
                client = client_for(session)
                request = asyncio.create_task(
                    client.async_download_app_map_point_cloud(
                        timeout=2, poll_interval=0.01
                    )
                )
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    request.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await request
                    snapshot = list(seen)
                    await asyncio.wait_for(client.async_close(), 2)
                    assert seen == snapshot
                    assert not client._point_cloud_generation_lock.locked()
                    assert not client._cloud_read_tasks
                finally:
                    release.set()
                    await asyncio.gather(request, return_exceptions=True)
                    await client.async_close()
                assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_request", [False, True])
@pytest.mark.parametrize("worker_fails", [False, True])
def test_native_point_cloud_close_drains_parser_and_retains_singleflight(
    monkeypatch,
    tmp_path,
    cancel_request,
    worker_fails,
):
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        client_point_cloud_async,
    )

    strings = cloud_strings("dreame")
    content = _binary_pcd((1, 2, 3, 1))
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    base_url = ""
    parser = client_point_cloud_async.parse_pcd_metadata

    def blocked_parser(*args, **kwargs):
        started.set()
        assert release.wait(3)
        finished.set()
        if worker_fails:
            raise RuntimeError("late parser failure")
        return parser(*args, **kwargs)

    monkeypatch.setattr(client_point_cloud_async, "parse_pcd_metadata", blocked_parser)

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        if request.path == "/object.pcd":
            return web.Response(body=content)
        return web.json_response({"code": 0, "data": base_url + "/object.pcd"})

    async def scenario():
        nonlocal base_url
        async with tls_server(monkeypatch, tmp_path, handler) as (url, context):
            base_url = url
            async with ClientSession(connector=TCPConnector(ssl=context)) as session:
                client = client_for(session)
                client._latest_app_map_inventory_identity = "inventory"
                client._latest_app_map_object_inventory_identity = "inventory"
                client._latest_app_map_object_names = ("stored.pcd",)
                first = asyncio.create_task(
                    client.async_download_app_map_point_cloud(allow_stored=True)
                )
                assert await asyncio.to_thread(started.wait, 2)
                closing = None
                try:
                    if cancel_request:
                        first.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await first
                    with pytest.raises(DreameLawnMowerPointCloudError) as busy:
                        await client.async_download_app_map_point_cloud()
                    assert busy.value.code == "point_cloud_generation_in_progress"
                    closing = asyncio.create_task(client.async_close())
                    await asyncio.sleep(0.02)
                    assert not closing.done()
                finally:
                    release.set()
                    if closing is not None:
                        await closing
                    await asyncio.gather(first, return_exceptions=True)
                    await client.async_close()
                assert finished.is_set()
                assert not client._point_cloud_generation_lock.locked()
                assert not client._cloud_read_tasks
                assert not session.closed

    asyncio.run(scenario())


def test_native_point_cloud_rejects_unchanged_announcement_object(
    monkeypatch, tmp_path
):
    strings = cloud_strings("dreame")
    content = _binary_pcd((1, 2, 3, 1))
    commands = []
    base_url = ""

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        if request.path == "/object.pcd":
            return web.Response(body=content, headers={"ETag": "unchanged"})
        if request.path == "/" + "/".join(strings[i] for i in (23, 39, 55)):
            return web.json_response({"code": 0, "data": base_url + "/object.pcd"})
        if request.path == "/" + "/".join(strings[i] for i in (23, 25, 41)):
            return web.json_response(
                {
                    "code": 0,
                    "data": [{"key": "99.20", "value": "old.pcd", "updateDate": 1000}],
                }
            )
        action = (await request.json())["data"]["params"]["in"][0]
        if action["m"] == "a":
            commands.append(action)
            result = {"r": 0}
        else:
            result = {"r": 0, "d": {"name": ["old.pcd"]}}
        return web.json_response({"code": 0, "data": {"result": {"out": [result]}}})

    async def scenario():
        nonlocal base_url
        async with tls_server(monkeypatch, tmp_path, handler) as (url, context):
            base_url = url
            async with ClientSession(connector=TCPConnector(ssl=context)) as session:
                client = client_for(session)
                try:
                    with pytest.raises(DreameLawnMowerPointCloudError) as failure:
                        await client.async_download_app_map_point_cloud(
                            timeout=0.25, poll_interval=0.01
                        )
                    assert failure.value.code == "point_cloud_not_published"
                    assert failure.value.diagnostic_reason == "unchanged_object"
                    assert failure.value.generation_acknowledged is True
                    assert len(commands) == 1
                finally:
                    await client.async_close()

    asyncio.run(scenario())
