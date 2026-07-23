"""Regression checks for point-cloud generation, download, and validation."""

from __future__ import annotations

import struct
import urllib.error
from email.message import Message
from types import SimpleNamespace
from typing import Any

import pytest

import dreame_lawn_mower_client.client as client_module
from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerPointCloudError,
    DreameLawnMowerPointCloudMetadata,
    parse_pcd_metadata,
)
from dreame_lawn_mower_client._loader import load_internal_module
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor

_internal_client_module = load_internal_module("client")
_internal_protocol_module = load_internal_module("protocol")


def _binary_pcd(*points: tuple[float, float, float, int]) -> bytes:
    header = (
        "# .PCD v0.7\n"
        "VERSION 0.7\n"
        "FIELDS x y z rgb\n"
        "SIZE 4 4 4 4\n"
        "TYPE F F F U\n"
        "COUNT 1 1 1 1\n"
        f"WIDTH {len(points)}\n"
        "HEIGHT 1\n"
        f"POINTS {len(points)}\n"
        "DATA binary\n"
    ).encode()
    payload = b"".join(struct.pack("<fffI", *point) for point in points)
    return header + payload


def _ascii_pcd(point_count: int) -> bytes:
    header = (
        "# .PCD v0.7\n"
        "VERSION 0.7\n"
        "FIELDS x y z\n"
        "SIZE 4 4 4\n"
        "TYPE F F F\n"
        "COUNT 1 1 1\n"
        f"WIDTH {point_count}\n"
        "HEIGHT 1\n"
        f"POINTS {point_count}\n"
        "DATA ascii\n"
    ).encode()
    return header + (b"1 2 3\n" * point_count)


