"""Regression checks for point-cloud generation, download, and validation."""

from __future__ import annotations

import asyncio
import json
import struct
import threading
import time
import urllib.error
from email.message import Message
from types import SimpleNamespace
from typing import Any

import pytest
import requests

import dreame_lawn_mower_client.client as client_module
from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerPointCloudError,
    DreameLawnMowerPointCloudMetadata,
    parse_pcd_metadata,
)
from dreame_lawn_mower_client._loader import load_internal_module
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor

_internal_client_module = load_internal_module("client_map_helpers")
_internal_client_facade_module = load_internal_module("client")
_internal_deadline_module = load_internal_module("deadline")
_internal_exceptions_module = load_internal_module("exceptions")
_internal_point_cloud_module = load_internal_module("point_cloud")
_internal_protocol_module = load_internal_module("protocol_cloud")


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


def _mova_client() -> DreameLawnMowerClient:
    return DreameLawnMowerClient(
        username="user@example.invalid",
        password="secret",
        country="eu",
        account_type="mova",
        descriptor=DreameLawnMowerDescriptor(
            did="device-1",
            name="Garage Mower",
            model="mova.mower.g2408",
            display_model="A2",
            account_type="mova",
            country="eu",
        ),
    )


class _FakeResponse:
    def __init__(
        self,
        content: bytes,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
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
        if etag is not None:
            self.headers["ETag"] = etag
        if last_modified is not None:
            self.headers["Last-Modified"] = last_modified

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
            {"r": 0, "d": {"name": ["private/generated-map-2.pcd"]}},
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
            "get_interim_file_url": lambda self, name, **kwargs: get_interim_file_url(
                name, **kwargs
            )
        },
    )()
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    client._sync_call_app_action = call_app_action
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    downloads = iter([b"not a pcd", content])
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
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
    assert all(options["retry_count"] == 0 for options in interim_file_options)
    assert all(0 < options["timeout"] <= 5 for options in interim_file_options)
    assert result.map_index == 0
    assert result.content == content
    assert result.metadata.points == 1
    assert not hasattr(result, "url")
    assert not hasattr(result, "object_name")


