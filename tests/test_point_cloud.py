"""Regression checks for point-cloud generation, download, and validation."""

from __future__ import annotations

import struct
import urllib.error
from email.message import Message
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
        self._url = url
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
        return self._content[:size]


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
    responses = iter(
        [
            {"r": 0},
            {"r": 0, "d": {"name": [""]}},
            {"r": 0, "d": {"name": ["private/generated-map.pcd"]}},
        ]
    )

    def call_app_action(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return next(responses)

    signed_url = "https://downloads.example.invalid/object?private=signature"
    cloud = type(
        "Cloud",
        (),
        {"get_interim_file_url": lambda self, name: signed_url},
    )()
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    client._sync_call_app_action = call_app_action
    client._sync_get_cloud_protocol = lambda: cloud
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        client_module.urllib.request,
        "urlopen",
        lambda request, timeout: _FakeResponse(content, request.full_url),
    )

    result = client._sync_download_app_map_point_cloud(0, 5, 0.1, 10, 1024)

    assert calls == [
        {"m": "a", "p": 0, "o": 10, "d": {"idx": 0}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
    ]
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

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fail_download)

    with pytest.raises(DreameLawnMowerPointCloudError) as captured:
        _internal_client_module._download_point_cloud_content(
            signed_url,
            timeout=10,
            max_bytes=1024,
        )

    assert "HTTP status 403" in str(captured.value)
    assert signed_url not in str(captured.value)
    assert signed_url not in repr(captured.value)
