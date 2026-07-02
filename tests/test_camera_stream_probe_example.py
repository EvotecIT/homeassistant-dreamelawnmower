"""Tests for the camera stream probe output helpers."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType, SimpleNamespace

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)


def _load_probe_module() -> ModuleType:
    path = Path("examples/camera_stream_handshake_probe.py")
    spec = importlib.util.spec_from_file_location("camera_stream_handshake_probe", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load camera stream probe module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_inputs_summary_redacts_stable_identifiers() -> None:
    module = _load_probe_module()
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="device-id-1",
        channel_id="product-1/device-name-1",
        product_id="product-1",
        device_name="device-name-1",
        p2p_info="p2p-info-1",
        secret_key="secret-key-1",
    )

    summary = module._safe_runtime_inputs_summary(inputs)

    for key in ("did", "channel_id", "product_id", "device_name", "xp2p_id"):
        assert key not in summary
        assert summary[f"{key}_present"] is True
    assert "p2p_info" not in summary
    assert summary["p2p_info_present"] is True
    assert "secret_key" not in summary
    assert summary["secret_key_present"] is True
    assert summary["ready"] is True


def test_xp2p_request_summary_redacts_computed_stream_target() -> None:
    module = _load_probe_module()
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="device-id-1",
        channel_id="channel-1",
        product_id="product-1",
        device_name="device-name-1",
        p2p_info="p2p-info-1",
    )

    summary = module._safe_xp2p_request_summary(inputs)

    assert summary["available"] is True
    assert summary["ready"] is True
    for key in (
        "service_id",
        "flv_channel_id",
        "product_id",
        "device_name",
        "p2p_info",
        "flv_path",
    ):
        assert key not in summary
        assert summary[f"{key}_present"] is True


def test_active_video_probe_blocks_docked_or_mapping_states() -> None:
    module = _load_probe_module()

    docked = SimpleNamespace(state="charging", raw_docked=True, raw_attributes={})
    mapping = SimpleNamespace(
        state="mowing",
        raw_docked=False,
        raw_attributes={"mapping": True},
    )
    ready = SimpleNamespace(state="mowing", raw_docked=False, raw_attributes={})

    assert "docked" in module._active_video_block_reason(docked)
    assert "mapping" in module._active_video_block_reason(mapping)
    assert module._active_video_block_reason(ready) is None


def test_wait_for_active_video_state_polls_until_undocked() -> None:
    module = _load_probe_module()
    docked = SimpleNamespace(state="charging", raw_docked=True, raw_attributes={})
    ready = SimpleNamespace(state="mowing", raw_docked=False, raw_attributes={})

    class _Client:
        async def async_refresh(self):
            return ready

    snapshot, result = asyncio.run(
        module._async_wait_for_active_video_state(
            _Client(),
            initial_snapshot=docked,
            timeout=1.0,
            interval=0.01,
        )
    )

    assert snapshot is ready
    assert result["waited"] is True
    assert result["ready"] is True
    assert result["block_reason"] is None


def test_next_step_message_points_blocked_active_probe_to_supervised_run() -> None:
    module = _load_probe_module()

    message = module._next_step_message(
        active_requested=True,
        active_block_reason="blocked while docked",
    )

    assert "--wait-undocked-timeout" in message
    assert "--dock-after-active" in message


def test_next_step_message_keeps_passive_probe_guidance() -> None:
    module = _load_probe_module()

    message = module._next_step_message(
        active_requested=False,
        active_block_reason=None,
    )

    assert "XP2P runtime flag" in message
    assert "--wait-undocked-timeout" not in message


def test_xp2p_runner_probe_checks_returned_stream_url(tmp_path) -> None:
    module = _load_probe_module()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FlvHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stream_url = f"http://127.0.0.1:{server.server_port}/ipc.flv"
    runner_script = tmp_path / "xp2p_runner.py"
    runner_script.write_text(
        "\n".join(
            [
                "import json, sys",
                "payload = json.loads(sys.stdin.read())",
                "if payload['operation'] == 'start':",
                "    print(json.dumps({",
                "        'service_id': payload['request']['service_id'],",
                "        'runner_session_id': 'runner-session-1',",
                f"        'stream_url': {stream_url!r}",
                "    }))",
                "else:",
                "    print(json.dumps({'stopped': True}))",
            ]
        ),
        encoding="utf-8",
    )
    runner_cmd = tmp_path / "xp2p_runner.cmd"
    runner_cmd.write_text(
        f'@"{sys.executable}" "{runner_script}"\r\n',
        encoding="utf-8",
    )
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="device-id-1",
        channel_id="channel-1",
        product_id="product-1",
        device_name="device-name-1",
        p2p_info="p2p-info-1",
    )

    try:
        result = asyncio.run(
            module._async_probe_xp2p_runner(
                runner_cmd,
                inputs,
                mode="one-shot",
                stream_url_timeout=1.0,
                stream_url_bytes=8,
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert result["started"] is True
    assert result["runner_mode"] == "one-shot"
    assert result["stream_url_present"] is True
    assert result["stream_health"]["available"] is True
    assert result["stream_health"]["flv_header_present"] is True
    assert result["stream_health"]["bytes_read"] == 8


class _FlvHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "video/x-flv")
        self.end_headers()
        self.wfile.write(b"FLV\x01\x05\x00\x00\x00\x09")

    def log_message(self, format: str, *args) -> None:
        return