def test_download_point_cloud_uses_fresh_lidar_announcement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    calls: list[dict[str, Any]] = []
    responses = iter([{"r": 0}])

    def call_app_action(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(payload)
        return next(responses)

    property_options: list[dict[str, Any]] = []
    now_ms = int(time.time() * 1000)
    update_dates = iter([now_ms - 1_000, now_ms + 1_000])

    def get_properties(key: str, **kwargs: Any) -> list[dict[str, Any]]:
        property_options.append(kwargs)
        return [
            {
                "key": key,
                "value": "private/generated-map.bin",
                "updateDate": next(update_dates, 2_000),
            }
        ]

    cloud = SimpleNamespace(
        get_properties=get_properties,
        get_interim_file_url=lambda name, **options: (
            "https://downloads.example.invalid/object"
        ),
    )
    client._sync_call_app_action = call_app_action
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            content,
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(0, 5, 0.1, 10, 1024)

    assert calls == [
        {"m": "a", "p": 0, "o": 10, "d": {"idx": 0}},
    ]
    assert property_options
    assert property_options[0]["retry_count"] == 0
    assert 0 < property_options[0]["timeout"] <= 5
    assert property_options[0]["deadline"] > time.monotonic()
    assert result.content == content
    assert result.metadata.points == 1


def test_download_point_cloud_uses_action_dispatch_as_freshness_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    clock = [1.0]
    actions: list[dict[str, Any]] = []
    announced_names = iter(
        [
            ("private/baseline-map.bin", 500),
            ("private/other-upload.bin", 1_500),
            ("private/generated-map.bin", 2_500),
        ]
    )
    signed_names: list[str] = []

    def call_app_action(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        actions.append(payload)
        clock[0] = 2.0
        kwargs["on_dispatch"]()
        clock[0] = 3.0
        return {"r": 0}

    def get_properties(key: str, **kwargs: Any) -> list[dict[str, Any]]:
        name, updated_at = next(announced_names)
        return [
            {
                "key": key,
                "value": name,
                "updateDate": updated_at,
            }
        ]

    def get_interim_file_url(name: str, **kwargs: Any) -> str:
        signed_names.append(name)
        return "https://downloads.example.invalid/object"

    cloud = SimpleNamespace(
        get_properties=get_properties,
        get_interim_file_url=get_interim_file_url,
    )
    client._sync_call_app_action = call_app_action
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    monkeypatch.setattr(client_module.time, "time", lambda: clock[0])
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            content,
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(0, 5, 0.1, 10, 1024)

    assert actions == [{"m": "a", "p": 0, "o": 10, "d": {"idx": 0}}]
    assert signed_names == ["private/generated-map.bin"]
    assert result.source == "generated"


def test_download_point_cloud_uses_stored_active_map_when_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    actions: list[dict[str, Any]] = []
    client._sync_call_app_action = lambda payload, **kwargs: actions.append(payload)
    cloud = SimpleNamespace(
        get_properties=lambda key, **options: [
            {
                "key": key,
                "value": "private/stored-map.bin",
                "updateDate": 1_000,
            }
        ],
        get_interim_file_url=lambda name, **options: (
            "https://downloads.example.invalid/stored"
        ),
    )
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            content,
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(
        0,
        5,
        0.1,
        10,
        1024,
        allow_stored=True,
    )

    assert actions == []
    assert result.map_index == 0
    assert result.content == content
    assert result.metadata.points == 1
    assert result.source == "stored"


def test_download_point_cloud_uses_legacy_stored_bin_after_property_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    actions: list[dict[str, Any]] = []
    client._sync_call_app_action = lambda payload, **kwargs: (
        actions.append(payload)
        or {"r": 0, "d": {"name": ["private/fixed-map.bin", ""]}}
    )
    signed_names: list[str] = []

    def get_interim_file_url(name: str, **options: Any) -> str:
        signed_names.append(name)
        return "https://downloads.example.invalid/stored"

    client._sync_get_cloud_protocol = lambda **kwargs: SimpleNamespace(
        get_properties=lambda key, **options: None,
        get_interim_file_url=get_interim_file_url,
    )
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            content,
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(
        0,
        45,
        1,
        10,
        1024,
        allow_stored=True,
    )

    assert actions == [{"m": "g", "t": "OBJ", "d": {"type": "3dmap"}}]
    assert signed_names == ["private/fixed-map.bin"]
    assert result.content == content
    assert result.source == "stored"


def test_download_point_cloud_reuses_privately_cached_metadata_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    actions: list[dict[str, Any]] = []
    client._sync_update_app_map_inventory_identity(
        [
            {
                "idx": 0,
                "current": True,
                "created": True,
                "info": {"hash": "map-hash-1", "size": 123},
            }
        ]
    )

    def call_app_action(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        actions.append(payload)
        if len(actions) == 1:
            return {
                "r": 0,
                "d": {"name": ["private/fixed-map.bin", ""]},
            }
        raise AssertionError("Stored metadata should avoid another mower action")

    client._sync_call_app_action = call_app_action
    metadata = client._sync_get_app_map_objects(include_urls=False)
    signed_names: list[str] = []

    def get_interim_file_url(name: str, **options: Any) -> str:
        signed_names.append(name)
        return "https://downloads.example.invalid/stored"

    client._sync_get_cloud_protocol = lambda **kwargs: SimpleNamespace(
        get_interim_file_url=get_interim_file_url,
        get_properties=lambda *args, **kwargs: pytest.fail(
            "Cached metadata should avoid the announcement preflight"
        ),
    )
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            content,
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(
        0,
        45,
        1,
        10,
        1024,
        allow_stored=True,
    )

    assert metadata["objects"] == [
        {"extension": "bin", "url_present": False},
        {"extension": None, "url_present": False},
    ]
    assert actions == [{"m": "g", "t": "OBJ", "d": {"type": "3dmap"}}]
    assert signed_names == ["private/fixed-map.bin"]
    assert result.content == content
    assert result.source == "stored"


def test_cached_point_cloud_names_are_invalidated_when_map_identity_changes() -> None:
    client = _client()
    old_map = [
        {
            "idx": 0,
            "current": True,
            "created": True,
            "info": {"hash": "old-map-hash", "size": 123},
        }
    ]
    new_map = [
        {
            "idx": 0,
            "current": True,
            "created": True,
            "info": {"hash": "new-map-hash", "size": 456},
        }
    ]
    client._sync_update_app_map_inventory_identity(old_map)
    client._sync_call_app_action = lambda payload, **kwargs: {
        "r": 0,
        "d": {"name": ["private/old-map.bin"]},
    }

    client._sync_get_app_map_objects(include_urls=False)

    assert client._latest_app_map_object_names == ("private/old-map.bin",)
    assert client._latest_app_map_object_inventory_identity is not None

    client._sync_update_app_map_inventory_identity(new_map)

    assert client._latest_app_map_object_names == ()
    assert client._latest_app_map_object_inventory_identity is None


def test_download_point_cloud_regenerates_when_stored_object_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    actions: list[dict[str, Any]] = []
    responses = iter([{"r": 0}])

    def call_app_action(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        actions.append(payload)
        return next(responses)

    now_ms = int(time.time() * 1000)
    update_dates = iter([now_ms - 1_000, now_ms - 1_000, now_ms + 1_000])

    def get_properties(key: str, **options: Any) -> list[dict[str, Any]]:
        updated_at = next(update_dates)
        return [
            {
                "key": key,
                "value": (
                    "private/stored-map.bin"
                    if updated_at < now_ms
                    else "private/fresh-map.bin"
                ),
                "updateDate": updated_at,
            }
        ]

    cloud = SimpleNamespace(
        get_properties=get_properties,
        get_interim_file_url=lambda name, **options: (
            "https://downloads.example.invalid/object"
        ),
    )
    client._sync_call_app_action = call_app_action
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    fresh_content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    downloads = iter([b"not a pcd", fresh_content])
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            next(downloads),
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(
        0,
        5,
        0.1,
        10,
        1024,
        allow_stored=True,
    )

    assert actions == [{"m": "a", "p": 0, "o": 10, "d": {"idx": 0}}]
    assert result.content == fresh_content


def test_download_point_cloud_retries_announced_object_until_signable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    responses = iter([{"r": 0}])
    client._sync_call_app_action = lambda payload, **kwargs: next(responses)
    signed_urls = iter(
        [
            None,
            None,
            None,
            None,
            "https://downloads.example.invalid/object",
        ]
    )
    now_ms = int(time.time() * 1000)
    update_dates = iter([now_ms - 1_000, now_ms + 1_000])
    cloud = SimpleNamespace(
        get_properties=lambda key, **options: [
            {
                "key": key,
                "value": "private/generated-map.bin",
                "updateDate": next(update_dates, now_ms + 1_000),
            }
        ],
        get_interim_file_url=lambda name, **options: next(signed_urls),
    )
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            content,
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(0, 5, 0.1, 10, 1024)

    assert result.content == content
    assert result.metadata.points == 1


def test_download_point_cloud_retries_malformed_signer_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    client._sync_call_app_action = lambda payload, **kwargs: {"r": 0}
    now_ms = int(time.time() * 1000)
    update_dates = iter([now_ms - 1_000, now_ms + 1_000])
    signer_results: Any = iter(
        [
            json.JSONDecodeError("Expecting value", "", 0),
            "https://downloads.example.invalid/object",
        ]
    )

    def get_interim_file_url(name: str, **options: Any) -> str:
        result = next(signer_results)
        if isinstance(result, Exception):
            raise result
        return result

    cloud = SimpleNamespace(
        get_properties=lambda key, **options: [
            {
                "key": key,
                "value": "private/generated-map.bin",
                "updateDate": next(update_dates, now_ms + 1_000),
            }
        ],
        get_interim_file_url=get_interim_file_url,
    )
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            content,
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(0, 5, 0.1, 10, 1024)

    assert result.content == content
    assert result.metadata.points == 1


def test_download_point_cloud_does_not_fallback_from_stale_announcement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    calls: list[dict[str, Any]] = []
    responses = iter([{"r": 0}])

    def call_app_action(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(payload)
        return next(responses)

    cloud = SimpleNamespace(
        get_properties=lambda key, **options: [
            {
                "key": key,
                "value": "private/stale-map.bin",
                "updateDate": 1,
            }
        ],
        get_interim_file_url=lambda name, **options: (
            "https://downloads.example.invalid/object"
        ),
    )
    client._sync_call_app_action = call_app_action
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    with pytest.raises(DreameLawnMowerPointCloudError):
        client._sync_download_app_map_point_cloud(
            0,
            0.05,
            0.001,
            10,
            1024,
        )

    assert calls == [
        {"m": "a", "p": 0, "o": 10, "d": {"idx": 0}},
    ]


def test_download_point_cloud_caps_optional_announcement_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    calls: list[dict[str, Any]] = []
    responses = iter(
        [
            {"r": 0, "d": {"name": ["private/previous-map.pcd"]}},
            {"r": 0},
            {"r": 0, "d": {"name": ["private/generated-map.pcd"]}},
        ]
    )

    def call_app_action(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(payload)
        return next(responses)

    property_options: list[dict[str, Any]] = []

    def get_properties(key: str, **options: Any) -> None:
        property_options.append(options)
        return None

    cloud = SimpleNamespace(
        get_properties=get_properties,
        get_interim_file_url=lambda name, **options: (
            "https://downloads.example.invalid/object"
        ),
    )
    client._sync_call_app_action = call_app_action
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            content,
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(0, 45, 0.1, 10, 1024)

    assert len(property_options) == 2
    assert property_options[0]["retry_count"] == 0
    assert 1.9 < property_options[0]["timeout"] <= 2.0
    assert property_options[0]["deadline"] > time.monotonic()
    assert property_options[1]["retry_count"] == 0
    assert 0 < property_options[1]["timeout"] <= 0.5
    assert calls == [
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "a", "p": 0, "o": 10, "d": {"idx": 0}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
    ]
    assert result.content == content


def test_download_point_cloud_recovers_from_transient_announcement_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    actions: list[dict[str, Any]] = []
    client._sync_call_app_action = lambda payload, **kwargs: (
        actions.append(payload)
        or (
            {"r": 0, "d": {"name": ["private/stale-map.pcd"]}}
            if payload.get("m") == "g"
            else {"r": 0}
        )
    )
    now_ms = int(time.time() * 1000)
    property_results = iter(
        [
            None,
            [
                {
                    "key": "99.20",
                    "value": "private/generated-map.bin",
                    "updateDate": now_ms + 1_000,
                }
            ],
        ]
    )
    cloud = SimpleNamespace(
        get_properties=lambda key, **options: next(property_results),
        get_interim_file_url=lambda name, **options: (
            "https://downloads.example.invalid/object"
        ),
    )
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            content,
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(0, 5, 0.1, 10, 1024)

    assert actions == [
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "a", "p": 0, "o": 10, "d": {"idx": 0}},
    ]
    assert result.content == content
    assert result.metadata.points == 1


def test_download_point_cloud_preserves_time_for_later_announcement_reprobe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    clock = [100.0]
    property_calls = 0
    legacy_poll_deadlines: list[float] = []
    action_calls: list[dict[str, Any]] = []

    def get_properties(key: str, **options: Any) -> Any:
        nonlocal property_calls
        property_calls += 1
        if property_calls < 3:
            return None
        return [
            {
                "key": key,
                "value": "private/generated-map.bin",
                "updateDate": 101_000,
            }
        ]

    def call_point_cloud_action(
        payload: dict[str, Any],
        *,
        operation: str,
        deadline: float,
        require_data: bool,
        on_dispatch: Any = None,
    ) -> Any:
        action_calls.append(payload)
        if payload.get("m") == "a":
            on_dispatch()
            return None
        if len(action_calls) == 1:
            return {"name": ["private/stale-map.pcd"]}
        legacy_poll_deadlines.append(deadline)
        clock[0] = deadline
        raise DreameLawnMowerPointCloudError(
            "bounded legacy poll timed out",
            code="point_cloud_timeout",
        )

    cloud = SimpleNamespace(
        get_properties=get_properties,
        get_interim_file_url=lambda name, **options: (
            "https://downloads.example.invalid/object"
        ),
    )
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    client._sync_call_point_cloud_action = call_point_cloud_action
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            content,
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(
        0,
        10,
        0.1,
        10,
        1024,
        deadline=110.0,
    )

    assert property_calls == 3
    assert legacy_poll_deadlines == [102.0]
    assert action_calls == [
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "a", "p": 0, "o": 10, "d": {"idx": 0}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
    ]
    assert result.content == content


def test_download_point_cloud_eventually_allows_slow_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    clock = [100.0]
    action_calls: list[dict[str, Any]] = []
    legacy_poll_deadlines: list[float] = []

    def call_point_cloud_action(
        payload: dict[str, Any],
        *,
        operation: str,
        deadline: float,
        require_data: bool,
        on_dispatch: Any = None,
    ) -> Any:
        action_calls.append(payload)
        if payload.get("m") == "a":
            on_dispatch()
            return None
        if len(action_calls) == 1:
            return {"name": ["private/stale-map.pcd"]}
        legacy_poll_deadlines.append(deadline)
        if len(legacy_poll_deadlines) < 3:
            clock[0] = deadline
            raise DreameLawnMowerPointCloudError(
                "bounded legacy poll timed out",
                code="point_cloud_timeout",
            )
        return {"name": ["private/generated-map.pcd"]}

    cloud = SimpleNamespace(
        get_properties=lambda key, **options: None,
        get_interim_file_url=lambda name, **options: (
            "https://downloads.example.invalid/object"
        ),
    )
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    client._sync_call_point_cloud_action = call_point_cloud_action
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            content,
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(
        0,
        15,
        0.1,
        10,
        1024,
        deadline=115.0,
    )

    assert legacy_poll_deadlines == [102.0, 104.0, 115.0]
    assert action_calls == [
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "a", "p": 0, "o": 10, "d": {"idx": 0}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
    ]
    assert result.content == content


def test_download_point_cloud_bounds_inconclusive_legacy_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    clock = [100.0]
    action_calls: list[dict[str, Any]] = []
    baseline_deadlines: list[float] = []
    property_calls = 0

    def get_properties(key: str, **options: Any) -> Any:
        nonlocal property_calls
        property_calls += 1
        if property_calls == 1:
            return None
        return [
            {
                "key": key,
                "value": "private/generated-map.bin",
                "updateDate": 103_000,
            }
        ]

    def call_point_cloud_action(
        payload: dict[str, Any],
        *,
        operation: str,
        deadline: float,
        require_data: bool,
        on_dispatch: Any = None,
    ) -> Any:
        action_calls.append(payload)
        if payload.get("m") == "g":
            baseline_deadlines.append(deadline)
            clock[0] = deadline
            raise DreameLawnMowerPointCloudError(
                "bounded baseline timed out",
                code="point_cloud_timeout",
            )
        on_dispatch()
        return None

    cloud = SimpleNamespace(
        get_properties=get_properties,
        get_interim_file_url=lambda name, **options: (
            "https://downloads.example.invalid/object"
        ),
    )
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    client._sync_call_point_cloud_action = call_point_cloud_action
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            content,
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(
        0,
        15,
        0.1,
        10,
        1024,
        deadline=115.0,
    )

    assert baseline_deadlines == [102.0]
    assert action_calls == [
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "a", "p": 0, "o": 10, "d": {"idx": 0}},
    ]
    assert result.content == content


def test_download_point_cloud_does_not_treat_timed_out_baseline_as_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    clock = [100.0]
    legacy_results = iter(
        [
            DreameLawnMowerPointCloudError(
                "baseline timed out",
                code="point_cloud_timeout",
            ),
            DreameLawnMowerPointCloudError(
                "bounded poll timed out",
                code="point_cloud_timeout",
            ),
            DreameLawnMowerPointCloudError(
                "bounded poll timed out",
                code="point_cloud_timeout",
            ),
            {"name": ["private/stale-map.pcd"]},
            {"name": ["private/generated-map.pcd"]},
        ]
    )
    returned_names: list[str] = []

    def call_point_cloud_action(
        payload: dict[str, Any],
        *,
        operation: str,
        deadline: float,
        require_data: bool,
        on_dispatch: Any = None,
    ) -> Any:
        if payload.get("m") == "a":
            on_dispatch()
            return None
        result = next(legacy_results)
        if isinstance(result, DreameLawnMowerPointCloudError):
            clock[0] = deadline
            raise result
        returned_names.extend(result["name"])
        return result

    cloud = SimpleNamespace(
        get_properties=lambda key, **options: None,
        get_interim_file_url=lambda name, **options: (
            "https://downloads.example.invalid/object"
        ),
    )
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    client._sync_call_point_cloud_action = call_point_cloud_action
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(client_module.time, "time", lambda: 100.0)
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            content,
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(
        0,
        15,
        0.1,
        10,
        1024,
        deadline=115.0,
    )

    assert returned_names == [
        "private/stale-map.pcd",
        "private/generated-map.pcd",
    ]
    assert result.content == content


@pytest.mark.parametrize(
    (
        "deadline",
        "fallback_reserve_seconds",
        "expected_timeout",
        "expected_probe_deadline",
    ),
    [
        (145.0, 15.0, 2.0, 102.0),
        (105.0, 2.5, 2.0, 102.0),
    ],
)
def test_announcement_probe_uses_reserved_absolute_sub_deadline(
    monkeypatch: pytest.MonkeyPatch,
    deadline: float,
    fallback_reserve_seconds: float,
    expected_timeout: float,
    expected_probe_deadline: float,
) -> None:
    client = _client()
    options: list[dict[str, Any]] = []

    def get_properties(key: str, **kwargs: Any) -> None:
        options.append(kwargs)
        return None

    monkeypatch.setattr(client_module.time, "monotonic", lambda: 100.0)

    result = client._sync_get_announced_point_cloud_object(
        SimpleNamespace(get_properties=get_properties),
        requested_after_ms=0,
        fallback_reserve_seconds=fallback_reserve_seconds,
        deadline=deadline,
    )

    assert result == (None, None, None)
    assert options == [
        {
            "retry_count": 0,
            "timeout": expected_timeout,
            "deadline": expected_probe_deadline,
        }
    ]


def test_announcement_probe_preserves_support_when_value_is_empty() -> None:
    client = _client()

    result = client._sync_get_announced_point_cloud_object(
        SimpleNamespace(
            get_properties=lambda key, **kwargs: [
                {
                    "key": key,
                    "value": None,
                    "updateDate": None,
                }
            ]
        ),
        requested_after_ms=0,
        deadline=time.monotonic() + 1,
    )

    assert result == (True, None, None)


def test_announcement_probe_treats_malformed_json_as_inconclusive() -> None:
    client = _client()

    def get_properties(key: str, **kwargs: Any) -> None:
        raise json.JSONDecodeError("Expecting value", "", 0)

    result = client._sync_get_announced_point_cloud_object(
        SimpleNamespace(get_properties=get_properties),
        requested_after_ms=0,
        deadline=time.monotonic() + 1,
    )

    assert result == (None, None, None)


def test_announcement_probe_treats_absent_property_as_unsupported() -> None:
    client = _client()

    result = client._sync_get_announced_point_cloud_object(
        SimpleNamespace(get_properties=lambda key, **kwargs: []),
        requested_after_ms=0,
        deadline=time.monotonic() + 1,
    )

    assert result == (False, None, None)


def test_announcement_probe_rejects_changed_object_from_before_request() -> None:
    client = _client()

    result = client._sync_get_announced_point_cloud_object(
        SimpleNamespace(
            get_properties=lambda key, **kwargs: [
                {
                    "key": key,
                    "value": "private/other-map.bin",
                    "updateDate": 2_000,
                }
            ]
        ),
        requested_after_ms=3_000,
        baseline=("private/baseline-map.bin", 1_000),
        deadline=time.monotonic() + 1,
    )

    assert result == (
        True,
        None,
        ("private/other-map.bin", 2_000),
    )


def test_announcement_probe_requires_post_request_time_without_baseline() -> None:
    client = _client()

    result = client._sync_get_announced_point_cloud_object(
        SimpleNamespace(
            get_properties=lambda key, **kwargs: [
                {
                    "key": key,
                    "value": "private/recent-other-map.bin",
                    "updateDate": 3_000,
                }
            ]
        ),
        requested_after_ms=3_000,
        require_post_request=True,
        deadline=time.monotonic() + 1,
    )

    assert result == (
        True,
        None,
        ("private/recent-other-map.bin", 3_000),
    )


def test_announcement_probe_treats_overflowing_timestamp_as_unusable() -> None:
    client = _client()

    result = client._sync_get_announced_point_cloud_object(
        SimpleNamespace(
            get_properties=lambda key, **kwargs: [
                {
                    "key": key,
                    "value": "private/generated-map.bin",
                    "updateDate": float("inf"),
                }
            ]
        ),
        requested_after_ms=3_000,
        require_post_request=True,
        deadline=time.monotonic() + 1,
    )

    assert result == (True, None, None)


def test_download_point_cloud_follows_initially_empty_announcement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    actions: list[dict[str, Any]] = []
    now_ms = int(time.time() * 1000)
    property_values = iter(
        [
            (None, None),
            ("private/fresh-map.bin", now_ms + 1_000),
        ]
    )

    def call_app_action(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        actions.append(payload)
        return {"r": 0}

    def get_properties(key: str, **kwargs: Any) -> list[dict[str, Any]]:
        value, updated_at = next(property_values)
        return [
            {
                "key": key,
                "value": value,
                "updateDate": updated_at,
            }
        ]

    cloud = SimpleNamespace(
        get_properties=get_properties,
        get_interim_file_url=lambda name, **options: (
            "https://downloads.example.invalid/object"
        ),
    )
    client._sync_call_app_action = call_app_action
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            content,
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(
        0,
        5,
        0.1,
        10,
        1024,
    )

    assert actions == [{"m": "a", "p": 0, "o": 10, "d": {"idx": 0}}]
    assert result.content == content


def test_download_point_cloud_bounds_invalid_announced_object_downloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    client._sync_call_app_action = lambda payload, **kwargs: {"r": 0}
    now_ms = int(time.time() * 1000)
    update_dates = iter([now_ms - 1_000, now_ms + 1_000])
    cloud = SimpleNamespace(
        get_properties=lambda key, **options: [
            {
                "key": key,
                "value": "private/generated-map.bin",
                "updateDate": next(update_dates, now_ms + 1_000),
            }
        ],
        get_interim_file_url=lambda name, **options: (
            "https://downloads.example.invalid/object"
        ),
    )
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    download_count = 0

    def open_invalid_response(
        request: Any,
        *,
        timeout: float,
        deadline: float,
    ) -> _FakeResponse:
        nonlocal download_count
        download_count += 1
        return _FakeResponse(b"not a pcd", request.full_url)

    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        open_invalid_response,
    )

    with pytest.raises(DreameLawnMowerPointCloudError) as captured:
        client._sync_download_app_map_point_cloud(
            0,
            0.02,
            0.001,
            10,
            1024,
        )

    assert captured.value.code == "point_cloud_download_invalid"
    assert download_count == 1


def test_download_point_cloud_waits_for_changed_mova_fixed_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _mova_client()
    calls: list[dict[str, Any]] = []
    responses = iter(
        [
            {"r": 0, "d": {"name": ["private/existing-map.bin"]}},
            {"r": 0},
            {"r": 0, "d": {"name": ["private/existing-map.bin"]}},
            {"r": 0, "d": {"name": ["private/existing-map.bin"]}},
            {"r": 0, "d": {"name": ["private/existing-map.bin"]}},
        ]
    )

    def call_app_action(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(payload)
        return next(responses)

    signed_url = "https://downloads.example.invalid/object?private=signature"
    cloud = type(
        "Cloud",
        (),
        {"get_interim_file_url": lambda self, name, **kwargs: signed_url},
    )()
    baseline_content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    refreshed_content = _binary_pcd((4.0, 5.0, 6.0, 0x654321))
    downloads = iter(
        [
            baseline_content,
            baseline_content,
            baseline_content,
            refreshed_content,
        ]
    )
    client._sync_call_app_action = call_app_action
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            next(downloads),
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(0, 5, 0.1, 10, 1024)

    # The object never gets renamed (MOVA behavior), so unchanged valid
    # baseline bytes are ignored until the overwritten content is observable.
    assert calls == [
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "a", "p": 0, "o": 10, "d": {"idx": 0}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
    ]
    assert result.map_index == 0
    assert result.content == refreshed_content
    assert result.metadata.points == 1
    assert not hasattr(result, "url")
    assert not hasattr(result, "object_name")


def test_download_point_cloud_accepts_new_mova_object_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _mova_client()
    responses = iter(
        [
            {"r": 0, "d": {"name": ["private/existing-map.bin"]}},
            {"r": 0},
            {"r": 0, "d": {"name": ["private/existing-map.bin"]}},
        ]
    )
    client._sync_call_app_action = lambda payload, **kwargs: next(responses)
    client._sync_get_cloud_protocol = lambda **kwargs: SimpleNamespace(
        get_interim_file_url=lambda name, **options: (
            "https://downloads.example.invalid/object"
        ),
    )
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    downloads = iter(
        [
            _FakeResponse(
                content,
                "https://downloads.example.invalid/object",
                etag='"baseline"',
            ),
            _FakeResponse(
                content,
                "https://downloads.example.invalid/object",
                etag='"refreshed"',
            ),
        ]
    )
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: next(downloads),
    )

    result = client._sync_download_app_map_point_cloud(0, 5, 0.1, 10, 1024)

    assert result.content == content
    assert result.metadata.points == 1


def test_download_point_cloud_retries_mova_fixed_object_until_valid_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _mova_client()
    calls: list[dict[str, Any]] = []
    responses = iter(
        [
            {"r": 0, "d": {"name": ["private/existing-map.bin"]}},
            {"r": 0},
            {"r": 0, "d": {"name": ["private/existing-map.bin"]}},
            {"r": 0, "d": {"name": ["private/existing-map.bin"]}},
            {"r": 0, "d": {"name": ["private/existing-map.bin"]}},
        ]
    )

    def call_app_action(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(payload)
        return next(responses)

    client._sync_call_app_action = call_app_action
    client._sync_get_cloud_protocol = lambda **kwargs: SimpleNamespace(
        get_interim_file_url=lambda name, **options: (
            "https://downloads.example.invalid/object"
        ),
    )
    baseline_content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    refreshed_content = _binary_pcd((4.0, 5.0, 6.0, 0x654321))
    downloads: Any = iter(
        [
            _FakeResponse(
                baseline_content,
                "https://downloads.example.invalid/object",
            ),
            TimeoutError(),
            _FakeResponse(
                b"partially overwritten",
                "https://downloads.example.invalid/object",
            ),
            _FakeResponse(
                refreshed_content,
                "https://downloads.example.invalid/object",
            ),
        ]
    )

    def open_response(request: Any, *, timeout: float, deadline: float) -> Any:
        result = next(downloads)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        open_response,
    )

    result = client._sync_download_app_map_point_cloud(0, 5, 0.1, 10, 1024)

    assert calls == [
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "a", "p": 0, "o": 10, "d": {"idx": 0}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
    ]
    assert result.content == refreshed_content
    assert result.metadata.points == 1


def test_download_point_cloud_accepts_dreame_bin_when_announcement_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    action_count = 0
    download_url_calls = 0
    property_calls = 0
    baseline_content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))
    refreshed_content = _binary_pcd((4.0, 5.0, 6.0, 0x654321))

    def call_app_action(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        nonlocal action_count
        action_count += 1
        if action_count == 1:
            return {"r": 0, "d": {"name": ["private/fixed-map.bin"]}}
        if action_count == 2:
            return {"r": 0}
        return {"r": 0, "d": {"name": ["private/fixed-map.bin"]}}

    def get_interim_file_url(name: str, **kwargs: Any) -> str:
        nonlocal download_url_calls
        download_url_calls += 1
        return "https://downloads.example.invalid/object"

    def get_properties(*args: Any, **kwargs: Any) -> None:
        nonlocal property_calls
        property_calls += 1

    client._sync_call_app_action = call_app_action
    client._sync_get_cloud_protocol = lambda **kwargs: SimpleNamespace(
        get_interim_file_url=get_interim_file_url,
        get_properties=get_properties,
    )
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)
    downloads = iter((baseline_content, refreshed_content))
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: _FakeResponse(
            next(downloads),
            request.full_url,
        ),
    )

    result = client._sync_download_app_map_point_cloud(0, 5, 0.1, 10, 1024)

    assert result.content == refreshed_content
    assert result.metadata.points == 1
    assert property_calls == 2
    assert download_url_calls == 2


def test_download_point_cloud_error_does_not_expose_signed_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_url = "https://downloads.example.invalid/object?secret=do-not-log"

    def fail_download(
        request: Any,
        *,
        timeout: float,
        deadline: float,
    ) -> None:
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


def test_download_point_cloud_stops_at_declared_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_url = "https://downloads.example.invalid/private-object"
    content = b"complete"
    response = _FakeResponse(content, signed_url)
    read_sizes: list[int] = []
    original_read = response.read

    def read(size: int) -> bytes:
        read_sizes.append(size)
        return original_read(size)

    response.read = read
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: response,
    )

    downloaded, _ = _internal_client_module._download_point_cloud_content(
        signed_url,
        timeout=10,
        max_bytes=1024,
    )

    assert downloaded == content
    assert read_sizes == [len(content)]


