"""Regression checks for point-cloud generation, download, and validation."""

from __future__ import annotations

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

_internal_client_module = load_internal_module("client")
_internal_point_cloud_module = load_internal_module("point_cloud")
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
            "get_interim_file_url": lambda self, name, **kwargs: (
                get_interim_file_url(name, **kwargs)
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

    assert cloud.request(
        "https://cloud.example.invalid/action",
        "{}",
        retry_count=0,
        timeout=20,
        deadline=101.0,
    ) is None

    assert login_options == [
        {"timeout": pytest.approx(1.0), "deadline": 101.0}
    ]


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

    assert _internal_protocol_module._app_action_response_log_value(
        private_response,
        redact=True,
    ) == "<redacted app action response>"
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
    request = urllib.request.Request(
        "https://downloads.example.invalid/private-object"
    )
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

    assert cloud.get_interim_file_url(
        "private/generated-map.pcd",
        retry_count=0,
        timeout=0.8,
        deadline=101.0,
    ) == "https://downloads.example.invalid/object"
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

    assert login_options == [
        {"timeout": pytest.approx(0.9), "deadline": 101.0}
    ]
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
    ):
        client._sync_download_app_map_point_cloud(0, 5, 0.1, 10, 1024)
