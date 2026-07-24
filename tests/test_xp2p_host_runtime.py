"""Contracts for the self-managed Linux XP2P runtime."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import struct
import subprocess
import threading
from dataclasses import replace

import pytest
import requests

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    xp2p_host_runtime,
    xp2p_host_worker_blob,
    xp2p_runtime_bootstrap,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.lan_video import (
    DreameLawnMowerLanVideoEndpoint,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerXp2pLiveStreamRequest,
    DreameLawnMowerXp2pLiveStreamSession,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.xp2p_config import (
    XP2P_PROTOCOL_AUTO,
    XP2P_PROTOCOL_TCP,
    DreameLawnMowerXp2pDeviceConfig,
    fetch_xp2p_device_config,
    normalize_xp2p_device_config,
    resolve_xp2p_device_config,
)

DreameLawnMowerXp2pHostAssets = xp2p_host_runtime.DreameLawnMowerXp2pHostAssets
_encode_request = xp2p_host_runtime._encode_request
_decode_success_payload = xp2p_host_runtime._decode_success_payload
_startup_response_timeout = xp2p_host_runtime._startup_response_timeout
_stun_servers = xp2p_host_runtime._stun_servers
WORKER_GZIP_BASE64 = xp2p_host_worker_blob.WORKER_GZIP_BASE64
WORKER_GZIP_SHA256 = xp2p_host_worker_blob.WORKER_GZIP_SHA256
WORKER_SHA256 = xp2p_host_worker_blob.WORKER_SHA256


def _inputs() -> DreameLawnMowerCameraStreamRuntimeInputs:
    return DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="did-1",
        channel_id="product-1/device-1",
        product_id="product-1",
        device_name="device-1",
        p2p_info="p2p-info-1",
        secret_id="secret-id-1",
        secret_key="secret-key-1",
        app_id="app-key-1",
        app_secret="app-secret-1",
    )


class _Response:
    status_code = 200

    def json(self):
        return {
            "code": 0,
            "data": {
                "Config": {
                    "EnableCrossStunTurn": 1,
                    "StunHost": "stun.example.test",
                    "StunIP": "192.0.2.1",
                    "StunPort": 20003,
                    "Protocol": "TCP",
                }
            },
        }


class _Client:
    def __init__(self) -> None:
        self.payload = None

    def post(self, _url, *, json, timeout):
        self.payload = json
        assert timeout == 3
        return _Response()


class _UnavailableClient:
    def post(self, _url, *, json, timeout):
        raise requests.ConnectionError("offline")


def test_tencent_device_config_request_is_signed_and_normalized() -> None:
    client = _Client()

    config = fetch_xp2p_device_config(
        _inputs(),
        client=client,
        timeout=3,
        timestamp=123,
        nonce=456,
        request_id="request-1",
    )

    assert config == DreameLawnMowerXp2pDeviceConfig(
        server="stun.example.test",
        ip="192.0.2.1",
        port=20003,
        protocol_type=XP2P_PROTOCOL_TCP,
        cross=True,
    )
    assert client.payload["Action"] == "AppDescribeConfigureDeviceP2P"
    assert client.payload["AppKey"] == "app-key-1"
    assert client.payload["ProductId"] == "product-1"
    assert client.payload["DeviceName"] == "device-1"
    assert client.payload["Signature"]
    assert "app-secret-1" not in client.payload.values()


def test_tencent_device_config_falls_back_like_the_sdk_when_unreachable() -> None:
    config = resolve_xp2p_device_config(_inputs(), client=_UnavailableClient())

    assert config.server == ""
    assert config.ip == ""
    assert config.port == 20002
    assert config.protocol_type == XP2P_PROTOCOL_TCP
    assert config.cross is False


def test_tencent_device_config_defaults_missing_protocol_to_proven_tcp() -> None:
    config = normalize_xp2p_device_config({"StunHost": "stun.example.test"})

    assert config.protocol_type == XP2P_PROTOCOL_TCP


def test_tencent_device_config_preserves_explicit_auto_protocol() -> None:
    config = normalize_xp2p_device_config({"Protocol": "AUTO"})

    assert config.protocol_type == XP2P_PROTOCOL_AUTO


def test_host_runtime_uses_fetched_regional_stun_endpoints() -> None:
    config = DreameLawnMowerXp2pDeviceConfig(
        server="stun.example.test",
        ip="192.0.2.1",
        port=20003,
    )

    assert _stun_servers(config) == (
        "stun.example.test:20003",
        "192.0.2.1:20003",
    )


def test_host_runtime_keeps_known_stun_fallback_without_fetched_endpoint() -> None:
    assert _stun_servers(DreameLawnMowerXp2pDeviceConfig()) == (
        "43.158.113.38:20002",
    )
    assert _stun_servers(
        DreameLawnMowerXp2pDeviceConfig(server="stun.example.test", port=0)
    ) == ("stun.example.test:20002",)


def test_host_runtime_timeout_covers_worker_retry_budgets() -> None:
    assert _startup_response_timeout() == 545.0
    assert xp2p_host_runtime.DEFAULT_XP2P_HOST_STARTUP_TIMEOUT == 545.0
    assert _startup_response_timeout(
        command_timeout_us=1_000_000,
        device_status_attempts=2,
        device_status_retry_interval=0.5,
        delegate_attempts=3,
        delegate_retry_interval=0.25,
        minimum=0,
    ) == 9.75
    assert (
        _startup_response_timeout(
            include_device_status=False,
            minimum=0,
        )
        == 65.0
    )


def test_embedded_worker_matches_reproducible_hashes() -> None:
    compressed = base64.b64decode(WORKER_GZIP_BASE64, validate=True)
    worker = gzip.decompress(compressed)

    assert hashlib.sha256(compressed).hexdigest() == WORKER_GZIP_SHA256
    assert hashlib.sha256(worker).hexdigest() == WORKER_SHA256
    assert worker.startswith(b"\x7fELF")


def test_host_worker_success_payload_reports_direct_or_relay_route() -> None:
    assert _decode_success_payload("62\nhttp://127.0.0.1/stream-62.flv") == (
        62,
        "http://127.0.0.1/stream-62.flv",
    )
    assert _decode_success_payload("63\nhttp://127.0.0.1/stream-63.flv") == (
        63,
        "http://127.0.0.1/stream-63.flv",
    )
    assert _decode_success_payload("0\nhttp://127.0.0.1/unknown.flv") == (
        None,
        "http://127.0.0.1/unknown.flv",
    )


def test_host_worker_accepts_legacy_url_only_success_payload() -> None:
    assert _decode_success_payload("http://127.0.0.1/legacy.flv") == (
        None,
        "http://127.0.0.1/legacy.flv",
    )


def test_host_runtime_refreshes_authoritative_relay_mode(tmp_path) -> None:
    payload = b"63"

    class _Process:
        stdin = io.BytesIO()
        stdout = io.BytesIO(b"DXR1" + struct.pack("!II", 0, len(payload)) + payload)

        @staticmethod
        def poll():
            return None

    runtime = xp2p_host_runtime.DreameLawnMowerXp2pHostRuntime(
        DreameLawnMowerXp2pHostAssets(
            worker_path=tmp_path / "worker",
            linker_path=tmp_path / "linker",
            library_path=tmp_path / "library",
            library_search_paths=(tmp_path,),
        )
    )
    process = _Process()
    session = DreameLawnMowerXp2pLiveStreamSession(
        service_id="product/device",
        stream_url="http://127.0.0.1/stream.flv",
        runner_process=process,
    )

    assert runtime.refresh_stream_link_mode(session) == 63
    assert session.stream_link_mode == 63
    assert session.as_dict()["sdk_stream_network_type"] == 63
    assert session.as_dict()["stream_route"] == "unknown"
    assert process.stdin.getvalue() == b"Q"


def test_host_response_reader_thread_is_daemon() -> None:
    started = threading.Event()
    release = threading.Event()

    class _BlockingStream:
        def read(self, _length: int) -> bytes:
            started.set()
            release.wait(timeout=2.0)
            return b""

    before = frozenset(threading.enumerate())
    try:
        with pytest.raises(
            xp2p_host_runtime.DreameLawnMowerVideoRuntimeError,
            match="timed out",
        ):
            xp2p_host_runtime._read_response(_BlockingStream(), timeout=0.1)
        assert started.is_set()
        readers = [
            thread
            for thread in threading.enumerate()
            if thread not in before and thread.name == "dreame-xp2p-host-response"
        ]
        assert len(readers) == 1
        assert readers[0].daemon is True
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread not in before and thread.name == "dreame-xp2p-host-response":
                thread.join(timeout=1.0)


def test_host_command_keeps_mower_secrets_out_of_argv(tmp_path) -> None:
    assets = DreameLawnMowerXp2pHostAssets(
        worker_path=tmp_path / "worker",
        linker_path=tmp_path / "linker64",
        library_path=tmp_path / "libiot_video_demo.so",
        library_search_paths=(tmp_path / "lib",),
        qemu_path=tmp_path / "qemu-aarch64-static",
    )
    request = DreameLawnMowerXp2pLiveStreamRequest.from_runtime_inputs(_inputs())
    payload = _encode_request(
        assets,
        tmp_path / "stun.txt",
        request,
        DreameLawnMowerXp2pDeviceConfig(),
        command_timeout_us=123,
        device_status_attempts=4,
        device_status_retry_interval=0.5,
        delegate_attempts=5,
        delegate_retry_interval=0.25,
    )

    command = " ".join(assets.command())
    assert "p2p-info-1" not in command
    assert "secret-id-1" not in command
    assert "secret-key-1" not in command
    assert payload.startswith(b"DXP1")
    assert struct.unpack("!I", payload[4:8])[0] == 20
    assert b"p2p-info-1" in payload
    assert b"secret-key-1" in payload


def test_host_lan_request_omits_cloud_credentials_and_encodes_direct_endpoint(
    tmp_path,
) -> None:
    assets = DreameLawnMowerXp2pHostAssets(
        worker_path=tmp_path / "worker",
        linker_path=tmp_path / "linker64",
        library_path=tmp_path / "libiot_video_demo.so",
        library_search_paths=(tmp_path / "lib",),
    )
    request = DreameLawnMowerXp2pLiveStreamRequest.from_lan_runtime_inputs(_inputs())
    endpoint = DreameLawnMowerLanVideoEndpoint(
        product_id="product-1",
        device_name="device-1",
        address="192.0.2.25",
        port=9000,
        response_version="2.4",
    )

    payload = _encode_request(
        assets,
        None,
        request,
        None,
        transport="lan",
        endpoint=endpoint,
        command_timeout_us=123,
        device_status_attempts=4,
        device_status_retry_interval=0.5,
        delegate_attempts=5,
        delegate_retry_interval=0.25,
    )
    fields = _request_fields(payload)

    assert fields[6:9] == [b"", b"", b""]
    assert fields[9].startswith(b"ipc.flv?action=live")
    assert b"_crypto=off" in fields[9]
    assert fields[10] == b""
    assert fields[-3:] == [b"lan", b"192.0.2.25", b"9000"]


def _request_fields(payload: bytes) -> list[bytes]:
    count = struct.unpack("!I", payload[4:8])[0]
    offset = 8
    fields = []
    for _index in range(count):
        length = struct.unpack("!I", payload[offset : offset + 4])[0]
        offset += 4
        fields.append(payload[offset : offset + length])
        offset += length
    return fields


def test_host_runtime_does_not_inherit_home_assistant_environment(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("HOME_ASSISTANT_SECRET", "must-not-reach-worker")
    emulated = DreameLawnMowerXp2pHostAssets(
        worker_path=tmp_path / "worker",
        linker_path=tmp_path / "linker64",
        library_path=tmp_path / "libiot_video_demo.so",
        library_search_paths=(tmp_path / "lib",),
        qemu_path=tmp_path / "qemu-aarch64-static",
    )
    native = DreameLawnMowerXp2pHostAssets(
        worker_path=tmp_path / "worker",
        linker_path=tmp_path / "linker64",
        library_path=tmp_path / "libiot_video_demo.so",
        library_search_paths=(tmp_path / "lib", tmp_path / "lib" / "bionic"),
    )

    assert emulated.environment() == {}
    assert native.environment() == {
        "LD_LIBRARY_PATH": native.library_search_path,
    }


def test_host_runtime_reports_abnormal_worker_exit_without_secrets(
    monkeypatch,
    tmp_path,
) -> None:
    long_secret = "s" * 600

    class _BrokenStdin:
        @staticmethod
        def write(_payload):
            raise BrokenPipeError(long_secret)

        @staticmethod
        def flush():
            raise AssertionError("flush should not follow a failed write")

    class _CrashedProcess:
        stdin = _BrokenStdin()
        stdout = io.BytesIO()
        stderr = io.BytesIO(
            b"x"
            + long_secret.encode()
            + b"y" * 340
            + b" /config/.storage/dreame-xp2p/qemu-aarch64-static\n"
        )

        @staticmethod
        def poll():
            return -11

        @staticmethod
        def wait(*, timeout):
            assert timeout == 0.2
            return -11

        @staticmethod
        def terminate():
            raise AssertionError("an exited worker must not be terminated")

    assets = DreameLawnMowerXp2pHostAssets(
        worker_path=tmp_path / "worker",
        linker_path=tmp_path / "linker64",
        library_path=tmp_path / "libiot_video_demo.so",
        library_search_paths=(tmp_path / "lib",),
    )
    runtime = xp2p_host_runtime.DreameLawnMowerXp2pHostRuntime(
        assets,
        config_fetcher=lambda _inputs: DreameLawnMowerXp2pDeviceConfig(),
    )
    monkeypatch.setattr(
        DreameLawnMowerXp2pHostAssets,
        "validate",
        lambda _self: None,
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: _CrashedProcess(),
    )

    with pytest.raises(
        xp2p_host_runtime.DreameLawnMowerVideoRuntimeError,
    ) as caught:
        runtime.start_live_stream(replace(_inputs(), secret_key=long_secret))

    message = str(caught.value)
    assert "stage=request_write" in message
    assert "exception=BrokenPipeError" in message
    assert "signal=11" in message
    assert long_secret not in message
    assert "/config/" not in message
    assert "[redacted]" in message
    assert "[redacted-path]" in message
    assert runtime.last_failure is not None
    assert runtime.last_failure["stage"] == "request_write"
    assert runtime.last_failure["returncode"] == -11
    native_trace = runtime.last_failure["native_trace"]
    assert "[redacted]" in native_trace
    assert long_secret[-64:] not in native_trace
    assert "[redacted-path]" in native_trace
    assert "/config/" not in native_trace


def test_host_runtime_preserves_context_when_worker_closes_response(
    monkeypatch,
    tmp_path,
) -> None:
    class _CrashedProcess:
        stdin = io.BytesIO()
        stdout = io.BytesIO()
        stderr = io.BytesIO(b"xp2p-worker: optional configuration applied\n")
        returncode = None

        @classmethod
        def poll(cls):
            return cls.returncode

        @classmethod
        def wait(cls, *, timeout):
            assert timeout == 0.2
            cls.returncode = -6
            return cls.returncode

        @classmethod
        def terminate(cls):
            raise AssertionError("an exited worker must not be terminated")

    runtime = xp2p_host_runtime.DreameLawnMowerXp2pHostRuntime(
        DreameLawnMowerXp2pHostAssets(
            worker_path=tmp_path / "worker",
            linker_path=tmp_path / "linker64",
            library_path=tmp_path / "libiot_video_demo.so",
            library_search_paths=(tmp_path / "lib",),
        ),
        config_fetcher=lambda _inputs: DreameLawnMowerXp2pDeviceConfig(),
    )
    monkeypatch.setattr(
        DreameLawnMowerXp2pHostAssets,
        "validate",
        lambda _self: None,
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *_args, **_kwargs: _CrashedProcess(),
    )

    with pytest.raises(
        xp2p_host_runtime.DreameLawnMowerVideoRuntimeError,
    ) as caught:
        runtime.start_live_stream(_inputs())

    message = str(caught.value)
    assert "XP2P host worker closed its response pipe" in message
    assert "stage=response_wait" in message
    assert "signal=6" in message
    assert "native=xp2p-worker: optional configuration applied" in message
    assert runtime.last_failure == {
        "stage": "response_wait",
        "exception": "DreameLawnMowerVideoRuntimeError",
        "returncode": -6,
        "exit": "signal=6",
        "native_trace": "xp2p-worker: optional configuration applied",
    }


@pytest.mark.parametrize(
    "start_method",
    ("start_live_stream", "start_lan_stream"),
)
def test_host_runtime_clears_previous_failure_before_input_validation(
    monkeypatch,
    tmp_path,
    start_method,
) -> None:
    runtime = xp2p_host_runtime.DreameLawnMowerXp2pHostRuntime(
        DreameLawnMowerXp2pHostAssets(
            worker_path=tmp_path / "worker",
            linker_path=tmp_path / "linker64",
            library_path=tmp_path / "libiot_video_demo.so",
            library_search_paths=(tmp_path / "lib",),
        )
    )
    runtime.last_failure = {
        "stage": "response_wait",
        "exit": "signal=11",
    }
    monkeypatch.setattr(
        DreameLawnMowerXp2pHostAssets,
        "validate",
        lambda _self: None,
    )

    with pytest.raises(
        xp2p_host_runtime.DreameLawnMowerVideoRuntimeError,
        match="missing .* fields: product_id",
    ):
        getattr(runtime, start_method)(replace(_inputs(), product_id=None))

    assert runtime.last_failure is None


def test_runtime_bootstrap_repairs_file_shaped_cache(
    monkeypatch,
    tmp_path,
) -> None:
    root = tmp_path / "xp2p"
    root.mkdir()
    runtime_path = root / (
        f"runtime-v{xp2p_runtime_bootstrap.RUNTIME_LAYOUT_VERSION}-x86_64"
    )
    runtime_path.write_text("interrupted install", encoding="utf-8")

    def _assets(path):
        return DreameLawnMowerXp2pHostAssets(
            worker_path=path / "bin" / "dreame-xp2p-host-runner",
            linker_path=path / "bin" / "linker64",
            library_path=path / "lib" / "libiot_video_demo.so",
            library_search_paths=(path / "lib",),
            qemu_path=path / "bin" / "qemu-aarch64-static",
        )

    def _validated(path, _architecture):
        if path == runtime_path and path.is_file():
            return None
        return _assets(path)

    monkeypatch.setattr(xp2p_runtime_bootstrap, "_validated_assets", _validated)
    monkeypatch.setattr(
        xp2p_runtime_bootstrap,
        "_install_runtime",
        lambda *_args, **_kwargs: None,
    )

    assets = xp2p_runtime_bootstrap.ensure_xp2p_host_runtime(
        root,
        machine="x86_64",
    )

    assert runtime_path.is_dir()
    assert assets.worker_path == runtime_path / "bin" / "dreame-xp2p-host-runner"
