"""Read selected files from a public Android build artifact over HTTP ranges."""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Mapping, Sequence
from typing import Protocol

from .video_runtime import DreameLawnMowerVideoRuntimeError

_CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)")


class _HttpResponse(Protocol):
    status_code: int
    content: bytes
    headers: Mapping[str, str]
    url: str


class _HttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout: float,
    ) -> _HttpResponse: ...


class _HttpRangeReader(io.RawIOBase):
    """Expose one immutable HTTP resource as a seekable binary stream."""

    def __init__(
        self,
        client: _HttpClient,
        url: str,
        size: int,
        *,
        timeout: float,
    ) -> None:
        super().__init__()
        self._client = client
        self._url = url
        self._size = size
        self._timeout = timeout
        self._position = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self._position + offset
        elif whence == io.SEEK_END:
            position = self._size + offset
        else:
            raise ValueError(f"Unsupported seek mode: {whence}")
        if position < 0:
            raise ValueError("Cannot seek before the start of an HTTP resource.")
        self._position = min(position, self._size)
        return self._position

    def read(self, size: int = -1) -> bytes:
        if self._position >= self._size or size == 0:
            return b""
        end = (
            self._size - 1
            if size < 0
            else min(self._size - 1, self._position + size - 1)
        )
        start = self._position
        response = self._client.get(
            self._url,
            headers={
                "Accept-Encoding": "identity",
                "Range": f"bytes={start}-{end}",
            },
            timeout=self._timeout,
        )
        content = bytes(response.content)
        _require_range_response(
            response,
            expected_start=start,
            expected_end=end,
            expected_size=self._size,
            content_length=len(content),
        )
        self._position += len(content)
        return content


def read_android_build_zip_entries(
    artifact_url: str,
    entry_names: Sequence[str],
    *,
    expected_size: int,
    http_client: _HttpClient,
    timeout: float,
) -> dict[str, bytes]:
    """Return selected entries without downloading the complete build archive."""
    probe = http_client.get(
        artifact_url,
        headers={
            "Accept-Encoding": "identity",
            "Range": "bytes=0-0",
        },
        timeout=timeout,
    )
    _require_range_response(
        probe,
        expected_start=0,
        expected_end=0,
        expected_size=expected_size,
        content_length=len(probe.content),
    )
    reader = _HttpRangeReader(
        http_client,
        probe.url,
        expected_size,
        timeout=timeout,
    )
    try:
        with zipfile.ZipFile(reader) as archive:
            return {name: archive.read(name) for name in entry_names}
    except (KeyError, OSError, zipfile.BadZipFile) as err:
        raise DreameLawnMowerVideoRuntimeError(
            "Android build runtime artifact was malformed."
        ) from err


def _require_range_response(
    response: _HttpResponse,
    *,
    expected_start: int,
    expected_end: int,
    expected_size: int,
    content_length: int,
) -> None:
    if response.status_code != 206:
        raise DreameLawnMowerVideoRuntimeError(
            "Android build runtime did not support HTTP range downloads."
        )
    match = _CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
    if match is None:
        raise DreameLawnMowerVideoRuntimeError(
            "Android build runtime returned an invalid Content-Range."
        )
    start, end, total = (int(value) for value in match.groups())
    expected_length = expected_end - expected_start + 1
    if (
        start != expected_start
        or end != expected_end
        or total != expected_size
        or content_length != expected_length
    ):
        raise DreameLawnMowerVideoRuntimeError(
            "Android build runtime returned an unexpected byte range."
        )
