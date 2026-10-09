"""Bound HTTP bodies before compressed input can allocate unbounded output."""

from __future__ import annotations

import zlib

from aiohttp import ClientResponse

from .exceptions import DreameLawnMowerConnectionError


class HttpResponseError(DreameLawnMowerConnectionError):
    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


async def async_read_bounded_response(
    response: ClientResponse,
    *,
    max_bytes: int,
) -> bytes:
    """Read identity/gzip/deflate with finite wire and decoded-output budgets.

    Callers must disable aiohttp auto_decompress before the response is created.
    Never flush a decoder: zlib.flush's length is an initial allocation size,
    not an output cap. A complete stream reaches eof through bounded decompress.
    """
    encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
    if encoding not in ("identity", "gzip", "deflate"):
        raise HttpResponseError(
            "Unsupported HTTP content encoding", reason="transport_error"
        )
    body = bytearray()
    wire_bytes = 0
    decoder = None
    try:
        async for chunk in response.content.iter_chunked(8192):
            wire_bytes += len(chunk)
            if wire_bytes > max_bytes * 2:
                raise HttpResponseError(
                    "HTTP response exceeds the size limit", reason="byte_limit"
                )
            if encoding != "identity":
                if decoder is None:
                    # Match the existing raw-deflate compatibility used by aiohttp.
                    window = (
                        31
                        if encoding == "gzip"
                        else (
                            zlib.MAX_WBITS if chunk[0] & 0xF == 8 else -zlib.MAX_WBITS
                        )
                    )
                    decoder = zlib.decompressobj(window)
                chunk = decoder.decompress(chunk, max_bytes - len(body) + 1)
            if len(body) + len(chunk) > max_bytes:
                raise HttpResponseError(
                    "HTTP response exceeds the size limit", reason="byte_limit"
                )
            body.extend(chunk)
        if encoding != "identity" and (
            decoder is None or not decoder.eof or decoder.unused_data
        ):
            raise HttpResponseError(
                "Incomplete or trailing compressed data", reason="truncated_content"
            )
    except zlib.error:
        raise HttpResponseError(
            "Invalid compressed HTTP response", reason="transport_error"
        ) from None
    return bytes(body)