def _client() -> DreameLawnMowerClient:
    return DreameLawnMowerClient(
        username="user@example.invalid",
        password="secret",
        country="eu",
        account_type="dreame",
        descriptor=DreameLawnMowerDescriptor(
            did="device-1",
            name="Garage Mower",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
    )


class _FakeResponse:
    def __init__(self, content: bytes, url: str) -> None:
        self._content = content
        self._offset = 0
        self._url = url
        self.fp = SimpleNamespace(
            raw=SimpleNamespace(
                _sock=SimpleNamespace(settimeout=lambda timeout: None),
            )
        )
        self.headers = Message()
        self.headers["Content-Length"] = str(len(content))
        self.headers["Content-Type"] = "application/octet-stream"

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, size: int) -> bytes:
        chunk = self._content[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def test_parse_pcd_metadata_accepts_binary_xyz_rgb() -> None:
    content = _binary_pcd(
        (1.0, 2.0, 3.0, 0xFF0000),
        (-1.0, -2.0, 0.5, 0x00FF00),
    )

    metadata = parse_pcd_metadata(content)

    assert isinstance(metadata, DreameLawnMowerPointCloudMetadata)
    assert metadata.points == 2
    assert metadata.fields == ("x", "y", "z", "rgb")
    assert metadata.data_encoding == "binary"
    assert metadata.bytes_per_point == 16
    assert metadata.has_rgb is True
    assert metadata.as_dict()["total_bytes"] == len(content)


def test_parse_pcd_metadata_validates_ascii_rows_incrementally() -> None:
    content = _ascii_pcd(10_000)

    metadata = parse_pcd_metadata(content)

    assert metadata.points == 10_000
    assert metadata.data_encoding == "ascii"
    assert metadata.payload_bytes == 60_000


@pytest.mark.parametrize(
    "content, message",
    [
        (b"", "empty"),
        (
            b"VERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\n"
            b"WIDTH 1\nHEIGHT 1\nPOINTS 1\nDATA binary\n",
            "payload length",
        ),
        (
            b"VERSION 0.7\nFIELDS x y\nSIZE 4 4\nTYPE F F\n"
            b"WIDTH 0\nHEIGHT 1\nPOINTS 0\nDATA binary\n",
            "x, y, and z",
        ),
        (
            b"VERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\n"
            b"WIDTH 1\nHEIGHT 1\nPOINTS 1\nDATA ascii\n1 2\n",
            "column count",
        ),
        (
            b"VERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\n"
            b"WIDTH 1\nHEIGHT 1\nPOINTS 1\nDATA ascii\n1 invalid 3\n",
            "invalid scalar",
        ),
        (
            b"VERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\n"
            b"WIDTH 1\nHEIGHT 1\nPOINTS 1\nDATA ascii\n1e100 2 3\n",
            "invalid scalar",
        ),
        (
            b"VERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\n"
            b"WIDTH 1\nHEIGHT 1\nPOINTS 1\nDATA binary_compressed\n"
            b"\x00\x00\x00\x00\x0c\x00\x00\x00",
            "Unsupported PCD DATA encoding",
        ),
        (
            _binary_pcd((float("nan"), 2.0, 3.0, 0x123456)),
            "finite values",
        ),
    ],
)
def test_parse_pcd_metadata_rejects_invalid_payloads(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(DreameLawnMowerPointCloudError, match=message):
        parse_pcd_metadata(content)


def test_download_point_cloud_uses_a2_generation_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    calls: list[dict[str, Any]] = []
    call_options: list[dict[str, Any]] = []
    responses = iter(
        [
            {"r": 0, "d": {"name": ["private/previous-map.pcd"]}},
            {"r": 0},
            {"r": 0, "d": {"name": ["private/previous-map.pcd"]}},
            {"r": 0, "d": {"name": ["private/generated-map.pcd"]}},
            {"r": 0, "d": {"name": ["private/generated-map.pcd"]}},
        ]
    )

    def call_app_action(
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        calls.append(payload)
        call_options.append(kwargs)
        return next(responses)

    signed_url = "https://downloads.example.invalid/object?private=signature"
    interim_file_options: list[dict[str, Any]] = []

    def get_interim_file_url(
        name: str,
        **kwargs: Any,
    ) -> str:
        interim_file_options.append(kwargs)
        return signed_url

    cloud = type(
        "Cloud",
        (),
        {
            "get_interim_file_url": lambda self, name, **kwargs: (
                get_interim_file_url(name, **kwargs)
            )
        },
    )()
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    client._sync_call_app_action = call_app_action
    client._sync_get_cloud_protocol = lambda: cloud
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    downloads = iter([b"not a pcd", content])
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout: _FakeResponse(
            next(downloads),
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(0, 5, 0.1, 10, 1024)

    assert calls == [
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "a", "p": 0, "o": 10, "d": {"idx": 0}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
    ]
    assert all(options["retry_count"] == 0 for options in call_options)
    assert all(0 < options["timeout"] <= 5 for options in call_options)
    assert len(interim_file_options) == 2
    assert all(
        options["retry_count"] == 0 for options in interim_file_options
    )
    assert all(
        0 < options["timeout"] <= 5 for options in interim_file_options
    )
    assert result.map_index == 0
    assert result.content == content
    assert result.metadata.points == 1
    assert not hasattr(result, "url")
    assert not hasattr(result, "object_name")


def test_download_point_cloud_error_does_not_expose_signed_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_url = "https://downloads.example.invalid/object?secret=do-not-log"

    def fail_download(request: Any, timeout: float) -> None:
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            Message(),
            None,
        )

    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        fail_download,
    )

    with pytest.raises(DreameLawnMowerPointCloudError) as captured:
        _internal_client_module._download_point_cloud_content(
            signed_url,
            timeout=10,
            max_bytes=1024,
        )

    assert "HTTP status 403" in str(captured.value)
    assert signed_url not in str(captured.value)
    assert signed_url not in repr(captured.value)


def test_download_point_cloud_rejects_insecure_redirect_before_following() -> None:
    request = urllib.request.Request(
        "https://downloads.example.invalid/private-object"
    )
    handler = _internal_client_module._HttpsOnlyPointCloudRedirectHandler()

    with pytest.raises(
        DreameLawnMowerPointCloudError,
        match="redirected to an insecure URL",
    ):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            Message(),
            "http://downloads.example.invalid/private-object",
        )


def test_interim_file_cloud_logs_redact_request_and_response() -> None:
    url = (
        "https://eu.example.invalid/"
        "dreame-user-iot/iotfile/getDownloadUrl"
    )

    assert _internal_protocol_module._cloud_request_log_value(
        url,
        '{"filename":"private/generated-map.pcd"}',
    ) == "<redacted interim file payload>"
    assert _internal_protocol_module._cloud_request_log_value(
        url,
        '{"data":"https://downloads.example.invalid/object?secret=signature"}',
    ) == "<redacted interim file payload>"


def test_download_point_cloud_enforces_overall_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_url = "https://downloads.example.invalid/object?private=signature"
    read_calls = 0
    applied_timeouts: list[float] = []
    response = _FakeResponse(b"chunk", signed_url)
    del response.headers["Content-Length"]
    response.fp.raw._sock.settimeout = applied_timeouts.append

    def read(size: int) -> bytes:
        nonlocal read_calls
        read_calls += 1
        return b"chunk"

    response.read = read
    monotonic_values = iter([100.0, 100.2, 101.1])
    monkeypatch.setattr(
        client_module.time,
        "monotonic",
        lambda: next(monotonic_values, 101.1),
    )
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout: response,
    )

    with pytest.raises(DreameLawnMowerPointCloudError, match="timed out"):
        _internal_client_module._download_point_cloud_content(
            signed_url,
            timeout=1.0,
            max_bytes=1024,
        )

    assert read_calls == 1
    assert applied_timeouts == pytest.approx([0.8])