def test_download_point_cloud_rejects_truncated_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_url = "https://downloads.example.invalid/private-object"
    response = _FakeResponse(b"short", signed_url)
    response.headers.replace_header("Content-Length", "10")
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        lambda request, *, timeout, deadline: response,
    )

    with pytest.raises(
        DreameLawnMowerPointCloudError,
        match="ended before its declared size",
    ):
        _internal_client_module._download_point_cloud_content(
            signed_url,
            timeout=10,
            max_bytes=1024,
        )


def test_download_point_cloud_rejects_insecure_redirect_before_following() -> None:
    request = urllib.request.Request("https://downloads.example.invalid/private-object")
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
    url = "https://eu.example.invalid/dreame-user-iot/iotfile/getDownloadUrl"

    assert (
        _internal_protocol_module._cloud_request_log_value(
            url,
            '{"filename":"private/generated-map.pcd"}',
        )
        == "<redacted interim file payload>"
    )
    assert (
        _internal_protocol_module._cloud_request_log_value(
            url,
            '{"data":"https://downloads.example.invalid/object?secret=signature"}',
        )
        == "<redacted interim file payload>"
    )


def test_cloud_property_logs_redact_request_and_response() -> None:
    url = "https://eu.example.invalid/dreame-user-iot/iotstatus/props"
    private_name = "ali_dreame/private-device/generated-map.bin"

    assert (
        _internal_protocol_module._cloud_request_log_value(
            url,
            '{"did":"private-device","keys":"99.20"}',
        )
        == "<redacted cloud properties payload>"
    )
    assert (
        _internal_protocol_module._cloud_request_log_value(
            url,
            f'{{"data":[{{"key":"99.20","value":"{private_name}"}}]}}',
        )
        == "<redacted cloud properties payload>"
    )


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
        lambda request, *, timeout, deadline: response,
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
                "deadline": 101.0,
                "redact_response": True,
                "raise_on_api_error": True,
            }
        ]


