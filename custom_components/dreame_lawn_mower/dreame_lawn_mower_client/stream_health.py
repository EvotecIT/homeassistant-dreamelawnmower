"""HTTP stream health checks for Dreame live video runtimes."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from http.client import HTTPResponse
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(slots=True, frozen=True)
class DreameLawnMowerStreamUrlProbeResult:
    """Redacted health result for a local stream URL."""

    available: bool
    status_code: int | None = None
    content_type: str | None = None
    bytes_read: int = 0
    flv_header_present: bool = False
    attempts: int = 1
    elapsed_seconds: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-safe stream health details without exposing the URL."""
        return asdict(self)


def probe_stream_url(
    stream_url: str | None,
    *,
    timeout: float = 3.0,
    read_bytes: int = 16,
    attempts: int = 1,
    retry_interval: float = 0.25,
) -> DreameLawnMowerStreamUrlProbeResult:
    """Open a stream URL briefly and check whether it looks like HTTP-FLV."""
    if not stream_url:
        return DreameLawnMowerStreamUrlProbeResult(
            available=False,
            error="stream_url_missing",
        )

    started = time.monotonic()
    max_attempts = max(int(attempts), 1)
    last_result: DreameLawnMowerStreamUrlProbeResult | None = None
    for attempt in range(1, max_attempts + 1):
        last_result = _probe_stream_url_once(
            stream_url,
            timeout=timeout,
            read_bytes=read_bytes,
            attempts=attempt,
            elapsed_seconds=time.monotonic() - started,
        )
        if last_result.flv_header_present:
            return _with_elapsed(last_result, started)
        if attempt < max_attempts:
            time.sleep(max(retry_interval, 0.0))
    if last_result is None:
        return DreameLawnMowerStreamUrlProbeResult(
            available=False,
            error="stream_probe_not_attempted",
        )
    return _with_elapsed(last_result, started)


def _probe_stream_url_once(
    stream_url: str,
    *,
    timeout: float,
    read_bytes: int,
    attempts: int,
    elapsed_seconds: float,
) -> DreameLawnMowerStreamUrlProbeResult:
    request = Request(stream_url, headers={"User-Agent": "dreame-lawn-mower-probe"})
    try:
        with urlopen(request, timeout=max(timeout, 0.1)) as response:
            return _probe_response(
                response,
                read_bytes=max(read_bytes, 0),
                attempts=attempts,
                elapsed_seconds=elapsed_seconds,
            )
    except HTTPError as err:
        return DreameLawnMowerStreamUrlProbeResult(
            available=False,
            status_code=err.code,
            content_type=err.headers.get("Content-Type"),
            attempts=attempts,
            elapsed_seconds=elapsed_seconds,
            error=f"http_error_{err.code}",
        )
    except URLError as err:
        return DreameLawnMowerStreamUrlProbeResult(
            available=False,
            attempts=attempts,
            elapsed_seconds=elapsed_seconds,
            error=f"url_error_{type(err.reason).__name__}",
        )
    except TimeoutError:
        return DreameLawnMowerStreamUrlProbeResult(
            available=False,
            attempts=attempts,
            elapsed_seconds=elapsed_seconds,
            error="timeout",
        )
    except OSError as err:
        return DreameLawnMowerStreamUrlProbeResult(
            available=False,
            attempts=attempts,
            elapsed_seconds=elapsed_seconds,
            error=type(err).__name__,
        )


def _probe_response(
    response: HTTPResponse,
    *,
    read_bytes: int,
    attempts: int,
    elapsed_seconds: float,
) -> DreameLawnMowerStreamUrlProbeResult:
    status_code = getattr(response, "status", None) or response.getcode()
    content_type = response.headers.get("Content-Type")
    chunk = response.read(read_bytes) if read_bytes else b""
    return DreameLawnMowerStreamUrlProbeResult(
        available=200 <= int(status_code) < 300,
        status_code=int(status_code),
        content_type=content_type,
        bytes_read=len(chunk),
        flv_header_present=chunk.startswith(b"FLV"),
        attempts=attempts,
        elapsed_seconds=elapsed_seconds,
    )


def _with_elapsed(
    result: DreameLawnMowerStreamUrlProbeResult,
    started: float,
) -> DreameLawnMowerStreamUrlProbeResult:
    return DreameLawnMowerStreamUrlProbeResult(
        available=result.available,
        status_code=result.status_code,
        content_type=result.content_type,
        bytes_read=result.bytes_read,
        flv_header_present=result.flv_header_present,
        attempts=result.attempts,
        elapsed_seconds=round(time.monotonic() - started, 3),
        error=result.error,
    )