def test_point_cloud_app_action_uses_and_enforces_remaining_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    options: list[dict[str, Any]] = []

    def call_app_action(
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        options.append(kwargs)
        return {"r": 0, "d": {}}

    client._sync_call_app_action = call_app_action
    monotonic_values = iter([100.2, 101.1])
    monkeypatch.setattr(
        client_module.time,
        "monotonic",
        lambda: next(monotonic_values, 101.1),
    )

    with pytest.raises(DreameLawnMowerPointCloudError, match="timed out"):
        client._sync_call_point_cloud_action(
            {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
            operation="read the generated point-cloud object state",
            deadline=101.0,
            require_data=True,
        )

    assert options == [
        {
            "retry_count": 0,
            "timeout": pytest.approx(0.8),
        }
    ]


def test_point_cloud_url_lookup_uses_and_enforces_remaining_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    options: list[dict[str, Any]] = []

    def get_interim_file_url(
        object_name: str,
        **kwargs: Any,
    ) -> str:
        options.append(kwargs)
        return "https://downloads.example.invalid/object"

    cloud = SimpleNamespace(get_interim_file_url=get_interim_file_url)
    monotonic_values = iter([100.2, 101.1])
    monkeypatch.setattr(
        client_module.time,
        "monotonic",
        lambda: next(monotonic_values, 101.1),
    )

    with pytest.raises(DreameLawnMowerPointCloudError, match="timed out"):
        client._sync_get_point_cloud_download_url(
            cloud,
            "private/generated-map.pcd",
            deadline=101.0,
        )

    assert options == [
        {
            "retry_count": 0,
            "timeout": pytest.approx(0.8),
        }
    ]


@pytest.mark.parametrize(
    "failed_poll",
    [
        None,
        {"r": 0, "d": {}},
    ],
)
def test_download_point_cloud_rejects_ambiguous_failed_object_poll(
    monkeypatch: pytest.MonkeyPatch,
    failed_poll: Any,
) -> None:
    client = _client()
    responses = iter(
        [
            {"r": 0, "d": {"name": ["private/previous-map.pcd"]}},
            {"r": 0},
            failed_poll,
        ]
    )
    client._sync_call_app_action = lambda payload, **kwargs: next(responses)
    client._sync_get_cloud_protocol = lambda: type(
        "Cloud",
        (),
        {"get_interim_file_url": lambda self, name, **kwargs: None},
    )()
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)

    with pytest.raises(
        DreameLawnMowerPointCloudError,
        match="could not read the generated point-cloud object state",
    ):
        client._sync_download_app_map_point_cloud(0, 5, 0.1, 10, 1024)