def test_point_cloud_action_response_enforces_overall_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied_timeouts: list[float] = []
    raw = SimpleNamespace()
    raw._fp = SimpleNamespace(
        fp=SimpleNamespace(
            raw=SimpleNamespace(
                _sock=SimpleNamespace(settimeout=applied_timeouts.append)
            )
        )
    )
    raw.read1 = lambda size, *, decode_content: b'{"code":0}'
    response = SimpleNamespace(raw=raw, encoding="utf-8")
    monotonic_values = iter([100.2, 101.1])
    monkeypatch.setattr(
        _internal_protocol_module.time,
        "monotonic",
        lambda: next(monotonic_values, 101.1),
    )

    with pytest.raises(requests.exceptions.Timeout, match="timed out"):
        _internal_protocol_module._read_cloud_response_text_with_deadline(
            response,
            deadline=101.0,
        )

    assert applied_timeouts == pytest.approx([0.8])


def test_point_cloud_action_marks_dispatch_before_waiting_for_response() -> None:
    protocol_type = _internal_protocol_module.DreameMowerDreameHomeCloudProtocol
    cloud = object.__new__(protocol_type)
    cloud._request_lock = threading.RLock()
    cloud._strings = [f"value-{index}" for index in range(57)]
    cloud._country = "eu"
    cloud._host = "host.example.invalid"
    cloud._did = "device-1"
    cloud._id = 1
    cloud._key_expire = None
    cloud._secondary_key = None
    cloud._connected = True
    cloud._fail_count = 0
    cloud._ti = ""
    cloud._key = "key"
    events: list[str] = []
    response = SimpleNamespace(
        status_code=200,
        text=('{"code":0,"data":{"result":{"out":[{"value":{"r":0}}]}}}'),
    )

    cloud._session = SimpleNamespace(
        post=lambda url, **request_options: events.append("post") or response,
    )

    result = cloud.call_app_action(
        {"m": "a", "p": 0, "o": 10, "d": {"idx": 0}},
        retry_count=0,
        on_dispatch=lambda: events.append("dispatch"),
    )

    assert events == ["dispatch", "post"]
    assert result == {"out": [{"value": {"r": 0}}]}


