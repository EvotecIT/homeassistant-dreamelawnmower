"""Local stream HTTP and native route lifetime contracts."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from aiohttp import BasicAuth, ClientSession, web

from custom_components.dreame_lawn_mower import video_stream_helpers
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    stream_health_async,
)

from .test_async_cloud_session import server


def test_fragmented_flv_header_does_not_wait_for_media(monkeypatch):
    async def run():
        release = asyncio.Event()
        transports = []

        async def handler(request):
            assert "Authorization" not in request.headers
            assert "Cookie" not in request.headers
            transports.append(request.transport)
            response = web.StreamResponse(headers={"Content-Type": "video/x-flv"})
            await response.prepare(request)
            await response.write(b"F")
            await asyncio.sleep(0)
            await response.write(b"LV")
            await release.wait()
            return response

        async def callback():
            await asyncio.sleep(0.15)
            assert not transports[0].is_closing()

        async with (
            server(monkeypatch, handler) as url,
            ClientSession(
                headers={"Authorization": "private", "Cookie": "private=1"}
            ) as session,
        ):
            try:
                result = await asyncio.wait_for(
                    stream_health_async.async_probe_stream_url(
                        url, session=session, timeout=0.1, on_stream_open=callback
                    ),
                    2,
                )
                assert result.flv_header_present
                assert result.bytes_read == 3
                assert result.content_type == "video/x-flv"
                assert result.first_bytes_hex == b"FLV".hex()
                assert not session.closed
            finally:
                release.set()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["http", "short", "timeout"])
def test_probe_retries_failure_then_accepts_flv(monkeypatch, failure):
    async def run():
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                if failure == "http":
                    return web.Response(status=503, text="private details")
                if failure == "short":
                    return web.Response(body=b"FL")
                await asyncio.sleep(0.2)
            return web.Response(body=b"FLV")

        async with server(monkeypatch, handler) as url, ClientSession() as session:
            result = await stream_health_async.async_probe_stream_url(
                url, session=session, timeout=0.1, attempts=2, retry_interval=0
            )
            assert result.flv_header_present
            assert result.attempts == 2
            assert calls == 2

    asyncio.run(run())


def test_cancelled_route_probe_drains_native_callback_before_closing_stream(
    monkeypatch,
):
    async def run():
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()
        release_worker = threading.Event()
        release_server = asyncio.Event()
        transports = []
        finished = []

        async def handler(request):
            transports.append(request.transport)
            response = web.StreamResponse()
            await response.prepare(request)
            await response.write(b"FLV")
            await release_server.wait()
            return response

        def refresh(stream, *, timeout):
            assert timeout == video_stream_helpers._STREAM_HEALTH_TIMEOUT
            loop.call_soon_threadsafe(entered.set)
            assert release_worker.wait(3)
            finished.append(True)

        async with server(monkeypatch, handler) as url, ClientSession() as session:
            monkeypatch.setattr(
                video_stream_helpers, "async_get_clientsession", lambda hass: session
            )
            hass = SimpleNamespace(
                async_add_executor_job=lambda fn, *args: loop.run_in_executor(
                    None, fn, *args
                )
            )
            runtime = SimpleNamespace(refresh_stream_link_mode=refresh)
            task = asyncio.create_task(
                video_stream_helpers.async_probe_stream_health_and_route(
                    hass, runtime, SimpleNamespace(stream_url=url)
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert not transports[0].is_closing()
                release_worker.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert finished == [True]
                assert not session.closed
            finally:
                release_worker.set()
                release_server.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_borrowed_session_defaults_do_not_change_anonymous_probe(monkeypatch):
    async def run():
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            assert "Authorization" not in request.headers
            assert "Cookie" not in request.headers
            assert request.headers["Accept-Encoding"] == "identity"
            if calls == 1:
                return web.Response(status=503)
            return web.Response(body=b"FLV")

        async with server(monkeypatch, handler) as url, ClientSession(
            base_url=f"{url}/",
            auth=BasicAuth("private", "secret"),
            headers={"Authorization": "private", "Cookie": "private=1"},
            raise_for_status=True,
            auto_decompress=True,
        ) as session:
            result = await stream_health_async.async_probe_stream_url(
                url, session=session, attempts=2, retry_interval=0
            )
            assert result.flv_header_present
            assert result.attempts == 2
            assert not session.closed
            assert session.connector is not None and not session.connector.closed

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["headers", "signature"])
def test_http_cancellation_closes_connection_without_route_inspection(
    monkeypatch, phase,
):
    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()
        transports = []
        callbacks = []

        async def handler(request):
            transports.append(request.transport)
            response = web.StreamResponse()
            if phase == "signature":
                await response.prepare(request)
                await response.write(b"F")
            entered.set()
            await release.wait()
            return response

        async def callback():
            callbacks.append(True)

        async with server(monkeypatch, handler) as url, ClientSession() as session:
            task = asyncio.create_task(stream_health_async.async_probe_stream_url(
                url, session=session, on_stream_open=callback,
            ))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                async with asyncio.timeout(2):
                    while not transports[0].is_closing():
                        await asyncio.sleep(0)
                assert callbacks == []
                assert not session.closed
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
