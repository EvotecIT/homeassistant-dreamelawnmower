"""Anonymous byte ranges preserve the seekable installer download contract."""

import asyncio
import time

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import public_download

from .test_async_cloud_session import DreameLawnMowerConnectionError, server


@pytest.mark.parametrize(
    "mode", ["valid", "status", "offset", "total", "short", "encoding", "large"]
)
def test_exact_range_survives_redirect_and_rejects_invalid_response(monkeypatch, mode):
    async def handler(request):
        assert request.headers["Range"] == "bytes=2-4"
        assert request.headers["Accept-Encoding"] == "identity"
        assert "Authorization" not in request.headers
        assert "Cookie" not in request.headers
        if request.path == "/redirect":
            raise web.HTTPFound("/archive")
        content_range = {
            "offset": "bytes 1-3/10",
            "total": "bytes 2-4/11",
        }.get(mode, "bytes 2-4/10")
        headers = {"Content-Range": content_range}
        if mode == "encoding":
            headers["Content-Encoding"] = "gzip"
        return web.Response(
            status=200 if mode == "status" else 206,
            body=b"ab" if mode == "short" else b"abcd" if mode == "large" else b"cde",
            headers=headers,
        )

    async def scenario():
        async with (
            server(monkeypatch, handler) as url,
            ClientSession(
                headers={"Authorization": "private", "Cookie": "private=1"},
            ) as session,
        ):

            async def download():
                return await public_download.async_download_public_response(
                    session,
                    url + "/redirect",
                    deadline=time.monotonic() + 3,
                    byte_range=(2, 4, 10),
                    max_bytes=3,
                )

            if mode == "valid":
                result = await download()
                assert result.content == b"cde"
                assert result.url == url + "/archive"
            else:
                with pytest.raises(DreameLawnMowerConnectionError):
                    await download()
            assert not session.closed
            assert not session.connector.closed

    asyncio.run(scenario())


def test_cancelled_range_releases_request_and_preserves_borrowed_session(monkeypatch):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            started.set()
            await release.wait()
            return web.Response(
                body=b"a", status=206, headers={"Content-Range": "bytes 0-0/10"}
            )

        async with server(monkeypatch, handler) as url, ClientSession() as session:
            task = asyncio.create_task(
                public_download.async_download_public_response(
                    session,
                    url,
                    deadline=time.monotonic() + 3,
                    byte_range=(0, 0, 10),
                    max_bytes=1,
                )
            )
            try:
                await asyncio.wait_for(started.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not session.closed
                assert not session.connector.closed
            finally:
                release.set()

    asyncio.run(scenario())