def test_point_cloud_action_can_raise_safe_cloud_api_code() -> None:
    protocol_type = _internal_protocol_module.DreameMowerDreameHomeCloudProtocol
    cloud = object.__new__(protocol_type)
    cloud._request_lock = threading.RLock()
    cloud._strings = [f"value-{index}" for index in range(57)]
    cloud._host = "host.example.invalid"
    cloud._did = "device-1"
    cloud._id = 1
    cloud._api_call = lambda *args, **kwargs: {
        "code": 80001,
        "success": False,
        "data": None,
    }

    with pytest.raises(
        _internal_exceptions_module.DreameLawnMowerCloudAPIError
    ) as captured:
        cloud.call_app_action(
            {"m": "a", "p": 0, "o": 10, "d": {"idx": 0}},
            retry_count=0,
            redact_response=True,
            raise_on_api_error=True,
        )

    assert captured.value.code == 80001
    assert "80001" in str(captured.value)


def test_point_cloud_wraps_safe_cloud_api_rejection() -> None:
    client = _client()

    def rejected(*args: Any, **kwargs: Any) -> None:
        raise _internal_exceptions_module.DreameLawnMowerCloudAPIError(80001)

    client._sync_call_app_action = rejected

    with pytest.raises(DreameLawnMowerPointCloudError) as captured:
        client._sync_call_point_cloud_action(
            {"m": "a", "p": 0, "o": 10, "d": {"idx": 0}},
            operation="start point-cloud generation",
            deadline=time.monotonic() + 5,
            require_data=False,
        )

    assert captured.value.code == "point_cloud_mower_request_rejected"
    assert captured.value.stage == "mower_request"
    assert captured.value.vendor_error_code == 80001


