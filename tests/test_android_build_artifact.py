"""Contracts for selective Android build artifact downloads."""

from __future__ import annotations

import io
import zipfile

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    android_build_artifact,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerVideoRuntimeError,
)


class _Response:
    def __init__(
        self,
        content: bytes,
        *,
        content_range: str,
        url: str,
        status_code: int = 206,
    ) -> None:
        self.content = content
        self.headers = {"Content-Range": content_range}
        self.status_code = status_code
        self.url = url


class _RangeClient:
    def __init__(self, content: bytes, *, status_code: int = 206) -> None:
        self.content = content
        self.status_code = status_code
        self.requests: list[tuple[str, str]] = []

    def get(self, url, *, headers, timeout):
        assert timeout == 10
        byte_range = headers["Range"]
        self.requests.append((url, byte_range))
        start, end = (
            int(value) for value in byte_range.removeprefix("bytes=").split("-")
        )
        return _Response(
            self.content[start : end + 1],
            content_range=f"bytes {start}-{end}/{len(self.content)}",
            url="https://signed.example.test/android-build.zip",
            status_code=self.status_code,
        )


def _build_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SYSTEM/apex/com.android.runtime.apex", b"runtime-apex")
        archive.writestr("SYSTEM/lib64/liblog.so", b"android-log")
        archive.writestr("unrelated-large-image.img", b"ignored" * 1000)
    return buffer.getvalue()


def test_selected_entries_are_read_with_http_ranges() -> None:
    content = _build_zip()
    client = _RangeClient(content)

    entries = android_build_artifact.read_android_build_zip_entries(
        "https://build.example.test/artifact/url",
        (
            "SYSTEM/apex/com.android.runtime.apex",
            "SYSTEM/lib64/liblog.so",
        ),
        expected_size=len(content),
        http_client=client,
        timeout=10,
    )

    assert entries == {
        "SYSTEM/apex/com.android.runtime.apex": b"runtime-apex",
        "SYSTEM/lib64/liblog.so": b"android-log",
    }
    assert client.requests[0] == (
        "https://build.example.test/artifact/url",
        "bytes=0-0",
    )
    assert all(
        url == "https://signed.example.test/android-build.zip"
        for url, _range in client.requests[1:]
    )
    assert sum(
        int(end) - int(start) + 1
        for _url, byte_range in client.requests
        for start, end in [byte_range.removeprefix("bytes=").split("-")]
    ) < len(content)


def test_range_reader_rejects_servers_without_partial_content() -> None:
    content = _build_zip()

    with pytest.raises(
        DreameLawnMowerVideoRuntimeError,
        match="did not support HTTP range downloads",
    ):
        android_build_artifact.read_android_build_zip_entries(
            "https://build.example.test/artifact/url",
            ("SYSTEM/apex/com.android.runtime.apex",),
            expected_size=len(content),
            http_client=_RangeClient(content, status_code=200),
            timeout=10,
        )
