"""Contracts for the self-managed Linux XP2P runtime."""

from __future__ import annotations

import base64
import gzip
import hashlib
import struct

import requests

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    xp2p_host_runtime,
    xp2p_host_worker_blob,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerXp2pLiveStreamRequest,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.xp2p_config import (
    XP2P_PROTOCOL_TCP,
    DreameLawnMowerXp2pDeviceConfig,
    fetch_xp2p_device_config,
    resolve_xp2p_device_config,
)

DreameLawnMowerXp2pHostAssets = xp2p_host_runtime.DreameLawnMowerXp2pHostAssets
_encode_request = xp2p_host_runtime._encode_request
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


def test_embedded_worker_matches_reproducible_hashes() -> None:
    compressed = base64.b64decode(WORKER_GZIP_BASE64, validate=True)
    worker = gzip.decompress(compressed)

    assert hashlib.sha256(compressed).hexdigest() == WORKER_GZIP_SHA256
    assert hashlib.sha256(worker).hexdigest() == WORKER_SHA256
    assert worker.startswith(b"\x7fELF")


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
    assert struct.unpack("!I", payload[4:8])[0] == 17
    assert b"p2p-info-1" in payload
    assert b"secret-key-1" in payload