def test_point_cloud_dispatch_waits_for_network_worker_slot() -> None:
    operation_slots = _internal_deadline_module._operation_slots
    acquired_slots = 0
    caller_started = threading.Event()
    events: list[str] = []
    result: list[Any] = []
    response = SimpleNamespace(status_code=200)
    session = SimpleNamespace(
        post=lambda url, **request_options: events.append("post") or response,
    )

    def post_with_deadline() -> None:
        caller_started.set()
        result.append(
            _internal_protocol_module._post_cloud_response(
                session,
                "https://cloud.example.invalid/action",
                {"timeout": 1, "stream": True},
                deadline=time.monotonic() + 1,
                on_dispatch=lambda: events.append("dispatch"),
            )
        )

    caller = threading.Thread(
        target=post_with_deadline,
    )

    try:
        for _ in range(_internal_deadline_module._MAX_ABANDONED_OPERATIONS):
            assert operation_slots.acquire(timeout=1)
            acquired_slots += 1
        caller.start()
        assert caller_started.wait(timeout=1)
        time.sleep(0.05)
        assert events == []

        operation_slots.release()
        acquired_slots -= 1
        caller.join(timeout=1)
    finally:
        for _ in range(acquired_slots):
            operation_slots.release()

    assert not caller.is_alive()
    assert events == ["dispatch", "post"]
    assert result == [response]


def test_point_cloud_401_reauthentication_uses_remaining_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol_type = _internal_protocol_module.DreameMowerDreameHomeCloudProtocol
    cloud = object.__new__(protocol_type)
    strings = [f"header-{index}" for index in range(53)]
    cloud._strings = strings
    cloud._country = "eu"
    cloud._ti = "ti"
    cloud._key = "key"
    cloud._key_expire = None
    cloud._secondary_key = "refresh"
    cloud._session = SimpleNamespace()
    cloud._connected = True
    cloud._fail_count = 0
    login_options: list[dict[str, Any]] = []
    cloud.login = lambda **kwargs: login_options.append(kwargs) or True
    response = SimpleNamespace(status_code=401, close=lambda: None)
    monkeypatch.setattr(
        _internal_protocol_module,
        "_post_cloud_response",
        lambda session, url, request_options, *, deadline: response,
    )
    monkeypatch.setattr(
        _internal_protocol_module,
        "_read_cloud_response_text_with_deadline",
        lambda response, *, deadline: "",
    )
    monkeypatch.setattr(
        _internal_protocol_module.time,
        "monotonic",
        lambda: 100.0,
    )

    assert (
        cloud.request(
            "https://cloud.example.invalid/action",
            "{}",
            retry_count=0,
            timeout=20,
            deadline=101.0,
        )
        is None
    )

    assert login_options == [{"timeout": pytest.approx(1.0), "deadline": 101.0}]


def test_pcd_validation_stops_at_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def monotonic() -> float:
        nonlocal calls
        calls += 1
        return 100.0 if calls < 4 else 101.0

    monkeypatch.setattr(
        _internal_point_cloud_module.time,
        "monotonic",
        monotonic,
    )

    with pytest.raises(
        DreameLawnMowerPointCloudError,
        match="validation timed out",
    ):
        parse_pcd_metadata(
            _binary_pcd(
                (1.0, 2.0, 3.0, 0x123456),
                (4.0, 5.0, 6.0, 0x654321),
            ),
            deadline=101.0,
        )

    assert calls >= 4


def test_point_cloud_action_log_redacts_private_object_response() -> None:
    private_response = {
        "data": {
            "result": {
                "out": [
                    {
                        "value": {
                            "r": 0,
                            "d": {"name": ["private/generated-map.pcd"]},
                        }
                    }
                ]
            }
        }
    }

    assert (
        _internal_protocol_module._app_action_response_log_value(
            private_response,
            redact=True,
        )
        == "<redacted app action response>"
    )
    assert (
        _internal_protocol_module._app_action_response_log_value(
            private_response,
            redact=False,
        )
        is private_response
    )


def test_ascii_point_cloud_rejects_oversized_row_before_splitting() -> None:
    content = _ascii_pcd(1).replace(
        b"1 2 3\n",
        (b"0 " * 10_000) + b"\n",
    )

    with pytest.raises(
        DreameLawnMowerPointCloudError,
        match="row exceeds the supported size",
    ):
        parse_pcd_metadata(content)


def test_ascii_row_iterator_rejects_oversized_row_before_slicing() -> None:
    class SliceGuard(bytes):
        def __getitem__(self, key: Any) -> Any:
            if isinstance(key, slice) and key.stop - key.start > 16:
                raise AssertionError("oversized row was sliced")
            return super().__getitem__(key)

    payload = SliceGuard((b"0 " * 100) + b"\n")

    with pytest.raises(
        DreameLawnMowerPointCloudError,
        match="row exceeds the supported size",
    ):
        list(
            _internal_point_cloud_module._iter_nonempty_ascii_rows(
                payload,
                max_row_bytes=16,
            )
        )


def test_ascii_point_cloud_rejects_attacker_controlled_scalar_count() -> None:
    content = _ascii_pcd(1)
    content = content.replace(b"FIELDS x y z\n", b"FIELDS x y z extra\n")
    content = content.replace(b"SIZE 4 4 4\n", b"SIZE 4 4 4 4\n")
    content = content.replace(b"TYPE F F F\n", b"TYPE F F F F\n")
    content = content.replace(b"COUNT 1 1 1\n", b"COUNT 1 1 1 1000000\n")
    content = content.replace(b"1 2 3\n", (b"0 " * 10_000) + b"\n")

    with pytest.raises(
        DreameLawnMowerPointCloudError,
        match="too many scalar values",
    ):
        parse_pcd_metadata(content)


