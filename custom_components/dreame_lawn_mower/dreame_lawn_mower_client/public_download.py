"""Bounded anonymous downloads sharing the caller's connection pool."""

from __future__ import annotations

import asyncio
import math
import time
from urllib.parse import urljoin, urlsplit
from urllib.request import proxy_bypass

from aiohttp import BasicAuth, ClientError, ClientSession, ClientTimeout, DummyCookieJar
from aiohttp.helpers import proxies_from_env

from .exceptions import DreameLawnMowerConnectionError

MAX_PUBLIC_JSON_BYTES = 1024 * 1024


class _RetryableDownloadError(Exception):
    """An HTTP response that permits a bounded read-only retry."""


def _validate_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        _ = parsed.port  # Validate the port before opening a request.
    except ValueError:
        raise DreameLawnMowerConnectionError(
            "Invalid anonymous download URL",
        ) from None
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None):
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
    session: ClientSession, url: str, *, deadline: float,
    max_bytes: int = MAX_PUBLIC_JSON_BYTES, attempts: int = 1, timeout: float = 20,
) -> bytes:
    """Never copy origin credentials, cookies, default headers or middleware.

    The temporary session owns only request metadata. Its connector belongs to
    the injected session and remains open on completion, cancellation or failure.
    Environment proxies are resolved separately so origin netrc auth is disabled.
    """
    if (not math.isfinite(deadline) or not math.isfinite(timeout) or timeout <= 0
            or max_bytes <= 0 or attempts not in (1, 2)):
        raise ValueError("Invalid download deadline, size or retry policy")
    _validate_url(url)
    if session.closed or session.connector is None:
        raise DreameLawnMowerConnectionError("Download session is closed")
    try:
        async with asyncio.timeout(max(0, deadline - time.monotonic())):
            async with ClientSession(
                connector=session.connector, connector_owner=False,
                cookie_jar=DummyCookieJar(), trust_env=False,
            ) as anonymous:
                for attempt in range(attempts):
                    try:
                        current = url
                        for redirect in range(6):
                            _validate_url(current)
                            proxy, proxy_auth = (
                                await asyncio.to_thread(_environment_proxy, current)
                                if session.trust_env else (None, None)
                            )
                            async with anonymous.get(
                                current, allow_redirects=False,
                                timeout=ClientTimeout(total=timeout),
                                proxy=proxy, proxy_auth=proxy_auth,
                            ) as response:
                                if response.status in (301, 302, 303, 307, 308):
                                    location = response.headers.get("Location")
                                    if not location or redirect == 5:
                                        raise DreameLawnMowerConnectionError(
                                            "Invalid download redirect",
                                        )
                                    target = urljoin(current, location)
                                    _validate_url(target)
                                    if (urlsplit(current).scheme == "https"
                                            and urlsplit(target).scheme != "https"):
                                        raise DreameLawnMowerConnectionError(
                                            "Download redirect downgraded HTTPS",
                                        )
                                    current = target
                                    continue
                                if (response.status in (408, 429)
                                        or response.status >= 500):
                                    raise _RetryableDownloadError
                                if response.status != 200:
                                    raise DreameLawnMowerConnectionError(
                                        f"Download returned HTTP {response.status}",
                                    )
                                body = bytearray()
                                async for chunk in response.content.iter_chunked(8192):
                                    if len(body) + len(chunk) > max_bytes:
                                        raise DreameLawnMowerConnectionError(
                                            "Download exceeds the size limit",
                                        )
                                    body.extend(chunk)
                                return bytes(body)
                    except (ClientError, TimeoutError, _RetryableDownloadError):
                        if attempt + 1 == attempts:
                            raise DreameLawnMowerConnectionError(
                                "Public file download failed",
                            ) from None
                raise AssertionError("Download attempts exhausted")
    except TimeoutError:
        raise DreameLawnMowerConnectionError("Public file download timed out") from None
