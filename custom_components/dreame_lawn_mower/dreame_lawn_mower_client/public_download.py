"""Bounded anonymous downloads sharing the caller's connection pool."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from urllib.request import proxy_bypass

from aiohttp import (
    BasicAuth,
    ClientError,
    ClientPayloadError,
    ClientSession,
    ClientTimeout,
    DummyCookieJar,
)
from aiohttp.helpers import proxies_from_env

from .exceptions import DreameLawnMowerConnectionError
from .http_response import async_read_bounded_response

MAX_PUBLIC_JSON_BYTES = 1024 * 1024


@dataclass(frozen=True)
class PublicDownload:
    content: bytes
    content_type: str
    etag: str | None
    last_modified: str | None
    url: str = ""


class PublicDownloadError(DreameLawnMowerConnectionError):
    """A download failure with privacy-safe classification and optional HTTP status."""

    def __init__(self, message: str, *, reason: str, status: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.status = status


class _RetryableDownloadError(PublicDownloadError):
    """An HTTP response that permits a bounded read-only retry."""


def _validate_url(url: str, *, https_only: bool = False) -> None:
    try:
        parsed = urlsplit(url)
        _ = parsed.port  # Validate the port before opening a request.
    except ValueError:
        raise DreameLawnMowerConnectionError(
            "Invalid anonymous download URL",
        ) from None
    if https_only and parsed.scheme != "https":
        raise PublicDownloadError("Download requires HTTPS", reason="https_redirect")
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise DreameLawnMowerConnectionError("Invalid anonymous download URL")


def _environment_proxy(url: str) -> tuple[str | None, BasicAuth | None]:
    parsed = urlsplit(url)
    if proxy_bypass(parsed.hostname or ""):
        return None, None
    info = proxies_from_env().get(parsed.scheme)
    if info is None:
        return None, None
    return str(info.proxy), info.proxy_auth


async def async_download_public_file(
    session: ClientSession,
    url: str,
    *,
    deadline: float,
    max_bytes: int = MAX_PUBLIC_JSON_BYTES,
    attempts: int = 1,
    timeout: float = 20,
) -> bytes:
    """Download an anonymous file using the shared bounded response owner."""
    result = await async_download_public_response(
        session,
        url,
        deadline=deadline,
        max_bytes=max_bytes,
        attempts=attempts,
        timeout=timeout,
    )
    return result.content


async def async_download_public_response(
    session: ClientSession,
    url: str,
    *,
    deadline: float,
    max_bytes: int = MAX_PUBLIC_JSON_BYTES,
    attempts: int = 1,
    timeout: float = 20,
    total_timeout: float | None = None,
    https_only: bool = False,
    byte_range: tuple[int, int, int] | None = None,
) -> PublicDownload:
    """Never copy origin credentials, cookies, default headers or middleware.

    The temporary session owns only request metadata. Its connector belongs to
    the injected session and remains open on completion, cancellation or failure.
    Environment proxies are resolved separately so origin netrc auth is disabled.
    With total_timeout, timeout bounds connection/read stalls instead of the
    request total. The shared deadline still bounds the complete operation.
    """
    if (
        not math.isfinite(deadline)
        or not math.isfinite(timeout)
        or timeout <= 0
        or (
            total_timeout is not None
            and (not math.isfinite(total_timeout) or total_timeout <= 0)
        )
        or max_bytes <= 0
        or attempts not in (1, 2)
    ):
        raise ValueError("Invalid download deadline, size or retry policy")
    headers = {"Accept-Encoding": "gzip, deflate"}
    if byte_range is not None:
        start, end, total = byte_range
        if not 0 <= start <= end < total or end - start + 1 > max_bytes:
            raise ValueError("Invalid download byte range or size budget")
        max_bytes = end - start + 1
        headers = {"Accept-Encoding": "identity", "Range": f"bytes={start}-{end}"}
    _validate_url(url, https_only=https_only)
    if session.closed or session.connector is None:
        raise DreameLawnMowerConnectionError("Download session is closed")
    try:
        async with asyncio.timeout(max(0, deadline - time.monotonic())):
            async with ClientSession(
                connector=session.connector,
                connector_owner=False,
                cookie_jar=DummyCookieJar(),
                trust_env=False,
                auto_decompress=False,
                headers=headers,
            ) as anonymous:
                for attempt in range(attempts):
                    try:
                        current = url
                        for redirect in range(6):
                            _validate_url(current, https_only=https_only)
                            proxy, proxy_auth = (
                                await asyncio.to_thread(_environment_proxy, current)
                                if session.trust_env
                                else (None, None)
                            )
                            async with anonymous.get(
                                current,
                                allow_redirects=False,
                                timeout=(
                                    ClientTimeout(total=timeout)
                                    if total_timeout is None
                                    else ClientTimeout(
                                        total=total_timeout,
                                        sock_connect=timeout,
                                        sock_read=timeout,
                                    )
                                ),
                                proxy=proxy,
                                proxy_auth=proxy_auth,
                            ) as response:
                                if response.status in (301, 302, 303, 307, 308):
                                    location = response.headers.get("Location")
                                    if not location or redirect == 5:
                                        raise DreameLawnMowerConnectionError(
                                            "Invalid download redirect",
                                        )
                                    target = urljoin(current, location)
                                    _validate_url(target, https_only=https_only)
                                    if (
                                        urlsplit(current).scheme == "https"
                                        and urlsplit(target).scheme != "https"
                                    ):
                                        raise DreameLawnMowerConnectionError(
                                            "Download redirect downgraded HTTPS",
                                        )
                                    current = target
                                    continue
                                if (
                                    response.status in (408, 429)
                                    or response.status >= 500
                                ):
                                    raise _RetryableDownloadError(
                                        f"Download returned HTTP {response.status}",
                                        reason="http_error",
                                        status=response.status,
                                    )
                                if response.status != (206 if byte_range else 200):
                                    raise PublicDownloadError(
                                        f"Download returned HTTP {response.status}",
                                        reason="http_error",
                                        status=response.status,
                                    )
                                if byte_range is not None and (
                                    response.headers.get("Content-Range")
                                    != f"bytes {start}-{end}/{total}"
                                    or response.headers.get(
                                        "Content-Encoding", "identity"
                                    )
                                    .strip()
                                    .lower()
                                    != "identity"
                                ):
                                    raise PublicDownloadError(
                                        "Download returned an invalid byte range",
                                        reason="range_mismatch",
                                    )
                                content = await async_read_bounded_response(
                                    response,
                                    max_bytes=max_bytes,
                                )
                                if byte_range is not None and len(content) != max_bytes:
                                    raise PublicDownloadError(
                                        "Download returned an incomplete byte range",
                                        reason="range_mismatch",
                                    )
                                return PublicDownload(
                                    content,
                                    response.content_type,
                                    response.headers.get("ETag"),
                                    response.headers.get("Last-Modified"),
                                    str(response.url),
                                )
                    except (ClientError, TimeoutError, _RetryableDownloadError) as err:
                        if attempt + 1 == attempts:
                            if isinstance(err, _RetryableDownloadError):
                                raise err
                            raise PublicDownloadError(
                                "Public file download failed",
                                reason=(
                                    "download_timeout"
                                    if isinstance(err, TimeoutError)
                                    else "truncated_content"
                                    if isinstance(err, ClientPayloadError)
                                    else "transport_error"
                                ),
                            ) from None
                raise AssertionError("Download attempts exhausted")
    except TimeoutError:
        raise PublicDownloadError(
            "Public file download timed out",
            reason="download_timeout",
        ) from None