def test_point_cloud_does_not_redownload_same_rejected_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    action_count = 0
    download_count = 0

    def call_app_action(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        nonlocal action_count
        action_count += 1
        if action_count == 1:
            return {"r": 0, "d": {"name": ["private/previous-map.pcd"]}}
        if action_count == 2:
            return {"r": 0}
        return {"r": 0, "d": {"name": ["private/rejected-map.pcd"]}}

    cloud = SimpleNamespace(
        get_interim_file_url=lambda name, **kwargs: (
            "https://downloads.example.invalid/object"
        )
    )

    def open_response(
        request: Any,
        *,
        timeout: float,
        deadline: float,
    ) -> _FakeResponse:
        nonlocal download_count
        download_count += 1
        return _FakeResponse(b"not a pcd", request.full_url)

    client._sync_call_app_action = call_app_action
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        open_response,
    )
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)

    with pytest.raises(DreameLawnMowerPointCloudError):
        client._sync_download_app_map_point_cloud(0, 0.01, 0.001, 10, 1024)

    assert action_count > 3
    assert download_count == 1


def test_point_cloud_retries_transient_download_for_same_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    action_count = 0
    download_count = 0
    content = _binary_pcd((1.0, 2.0, 3.0, 0x123456))

    def call_app_action(payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        nonlocal action_count
        action_count += 1
        if action_count == 1:
            return {"r": 0, "d": {"name": ["private/previous-map.pcd"]}}
        if action_count == 2:
            return {"r": 0}
        return {"r": 0, "d": {"name": ["private/generated-map.pcd"]}}

    def open_response(
        request: Any,
        *,
        timeout: float,
        deadline: float,
    ) -> _FakeResponse:
        nonlocal download_count
        download_count += 1
        if download_count == 1:
            raise urllib.error.URLError("temporary failure")
        return _FakeResponse(content, request.full_url)

    client._sync_call_app_action = call_app_action
    client._sync_get_cloud_protocol = lambda **kwargs: SimpleNamespace(
        get_interim_file_url=lambda name, **options: (
            "https://downloads.example.invalid/object"
        )
    )
    monkeypatch.setattr(
        _internal_client_module,
        "_open_point_cloud_response",
        open_response,
    )
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)

    result = client._sync_download_app_map_point_cloud(0, 5, 0.1, 10, 1024)

    assert result.content == content
    assert download_count == 2


def test_point_cloud_header_receive_observes_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()

    class BlockingOpener:
        def open(self, request: Any, *, timeout: float) -> _FakeResponse:
            release.wait(5)
            return _FakeResponse(b"", request.full_url)

    monkeypatch.setattr(
        _internal_client_module.urllib.request,
        "build_opener",
        lambda *handlers: BlockingOpener(),
    )
    request = urllib.request.Request("https://downloads.example.invalid/private-object")
    started = time.monotonic()
    try:
        with pytest.raises(DreameLawnMowerPointCloudError, match="timed out"):
            _internal_client_module._open_point_cloud_response(
                request,
                timeout=5,
                deadline=time.monotonic() + 0.05,
            )
    finally:
        release.set()

    assert time.monotonic() - started < 1


def test_cloud_header_receive_observes_absolute_deadline() -> None:
    release = threading.Event()

    class BlockingSession:
        def post(self, url: str, **options: Any) -> Any:
            release.wait(5)
            return SimpleNamespace(close=lambda: None)

    started = time.monotonic()
    try:
        with pytest.raises(requests.exceptions.Timeout, match="timed out"):
            _internal_protocol_module._post_cloud_response(
                BlockingSession(),
                "https://cloud.example.invalid/action",
                {"timeout": 5, "stream": True},
                deadline=time.monotonic() + 0.05,
            )
    finally:
        release.set()

    assert time.monotonic() - started < 1


def test_deadline_slot_is_released_when_late_response_close_fails() -> None:
    for _ in range(4):
        release = threading.Event()
        close_called = threading.Event()

        class BadResponse:
            def __init__(self, close_event: threading.Event) -> None:
                self.close_event = close_event

            def close(self) -> None:
                self.close_event.set()
                raise RuntimeError("cleanup failed")

        def operation(
            release_event: threading.Event = release,
            close_event: threading.Event = close_called,
        ) -> BadResponse:
            release_event.wait(5)
            return BadResponse(close_event)

        try:
            with pytest.raises(
                _internal_deadline_module.DeadlineExceededError,
            ):
                _internal_deadline_module.run_with_deadline(
                    operation,
                    deadline=time.monotonic() + 0.01,
                )
        finally:
            release.set()
        assert close_called.wait(1)

    assert (
        _internal_deadline_module.run_with_deadline(
            lambda: "available",
            deadline=time.monotonic() + 1,
        )
        == "available"
    )


def test_deadline_slot_is_released_when_worker_thread_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_start(thread: threading.Thread) -> None:
        raise RuntimeError("thread capacity exhausted")

    with monkeypatch.context() as patcher:
        patcher.setattr(
            _internal_deadline_module.threading.Thread,
            "start",
            fail_start,
        )
        for _ in range(4):
            with pytest.raises(RuntimeError, match="capacity exhausted"):
                _internal_deadline_module.run_with_deadline(
                    lambda: "not started",
                    deadline=time.monotonic() + 1,
                )

    assert (
        _internal_deadline_module.run_with_deadline(
            lambda: "available",
            deadline=time.monotonic() + 1,
        )
        == "available"
    )


def test_point_cloud_generation_deadline_includes_executor_queue_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    queued = asyncio.Event()

    async def wait_in_executor_queue(
        function: Any,
        *args: Any,
    ) -> Any:
        queued.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        _internal_client_facade_module.asyncio,
        "to_thread",
        wait_in_executor_queue,
    )

    async def run() -> None:
        with pytest.raises(
            DreameLawnMowerPointCloudError,
            match="generation timed out",
        ) as captured:
            await client.async_download_app_map_point_cloud(timeout=0.01)
        assert captured.value.code == "point_cloud_timeout"
        assert captured.value.stage == "generation"
        assert captured.value.timeout_seconds == 0.01
        assert captured.value.retryable is True
        assert queued.is_set()

    asyncio.run(run())


def test_stored_point_cloud_preflight_has_a_separate_time_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    captured: dict[str, Any] = {}

    async def capture_to_thread(function: Any, *args: Any) -> Any:
        captured["function"] = function
        captured["args"] = args
        return SimpleNamespace(content=b"pcd")

    monkeypatch.setattr(
        _internal_client_facade_module.asyncio,
        "to_thread",
        capture_to_thread,
    )

    async def run() -> None:
        result = await client.async_download_app_map_point_cloud(
            timeout=5,
            allow_stored=True,
        )
        assert result.content

    started = time.monotonic()
    asyncio.run(run())

    assert captured["function"] == client._sync_download_app_map_point_cloud
    assert captured["args"][-1] is True
    assert captured["args"][-2] - started == pytest.approx(17, abs=0.25)


