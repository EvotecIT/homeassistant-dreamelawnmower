"""Tests for the camera stream probe output helpers."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest_socket

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.stream_health import (
    _probe_response,
    probe_stream_url,
)


def _load_probe_module() -> ModuleType:
    path = Path("examples/camera_stream_handshake_probe.py")
    spec = importlib.util.spec_from_file_location("camera_stream_handshake_probe", path)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load camera stream probe module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_help_uses_standalone_client_package(tmp_path) -> None:
    (tmp_path / "sitecustomize.py").write_text(
        "\n".join(
            [
                "import sys",
                "class _BlockHomeAssistant:",
                "    def find_spec(self, fullname, path=None, target=None):",
                (
                    "        if fullname == 'homeassistant' or "
                    "fullname.startswith('homeassistant.'):"
                ),
                (
                    "            raise RuntimeError("
                    "'standalone probe imported Home Assistant')"
                ),
                "        return None",
                "sys.meta_path.insert(0, _BlockHomeAssistant())",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        item
        for item in (str(tmp_path), env.get("PYTHONPATH"))
        if item
    )
    completed = subprocess.run(
        [sys.executable, "examples/camera_stream_handshake_probe.py", "--help"],
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--execute" in completed.stdout


class _FakeVideoClient:
    def __init__(self) -> None:
        self.video_enabled: list[bool] = []

    async def async_set_camera_stream_enabled(self, enabled: bool):
        self.video_enabled.append(enabled)
        return {"ok": True, "enabled": enabled}


class _FakeDockClient:
    def __init__(self, *snapshots) -> None:
        self.snapshots = list(snapshots)
        self.dock_called = False

    async def async_dock(self) -> None:
        self.dock_called = True

    async def async_refresh(self):
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]


class _FakeStartClient:
    async def async_start_mowing(self) -> None:
        return None

    async def async_refresh(self):
        raise RuntimeError("refresh unavailable")


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
    assert summary["qcloud_credential_state"] == "partial"
    assert summary["missing_qcloud_credentials"] == ("secret_id",)
    assert summary["app_credential_state"] == "absent"
    assert summary["missing_app_credentials"] == ("app_id", "app_secret")
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
        "delegate_id",
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


def test_next_step_message_reports_missing_host_runtime() -> None:
    module = _load_probe_module()

    message = module._next_step_message(
        active_requested=True,
        active_block_reason=None,
        active_stream_verdict={
            "status": "configuration_missing",
            "source": "xp2p_runner",
        },
    )

    assert "host XP2P runtime" in message
    assert "xp2p_runner" in message
    assert "--wait-undocked-timeout" not in message


def test_field_test_profile_expands_xp2p_runner_defaults() -> None:
    module = _load_probe_module()
    args = SimpleNamespace(
        field_test_profile="xp2p-runner",
        start_xp2p_runner=False,
        start_native_xp2p=False,
        wait_undocked_timeout=0.0,
        dock_after_active=False,
        stream_url_attempts=module.DEFAULT_STREAM_URL_ATTEMPTS,
        stream_url_retry_interval=module.DEFAULT_STREAM_URL_RETRY_INTERVAL,
    )

    result = module._apply_field_test_profile(args)

    assert result.start_xp2p_runner is True
    assert result.start_native_xp2p is False
    assert result.wait_undocked_timeout == module.PROFILE_WAIT_UNDOCKED_TIMEOUT
    assert result.dock_after_active is True
    assert result.stream_url_attempts == module.PROFILE_STREAM_URL_ATTEMPTS
    assert (
        result.stream_url_retry_interval
        == module.PROFILE_STREAM_URL_RETRY_INTERVAL
    )


def test_field_test_profile_preserves_explicit_probe_timing() -> None:
    module = _load_probe_module()
    args = SimpleNamespace(
        field_test_profile="native-xp2p",
        start_xp2p_runner=False,
        start_native_xp2p=False,
        wait_undocked_timeout=30.0,
        dock_after_active=False,
        stream_url_attempts=3,
        stream_url_retry_interval=0.5,
    )

    result = module._apply_field_test_profile(args)

    assert result.start_native_xp2p is True
    assert result.start_xp2p_runner is False
    assert result.wait_undocked_timeout == 30.0
    assert result.dock_after_active is True
    assert result.stream_url_attempts == 3
    assert result.stream_url_retry_interval == 0.5


def test_start_before_active_enables_dock_cleanup() -> None:
    module = _load_probe_module()
    args = SimpleNamespace(
        field_test_profile="none",
        start_before_active=True,
        dock_after_active=False,
    )

    result = module._apply_field_test_profile(args)

    assert result.dock_after_active is True


def test_start_before_active_preserves_cleanup_after_wait_failure() -> None:
    module = _load_probe_module()
    docked = SimpleNamespace(
        state="charging",
        activity="docked",
        battery_level=90,
        docked=True,
        raw_docked=True,
        mowing=False,
        paused=False,
        returning=False,
    )

    snapshot, result = asyncio.run(
        module._async_start_before_active(
            _FakeStartClient(),
            initial_snapshot=docked,
            timeout=1.0,
            interval=0.01,
        )
    )

    assert snapshot is docked
    assert result["sent"] is True
    assert result["wait_error_type"] == "RuntimeError"


def test_dock_after_active_waits_for_station_state() -> None:
    module = _load_probe_module()
    returning = SimpleNamespace(
        state="returning",
        activity="returning",
        battery_level=90,
        docked=False,
        raw_docked=False,
        mowing=False,
        paused=False,
        returning=True,
    )
    docked = SimpleNamespace(
        state="charging",
        activity="docked",
        battery_level=90,
        docked=True,
        raw_docked=True,
        mowing=False,
        paused=False,
        returning=False,
    )
    client = _FakeDockClient(returning, docked)
    output: dict[str, object] = {}

    asyncio.run(
        module._async_dock_after_active(
            client,
            output,
            timeout=1.0,
            interval=0.01,
        )
    )

    result = output["dock_after_active"]
    assert client.dock_called is True
    assert result["sent"] is True
    assert result["wait"]["ready"] is True
    assert result["after"]["state"] == "charging"


def test_host_runtime_preflight_blocks_missing_field_runner_before_wait() -> None:
    module = _load_probe_module()
    args = SimpleNamespace(
        start_xp2p_runner=True,
        start_native_xp2p=False,
        xp2p_runner=None,
        xp2p_library=None,
    )

    result = module._host_runtime_preflight(args)

    assert result["live_stream_requested"] is True
    assert result["runnable"] is False
    assert result["runnable_sources"] == []
    assert result["failures"] == {
        "xp2p_runner": "--xp2p-runner is required with --start-xp2p-runner.",
    }


def test_active_stream_verdict_reports_blocked_probe() -> None:
    module = _load_probe_module()

    verdict = module._active_stream_verdict(
        {},
        active_requested=True,
        active_block_reason="blocked while docked",
    )

    assert verdict == {
        "status": "blocked",
        "reason": "blocked while docked",
    }


def test_active_stream_verdict_reports_configuration_missing() -> None:
    module = _load_probe_module()

    verdict = module._active_stream_verdict(
        {
            "xp2p_runner": {
                "started": False,
                "attempted": False,
                "available": False,
                "error_category": "configuration_missing",
                "error": "--xp2p-runner is required with --start-xp2p-runner.",
            }
        },
        active_requested=True,
        active_block_reason=None,
    )

    assert verdict == {
        "status": "configuration_missing",
        "source": "xp2p_runner",
        "error": "--xp2p-runner is required with --start-xp2p-runner.",
    }


def test_active_stream_verdict_prefers_confirmed_flv_over_missing_other_path() -> None:
    module = _load_probe_module()

    verdict = module._active_stream_verdict(
        {
            "native_xp2p": {
                "started": False,
                "attempted": False,
                "available": False,
                "error_category": "configuration_missing",
                "error": "--xp2p-library is required with --start-native-xp2p.",
            },
            "xp2p_runner": {
                "started": True,
                "stream_health": {
                    "available": True,
                    "flv_header_present": True,
                    "bytes_read": 8,
                    "status_code": 200,
                },
            },
        },
        active_requested=True,
        active_block_reason=None,
    )

    assert verdict["status"] == "flv_header_confirmed"
    assert verdict["source"] == "xp2p_runner"


def test_active_stream_verdict_reports_confirmed_flv_header() -> None:
    module = _load_probe_module()

    verdict = module._active_stream_verdict(
        {
            "xp2p_runner": {
                "started": True,
                "stream_health": {
                    "available": True,
                    "flv_header_present": True,
                    "bytes_read": 8,
                    "status_code": 200,
                },
            }
        },
        active_requested=True,
        active_block_reason=None,
    )

    assert verdict == {
        "status": "flv_header_confirmed",
        "source": "xp2p_runner",
        "available": True,
        "flv_header_present": True,
        "bytes_read": 8,
        "status_code": 200,
    }


def test_active_stream_verdict_reports_open_stream_without_flv_header() -> None:
    module = _load_probe_module()

    verdict = module._active_stream_verdict(
        {
            "native_xp2p": {
                "started": True,
                "stream_health": {
                    "available": True,
                    "content_type": "application/json",
                    "error_category": "open_without_flv_header",
                    "flv_header_present": False,
                    "bytes_read": 8,
                    "attempts": 3,
                    "elapsed_seconds": 2.0,
                    "first_bytes_hex": "7b226572726f7222",
                },
            }
        },
        active_requested=True,
        active_block_reason=None,
    )

    assert verdict["status"] == "stream_opened_without_flv_header"
    assert verdict["source"] == "native_xp2p"
    assert verdict["available"] is True
    assert verdict["flv_header_present"] is False
    assert verdict["bytes_read"] == 8
    assert verdict["content_type"] == "application/json"
    assert verdict["error_category"] == "open_without_flv_header"
    assert verdict["attempts"] == 3
    assert verdict["elapsed_seconds"] == 2.0
    assert verdict["first_bytes_hex"] == "7b226572726f7222"


def test_xp2p_runner_probe_checks_returned_stream_url(tmp_path) -> None:
    pytest_socket.enable_socket()
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
    if os.name == "nt":
        runner_cmd = tmp_path / "xp2p_runner.cmd"
        runner_cmd.write_text(
            f'@"{sys.executable}" "{runner_script}"\r\n',
            encoding="utf-8",
        )
    else:
        runner_cmd = tmp_path / "xp2p_runner"
        runner_cmd.write_text(
            "#!/bin/sh\n"
            f"exec {shlex.quote(sys.executable)} "
            f"{shlex.quote(str(runner_script))}\n",
            encoding="utf-8",
        )
        runner_cmd.chmod(0o755)
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
                _FakeVideoClient(),
                runner_cmd,
                inputs,
                mode="one-shot",
                stream_url_timeout=1.0,
                stream_url_bytes=8,
                stream_url_attempts=2,
                stream_url_retry_interval=0.01,
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert result["started"] is True
    assert result["attempted"] is True
    assert result["runner_mode"] == "one-shot"
    assert result["stream_url_present"] is True
    assert result["stream_health"]["available"] is True
    assert result["stream_health"]["flv_header_present"] is True
    assert result["stream_health"]["bytes_read"] == 3
    assert result["stream_health"]["attempts"] == 1
    assert result["app_video_enable"]["ok"] is True
    assert result["app_video_disable"]["ok"] is True


def test_xp2p_runner_probe_missing_runner_is_preflight_failure() -> None:
    module = _load_probe_module()
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="device-id-1",
        channel_id="channel-1",
        product_id="product-1",
        device_name="device-name-1",
        p2p_info="p2p-info-1",
    )

    result = asyncio.run(
        module._async_probe_xp2p_runner(
            _FakeVideoClient(),
            None,
            inputs,
            mode="process",
            stream_url_timeout=1.0,
            stream_url_bytes=8,
            stream_url_attempts=1,
            stream_url_retry_interval=0.01,
        )
    )

    assert result["started"] is False
    assert result["attempted"] is False
    assert result["error_category"] == "configuration_missing"
    assert "--xp2p-runner" in str(result["error"])


def test_stream_url_probe_retries_until_flv_header() -> None:
    pytest_socket.enable_socket()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DelayedFlvHandler)
    _DelayedFlvHandler.request_count = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stream_url = f"http://127.0.0.1:{server.server_port}/ipc.flv"

    try:
        result = probe_stream_url(
            stream_url,
            timeout=1.0,
            read_bytes=8,
            attempts=3,
            retry_interval=0.01,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert result.available is True
    assert result.flv_header_present is True
    assert result.bytes_read == 3
    assert result.attempts == 2
    assert result.elapsed_seconds >= 0


def test_stream_url_probe_invokes_route_callback_after_flv_header() -> None:
    pytest_socket.enable_socket()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FlvHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    callbacks: list[str] = []

    try:
        result = probe_stream_url(
            f"http://127.0.0.1:{server.server_port}/ipc.flv",
            timeout=1.0,
            on_stream_open=lambda: callbacks.append("open"),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert result.flv_header_present is True
    assert callbacks == ["open"]


def test_stream_health_accepts_flushed_signature_without_waiting_for_media() -> None:
    reads: list[int] = []

    class _HeaderOnlyResponse:
        status = 200
        headers = {"Content-Type": "video/x-flv"}

        def getcode(self) -> int:
            return self.status

        def read(self, size: int) -> bytes:
            reads.append(size)
            if size > 3:
                raise TimeoutError("media tag not flushed yet")
            return b"FLV"[:size]

    result = _probe_response(
        _HeaderOnlyResponse(),
        read_bytes=16,
        attempts=1,
        elapsed_seconds=0.0,
    )

    assert reads == [3]
    assert result.flv_header_present is True
    assert result.bytes_read == 3


def test_stream_url_probe_reports_open_non_flv_response() -> None:
    pytest_socket.enable_socket()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stream_url = f"http://127.0.0.1:{server.server_port}/ipc.flv"

    try:
        result = probe_stream_url(
            stream_url,
            timeout=1.0,
            read_bytes=8,
            attempts=1,
            retry_interval=0.01,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert result.available is True
    assert result.flv_header_present is False
    assert result.error_category == "open_without_flv_header"
    assert result.content_type == "application/json"
    assert result.first_bytes_hex == b'{"e'.hex()


class _FlvHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "video/x-flv")
        self.end_headers()
        self.wfile.write(b"FLV\x01\x05\x00\x00\x00\x09")

    def log_message(self, format: str, *args) -> None:
        return


class _DelayedFlvHandler(BaseHTTPRequestHandler):
    request_count = 0

    def do_GET(self) -> None:
        type(self).request_count += 1
        self.send_response(200)
        self.send_header("Content-Type", "video/x-flv")
        self.end_headers()
        if type(self).request_count == 1:
            self.wfile.write(b"WAITING!")
        else:
            self.wfile.write(b"FLV\x01\x05\x00\x00\x00\x09")

    def log_message(self, format: str, *args) -> None:
        return


class _JsonHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"not_ready"}')

    def log_message(self, format: str, *args) -> None:
        return
