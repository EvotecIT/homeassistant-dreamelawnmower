"""Compressed HTTP responses must be bounded at the decoder boundary."""

from __future__ import annotations

import asyncio
import gzip
import time
import zlib

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    http_response,
    public_download,
)

from .test_async_cloud_session import (
    OPTIONS,
    DreameCloudSession,
    DreameLawnMowerConnectionError,
    server,
)


@pytest.mark.parametrize("transport", ["public", "cloud"])
def test_compressed_input_is_bounded_before_allocation(monkeypatch, transport):
    payload = gzip.compress(b"x" * (8 * 1024 * 1024))
    real_decoder = zlib.decompressobj
    limits = []
    sizes = []

    class Decoder:
        def __init__(self, window):
            self.inner = real_decoder(window)

        def decompress(self, data, max_length=0):
            limits.append(max_length)
            assert 0 < max_length <= 1024 * 1024 + 1
            result = self.inner.decompress(data, max_length)
            sizes.append(len(result))
            return result

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(http_response.zlib, "decompressobj", Decoder)

    async def handler(request):
        assert request.headers["Accept-Encoding"] == "gzip, deflate"
        return web.Response(body=payload, headers={"Content-Encoding": "gzip"})

    async def scenario():
        async with server(monkeypatch, handler) as url, ClientSession() as session:
            with pytest.raises(DreameLawnMowerConnectionError, match="size limit"):
                if transport == "public":
                    await public_download.async_download_public_file(
                        session, url, deadline=time.monotonic() + 5,
                    )
                else:
                    cloud = DreameCloudSession(session, **OPTIONS)
                    await cloud.async_login()
            assert not session.closed

    asyncio.run(scenario())
    assert limits
    assert max(sizes) <= 1024 * 1024 + 1


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "raw_deflate", "truncated"])
def test_bounded_response_decodes_supported_streams_and_checks_completion(
    monkeypatch, encoding,
):
    data = b'{"valid": true}'
    if encoding in ("gzip", "truncated"):
        body = gzip.compress(data)
        if encoding == "truncated":
            body = body[:-4]
        header = "gzip"
    else:
        compressor = zlib.compressobj(wbits=-15 if encoding == "raw_deflate" else 15)
        body = compressor.compress(data) + compressor.flush()
        header = "deflate"

    async def handler(request):
        return web.Response(body=body, headers={"Content-Encoding": header})

    async def scenario():
        async with server(monkeypatch, handler) as url, ClientSession() as session:
            operation = public_download.async_download_public_file(
                session, url, deadline=time.monotonic() + 5,
            )
            if encoding == "truncated":
                with pytest.raises(DreameLawnMowerConnectionError, match="Incomplete"):
                    await operation
            else:
                assert await operation == data

    asyncio.run(scenario())