def test_stored_point_cloud_fallback_receives_full_generation_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    clock = [100.0]
    captured_deadlines: list[float] = []
    cloud = SimpleNamespace(get_interim_file_url=lambda name: None)
    client._sync_get_cloud_protocol = lambda **kwargs: cloud

    def probe(*args: Any, **kwargs: Any) -> tuple[bool, None, None]:
        clock[0] = 106.0
        return False, None, None

    def stop_at_baseline(payload: dict[str, Any], **kwargs: Any) -> Any:
        captured_deadlines.append(kwargs["deadline"])
        raise RuntimeError("stop after deadline capture")

    client._sync_get_announced_point_cloud_object = probe
    client._sync_call_point_cloud_action = stop_at_baseline
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])

    with pytest.raises(RuntimeError, match="deadline capture"):
        client._sync_download_app_map_point_cloud(
            0,
            5,
            0.1,
            10,
            1024,
            deadline=112.0,
            allow_stored=True,
        )

    assert captured_deadlines == [111.0]


def test_failed_cached_point_cloud_preserves_full_generation_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    clock = [100.0]
    captured_deadlines: list[float] = []
    cloud = SimpleNamespace(get_interim_file_url=lambda name: None)
    client._sync_get_cloud_protocol = lambda **kwargs: cloud
    client._sync_update_app_map_inventory_identity(
        [
            {
                "idx": 0,
                "current": True,
                "created": True,
                "info": {"hash": "map-hash-1", "size": 123},
            }
        ]
    )
    client._latest_app_map_object_names = ("private/stale-map.bin",)
    client._latest_app_map_object_inventory_identity = (
        client._latest_app_map_inventory_identity
    )

    def fail_cached_download(*args: Any, **kwargs: Any) -> None:
        clock[0] = 105.0
        return None

    def probe(*args: Any, **kwargs: Any) -> tuple[bool, None, None]:
        clock[0] = 107.0
        return False, None, None

    def stop_at_baseline(payload: dict[str, Any], **kwargs: Any) -> Any:
        captured_deadlines.append(kwargs["deadline"])
        raise RuntimeError("stop after deadline capture")

    client._sync_try_download_stored_point_cloud = fail_cached_download
    client._sync_get_announced_point_cloud_object = probe
    client._sync_call_point_cloud_action = stop_at_baseline
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])

    with pytest.raises(RuntimeError, match="deadline capture"):
        client._sync_download_app_map_point_cloud(
            0,
            5,
            0.1,
            10,
            1024,
            deadline=117.0,
            allow_stored=True,
        )

    assert captured_deadlines == [112.0]


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
            "deadline": 101.0,
        }
    ]


def test_interim_file_protocol_forwards_absolute_deadline() -> None:
    protocol_type = _internal_protocol_module.DreameMowerDreameHomeCloudProtocol
    cloud = object.__new__(protocol_type)
    strings = [""] * 56
    strings[21] = "country"
    strings[23] = "iot"
    strings[35] = "model"
    strings[39] = "file"
    strings[40] = "filename"
    strings[55] = "download"
    cloud._strings = strings
    cloud._did = "device-1"
    cloud._model = "dreame.mower.g2408"
    cloud._country = "eu"
    options: list[dict[str, Any]] = []

    def api_call(*args: Any, **kwargs: Any) -> dict[str, str]:
        options.append(kwargs)
        return {"data": "https://downloads.example.invalid/object"}

    cloud._api_call = api_call

    assert (
        cloud.get_interim_file_url(
            "private/generated-map.pcd",
            retry_count=0,
            timeout=0.8,
            deadline=101.0,
        )
        == "https://downloads.example.invalid/object"
    )
    assert options == [
        {
            "retry_count": 0,
            "timeout": 0.8,
            "deadline": 101.0,
        }
    ]


def test_cloud_property_lookup_forwards_absolute_deadline() -> None:
    protocol_type = _internal_protocol_module.DreameMowerDreameHomeCloudProtocol
    cloud = object.__new__(protocol_type)
    strings = [""] * 42
    strings[23] = "iot"
    strings[25] = "status"
    strings[41] = "props"
    cloud._strings = strings
    cloud._did = "device-1"
    options: list[dict[str, Any]] = []

    def api_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        options.append(kwargs)
        return {"data": [{"key": "99.20", "value": "private/map.bin"}]}

    cloud._api_call = api_call

    result = cloud.get_properties(
        "99.20",
        retry_count=0,
        timeout=0.8,
        deadline=101.0,
    )

    assert result == [{"key": "99.20", "value": "private/map.bin"}]
    assert options == [
        {
            "retry_count": 0,
            "timeout": 0.8,
            "deadline": 101.0,
        }
    ]


def test_point_cloud_preflight_uses_remaining_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    login_options: list[dict[str, Any]] = []
    info_options: list[dict[str, Any]] = []
    action_options: list[dict[str, Any]] = []

    class Cloud:
        logged_in = False
        _host = None

        def login(self, **kwargs: Any) -> bool:
            login_options.append(kwargs)
            self.logged_in = True
            return True

        def get_device_info_v2(self, lang: str, **kwargs: Any) -> None:
            info_options.append(kwargs)
            self._host = "host.example.invalid"

        def call_app_action(
            self,
            payload: dict[str, Any],
            **kwargs: Any,
        ) -> dict[str, Any]:
            action_options.append(kwargs)
            return {"out": [{"value": {"r": 0, "d": {}}}]}

    cloud = Cloud()
    client._device = SimpleNamespace(
        _protocol=SimpleNamespace(cloud=cloud),
    )
    monotonic_values = iter([100.1, 100.2])
    monkeypatch.setattr(
        client_module.time,
        "monotonic",
        lambda: next(monotonic_values, 100.2),
    )

    client._sync_call_app_action(
        {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
        retry_count=0,
        timeout=0.9,
        deadline=101.0,
        redact_response=True,
    )

    assert login_options == [{"timeout": pytest.approx(0.9), "deadline": 101.0}]
    assert info_options == [
        {
            "retry_count": 0,
            "timeout": pytest.approx(0.8),
            "deadline": 101.0,
        }
    ]
    assert action_options == [
        {
            "siid": 2,
            "aiid": 50,
            "retry_count": 0,
            "timeout": pytest.approx(0.8),
            "deadline": 101.0,
            "redact_response": True,
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
    client._sync_get_cloud_protocol = lambda **kwargs: type(
        "Cloud",
        (),
        {"get_interim_file_url": lambda self, name, **kwargs: None},
    )()
    monkeypatch.setattr(client_module.time, "sleep", lambda _: None)

    with pytest.raises(
        DreameLawnMowerPointCloudError,
        match="could not read the generated point-cloud object state",
    ) as captured:
        client._sync_download_app_map_point_cloud(0, 5, 0.1, 10, 1024)
    assert captured.value.code == "point_cloud_mower_response_invalid"
    assert captured.value.stage == "mower_response"


@pytest.mark.parametrize(
    ("map_index", "timeout", "poll_interval", "download_timeout", "max_bytes"),
    [
        (-1, 5, 0.1, 10, 1024),
        (0, 0, 0.1, 10, 1024),
        (0, 5, 0, 10, 1024),
        (0, 5, 0.1, 0, 1024),
        (0, 5, 0.1, 10, 0),
    ],
)
def test_download_point_cloud_classifies_invalid_request_values(
    map_index: int,
    timeout: float,
    poll_interval: float,
    download_timeout: float,
    max_bytes: int,
) -> None:
    client = _client()

    with pytest.raises(DreameLawnMowerPointCloudError) as captured:
        client._sync_download_app_map_point_cloud(
            map_index,
            timeout,
            poll_interval,
            download_timeout,
            max_bytes,
        )

    assert captured.value.code == "point_cloud_invalid_request"
    assert captured.value.stage == "request"
    assert captured.value.retryable is False
