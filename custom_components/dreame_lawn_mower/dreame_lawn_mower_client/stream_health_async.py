"""Async HTTP health checks using the existing FLV result contract."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from aiohttp import ClientError, ClientSession, ClientTimeout, DummyCookieJar

from .public_download import _environment_proxy
from .stream_health import (
    DreameLawnMowerStreamUrlProbeResult,
    _probe_chunk,
    _with_elapsed,
)


async def async_probe_stream_url(
    stream_url: str | None,
    *,
    session: ClientSession,
    timeout: float = 3.0,
    read_bytes: int = 16,
    attempts: int = 1,
    retry_interval: float = 0.25,
    on_stream_open: Callable[[], Awaitable[None]] | None = None,
) -> DreameLawnMowerStreamUrlProbeResult:
    """Read only the FLV signature, retaining the response during the callback.

    The callback owns its cancellation cleanup. HTTP deadlines cover opening and
    reading the stream; they must not close it during native route inspection.
    """
    if not stream_url:
        return DreameLawnMowerStreamUrlProbeResult(
            available=False,
            error_category="missing_url",
            error="stream_url_missing",
        )
    started = time.monotonic()
    if session.closed or session.connector is None:
        return DreameLawnMowerStreamUrlProbeResult(
            available=False,
            error_category="url_error",
            error="session_closed",
        )
    max_attempts = max(int(attempts), 1)
    async with ClientSession(
        connector=session.connector,
        connector_owner=False,
        cookie_jar=DummyCookieJar(),
        timeout=ClientTimeout(total=None),
        auto_decompress=False,
        headers={
            "User-Agent": "dreame-lawn-mower-probe",
            "Accept-Encoding": "identity",
        },
    ) as probe_session:
        for attempt in range(1, max_attempts + 1):
            response = None
            try:
                async with asyncio.timeout(max(timeout, 0.1)):
                    proxy, proxy_auth = (
                        await asyncio.to_thread(_environment_proxy, stream_url)
                        if session.trust_env
                        else (None, None)
                    )
                    response = await probe_session.get(
                        stream_url,
                        proxy=proxy,
                        proxy_auth=proxy_auth,
                    )
                    if response.status >= 400:
                        result = DreameLawnMowerStreamUrlProbeResult(
                            available=False,
                            error_category="http_error",
                            status_code=response.status,
                            content_type=response.headers.get("Content-Type"),
                            attempts=attempt,
                            error=f"http_error_{response.status}",
                        )
                    else:
                        try:
                            chunk = await response.content.readexactly(
                                min(max(read_bytes, 0), 3),
                            )
                        except asyncio.IncompleteReadError as err:
                            chunk = err.partial
                        result = _probe_chunk(
                            response.status,
                            response.headers.get("Content-Type"),
                            chunk,
                            attempts=attempt,
                            elapsed_seconds=0,
                        )
                if result.flv_header_present and on_stream_open is not None:
                    await on_stream_open()
            except TimeoutError:
                result = DreameLawnMowerStreamUrlProbeResult(
                    available=False,
                    error_category="timeout",
                    attempts=attempt,
                    error="timeout",
                )
            except (ClientError, OSError) as err:
                result = DreameLawnMowerStreamUrlProbeResult(
                    available=False,
                    error_category="url_error",
                    attempts=attempt,
                    error=f"url_error_{type(err).__name__}",
                )
            finally:
                if response is not None:
                    response.close()
            if result.flv_header_present or attempt == max_attempts:
                return _with_elapsed(result, started)
            await asyncio.sleep(max(retry_interval, 0))
    raise AssertionError("At least one stream probe attempt is required")
