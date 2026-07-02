"""Contract tests for the Dreame XP2P video runtime boundary."""

from __future__ import annotations

import json
import sys
from ctypes import c_void_p
from typing import Any

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.video_runtime import (
    XP2P_PROTOCOL_TCP,
    DreameLawnMowerNativeXp2pRuntime,
    DreameLawnMowerVideoRuntimeError,
    DreameLawnMowerXp2pAppConfig,
    DreameLawnMowerXp2pExternalRunner,
    DreameLawnMowerXp2pLiveStreamRequest,
    DreameLawnMowerXp2pLiveStreamSession,
    DreameLawnMowerXp2pProcessRunner,
    diagnose_native_xp2p_runtime,
)


class _FakeFunction:
    def __init__(self, result: Any = 0) -> None:
        self.result = result
        self.calls: list[tuple[Any, ...]] = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args: Any) -> Any:
        self.calls.append(args)
        if callable(self.result):
            return self.result(*args)
        return self.result


class _FakeXp2pLibrary:
    def __init__(self) -> None:
        self.startService = _FakeFunction(0)
        self.postCommandRequestSync = _FakeFunction(0)
        self.startAvRecvService = _FakeFunction(c_void_p(1234))
        self.stopAvRecvService = _FakeFunction(0)
        self.setQcloudApiCred = _FakeFunction(0)
        self.delegateHttpFlv = _FakeFunction(b"http://127.0.0.1:54321/ipc.flv")
        self.stopService = _FakeFunction(None)


def _runtime_inputs() -> DreameLawnMowerCameraStreamRuntimeInputs:
    return DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="device-1",
        channel_id="channel-1",
        product_id="product-1",
        device_name="mower-camera-1",
        p2p_info="p2p-info-1",
        secret_id="secret-id-1",
        secret_key="secret-key-1",
    )


def test_xp2p_live_stream_request_uses_runtime_contract() -> None:
    request = DreameLawnMowerXp2pLiveStreamRequest.from_runtime_inputs(
        _runtime_inputs()
    )

    assert request.service_id == "channel-1"
    assert request.product_id == "product-1"
    assert request.device_name == "mower-camera-1"
    assert request.secret_id == "secret-id-1"
    assert request.secret_key == "secret-key-1"
    assert (
        request.flv_path
        == "ipc.flv?action=live&channel=channel-1&quality=high&_crypto=on"
    )
    assert request.live_command == "action=live"
    redacted = request.as_dict(redact=True)
    for key in ("service_id", "product_id", "device_name", "flv_path"):
        assert key not in redacted
        assert redacted[f"{key}_present"] is True
    assert "p2p_info" not in redacted
    assert redacted["p2p_info_present"] is True
    assert "secret_id" not in redacted
    assert "secret_key" not in redacted
    assert redacted["secret_id_present"] is True
    assert redacted["secret_key_present"] is True


def test_xp2p_live_stream_request_encodes_fallback_channel_in_flv_path() -> None:
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="device-1",
        product_id="product-1",
        device_name="mower-camera-1",
        p2p_info="p2p-info-1",
    )

    request = DreameLawnMowerXp2pLiveStreamRequest.from_runtime_inputs(inputs)

    assert request.service_id == "product-1/mower-camera-1"
    assert (
        request.flv_path
        == "ipc.flv?action=live&channel=product-1%2Fmower-camera-1&"
        "quality=high&_crypto=on"
    )


def test_xp2p_live_stream_request_requires_p2p_runtime_inputs() -> None:
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="device-1",
        product_id="product-1",
        device_name="mower-camera-1",
    )

    try:
        DreameLawnMowerXp2pLiveStreamRequest.from_runtime_inputs(inputs)
    except DreameLawnMowerVideoRuntimeError as err:
        assert "p2p_info" in str(err)
    else:
        raise AssertionError("Expected missing p2p_info to block native stream start")


def test_native_xp2p_runtime_starts_live_stream_and_returns_flv_url() -> None:
    library = _FakeXp2pLibrary()
    runtime = DreameLawnMowerNativeXp2pRuntime("fake-xp2p.so", library=library)

    session = runtime.start_live_stream(
        _runtime_inputs(),
        app_config=DreameLawnMowerXp2pAppConfig(protocol_type=XP2P_PROTOCOL_TCP),
        command_timeout_us=123,
    )

    assert session.stream_url == "http://127.0.0.1:54321/ipc.flv"
    assert session.service_id == "channel-1"
    assert session.command_result == 0
    assert session.av_recv_handle is not None
    assert library.startService.calls
    start_call = library.startService.calls[0]
    assert start_call[0] == b"channel-1"
    assert start_call[1] == b"product-1"
    assert start_call[2] == b"mower-camera-1"
    assert start_call[3] == b"p2p-info-1"
    assert start_call[4].type == XP2P_PROTOCOL_TCP
    assert library.setQcloudApiCred.calls == [(b"secret-id-1", b"secret-key-1")]
    command_call = library.postCommandRequestSync.calls[0]
    assert command_call[0] == b"channel-1"
    assert bytes(command_call[1][: command_call[2]]) == b"action=live"
    assert command_call[5] == 123
    assert (
        library.startAvRecvService.calls[0][1]
        == b"ipc.flv?action=live&channel=channel-1&quality=high&_crypto=on"
    )
    assert library.delegateHttpFlv.calls[0][0] == b"channel-1"

    runtime.stop_live_stream(session)

    assert library.stopAvRecvService.calls
    assert library.stopService.calls == [(b"channel-1",)]


def test_native_xp2p_runtime_fails_when_qcloud_credentials_are_rejected() -> None:
    library = _FakeXp2pLibrary()
    library.setQcloudApiCred = _FakeFunction(-7)
    runtime = DreameLawnMowerNativeXp2pRuntime("fake-xp2p.so", library=library)

    try:
        runtime.start_live_stream(_runtime_inputs())
    except DreameLawnMowerVideoRuntimeError as err:
        assert "setQcloudApiCred" in str(err)
        assert "-7" in str(err)
    else:
        raise AssertionError("Expected rejected QCloud credentials to fail")

    assert library.startService.calls == []


def test_native_xp2p_runtime_cleans_up_when_flv_url_is_missing() -> None:
    library = _FakeXp2pLibrary()
    library.delegateHttpFlv = _FakeFunction(None)
    runtime = DreameLawnMowerNativeXp2pRuntime("fake-xp2p.so", library=library)

    try:
        runtime.start_live_stream(_runtime_inputs())
    except DreameLawnMowerVideoRuntimeError as err:
        assert "delegateHttpFlv" in str(err)
    else:
        raise AssertionError("Expected missing FLV URL to fail")

    assert library.stopAvRecvService.calls
    assert library.stopService.calls == [(b"channel-1",)]


def test_native_xp2p_runtime_reports_missing_required_symbols() -> None:
    library = _FakeXp2pLibrary()
    del library.delegateHttpFlv

    try:
        DreameLawnMowerNativeXp2pRuntime("fake-xp2p.so", library=library)
    except DreameLawnMowerVideoRuntimeError as err:
        assert "delegateHttpFlv" in str(err)
    else:
        raise AssertionError("Expected missing native symbol to fail")


def test_native_xp2p_runtime_reports_loader_errors() -> None:
    try:
        DreameLawnMowerNativeXp2pRuntime("missing-xp2p-runtime.so")
    except DreameLawnMowerVideoRuntimeError as err:
        assert "Could not load XP2P native library" in str(err)
    else:
        raise AssertionError("Expected missing native library to fail")


def test_native_xp2p_runtime_diagnostics_report_ready_library() -> None:
    diagnostics = diagnose_native_xp2p_runtime(
        "fake-xp2p.so",
        library=_FakeXp2pLibrary(),
    )

    assert diagnostics.ready is True
    assert diagnostics.loadable is True
    assert diagnostics.missing_required_symbols == ()
    assert diagnostics.missing_optional_symbols == ()
    assert diagnostics.error is None
    assert diagnostics.as_dict()["ready"] is True


def test_native_xp2p_runtime_diagnostics_report_missing_required_symbols() -> None:
    library = _FakeXp2pLibrary()
    del library.startService

    diagnostics = diagnose_native_xp2p_runtime("fake-xp2p.so", library=library)

    assert diagnostics.ready is False
    assert diagnostics.loadable is True
    assert diagnostics.missing_required_symbols == ("startService",)
    assert "startService" in str(diagnostics.error)


def test_native_xp2p_runtime_diagnostics_report_missing_optional_symbols() -> None:
    library = _FakeXp2pLibrary()
    del library.stopService

    diagnostics = diagnose_native_xp2p_runtime("fake-xp2p.so", library=library)

    assert diagnostics.ready is True
    assert diagnostics.missing_required_symbols == ()
    assert diagnostics.missing_optional_symbols == ("stopService",)


def test_native_xp2p_runtime_diagnostics_report_loader_errors() -> None:
    diagnostics = diagnose_native_xp2p_runtime("missing-xp2p-runtime.so")

    assert diagnostics.ready is False
    assert diagnostics.loadable is False
    assert diagnostics.missing_required_symbols == ()
    assert "Could not load XP2P native library" in str(diagnostics.error)


def test_external_runner_starts_live_stream_and_stops_session(tmp_path) -> None:
    capture = tmp_path / "calls.jsonl"
    runner_script = tmp_path / "xp2p_runner.py"
    runner_script.write_text(
        "\n".join(
            [
                "import json, pathlib, sys",
                "payload = json.loads(sys.stdin.read())",
                f"path = pathlib.Path({str(capture)!r})",
                "with path.open('a', encoding='utf-8') as handle:",
                "    handle.write(json.dumps(payload, sort_keys=True) + '\\n')",
                "if payload['operation'] == 'start':",
                "    request = payload['request']",
                "    print(json.dumps({",
                "        'service_id': request['service_id'],",
                "        'runner_session_id': 'runner-session-1',",
                "        'stream_url': 'http://127.0.0.1:5544/ipc.flv'",
                "    }))",
                "else:",
                "    print(json.dumps({'stopped': True}))",
            ]
        ),
        encoding="utf-8",
    )
    runner = DreameLawnMowerXp2pExternalRunner((sys.executable, runner_script))

    session = runner.start_live_stream(_runtime_inputs(), command_timeout_us=321)
    runner.stop_live_stream(session)

    assert session.stream_url == "http://127.0.0.1:5544/ipc.flv"
    assert session.runtime == "external_xp2p_runner"
    assert session.service_id == "channel-1"
    assert session.runner_session_id == "runner-session-1"
    calls = [
        json.loads(line)
        for line in capture.read_text(encoding="utf-8").splitlines()
    ]
    assert calls[0]["operation"] == "start"
    assert calls[0]["command_timeout_us"] == 321
    assert calls[0]["request"]["product_id"] == "product-1"
    assert calls[0]["request"]["device_name"] == "mower-camera-1"
    assert calls[0]["request"]["p2p_info"] == "p2p-info-1"
    assert calls[0]["request"]["secret_id"] == "secret-id-1"
    assert calls[0]["request"]["secret_key"] == "secret-key-1"
    assert (
        calls[0]["request"]["flv_path"]
        == "ipc.flv?action=live&channel=channel-1&quality=high&_crypto=on"
    )
    assert calls[1] == {
        "operation": "stop",
        "session": {
            "runner_session_id": "runner-session-1",
            "service_id": "channel-1",
            "stream_url": "http://127.0.0.1:5544/ipc.flv",
        },
    }


def test_external_runner_requires_stream_url(tmp_path) -> None:
    runner_script = tmp_path / "xp2p_runner.py"
    runner_script.write_text("print('{}')\n", encoding="utf-8")
    runner = DreameLawnMowerXp2pExternalRunner((sys.executable, runner_script))

    try:
        runner.start_live_stream(_runtime_inputs())
    except DreameLawnMowerVideoRuntimeError as err:
        assert "stream_url" in str(err)
    else:
        raise AssertionError("Expected missing stream_url to fail")


def test_external_runner_reports_process_failures_without_secret(tmp_path) -> None:
    runner_script = tmp_path / "xp2p_runner.py"
    runner_script.write_text(
        "\n".join(
            [
                "import sys",
                "sys.stderr.write(",
                "    'linker failed for p2p-info-1 channel-1 secret-key-1\\n'",
                ")",
                "raise SystemExit(7)",
            ]
        ),
        encoding="utf-8",
    )
    runner = DreameLawnMowerXp2pExternalRunner((sys.executable, runner_script))

    try:
        runner.start_live_stream(_runtime_inputs())
    except DreameLawnMowerVideoRuntimeError as err:
        message = str(err)
        assert "exit code 7" in message
        assert "linker failed" in message
        assert "stderr=" in message
        assert "p2p-info-1" not in message
        assert "channel-1" not in message
        assert "secret-key-1" not in message
    else:
        raise AssertionError("Expected failing external runner to fail")


def test_process_runner_keeps_stream_process_alive_until_stop(tmp_path) -> None:
    capture = tmp_path / "calls.jsonl"
    runner_script = tmp_path / "xp2p_process_runner.py"
    runner_script.write_text(
        "\n".join(
            [
                "import json, pathlib, sys",
                f"path = pathlib.Path({str(capture)!r})",
                "start = json.loads(sys.stdin.readline())",
                "with path.open('a', encoding='utf-8') as handle:",
                "    handle.write(json.dumps(start, sort_keys=True) + '\\n')",
                "request = start['request']",
                "print(json.dumps({",
                "    'service_id': request['service_id'],",
                "    'runner_session_id': 'process-session-1',",
                "    'stream_url': 'http://127.0.0.1:5544/ipc.flv'",
                "}), flush=True)",
                "stop = json.loads(sys.stdin.readline())",
                "with path.open('a', encoding='utf-8') as handle:",
                "    handle.write(json.dumps(stop, sort_keys=True) + '\\n')",
            ]
        ),
        encoding="utf-8",
    )
    runner = DreameLawnMowerXp2pProcessRunner((sys.executable, runner_script))

    session = runner.start_live_stream(_runtime_inputs(), command_timeout_us=321)

    assert session.stream_url == "http://127.0.0.1:5544/ipc.flv"
    assert session.runtime == "xp2p_process_runner"
    assert session.runner_session_id == "process-session-1"
    assert session.runner_process is not None
    assert session.runner_process.poll() is None
    assert session.as_dict()["runner_process_alive"] is True

    runner.stop_live_stream(session)

    assert session.runner_process.poll() == 0
    calls = [
        json.loads(line)
        for line in capture.read_text(encoding="utf-8").splitlines()
    ]
    assert calls[0]["operation"] == "start"
    assert calls[0]["command_timeout_us"] == 321
    assert calls[0]["request"]["flv_path"].startswith("ipc.flv?action=live")
    assert calls[0]["request"]["secret_id"] == "secret-id-1"
    assert calls[0]["request"]["secret_key"] == "secret-key-1"
    assert calls[1] == {
        "operation": "stop",
        "session": {
            "runner_session_id": "process-session-1",
            "service_id": "channel-1",
            "stream_url": "http://127.0.0.1:5544/ipc.flv",
        },
    }


def test_process_runner_reports_missing_stream_url_without_secret(tmp_path) -> None:
    runner_script = tmp_path / "xp2p_process_runner.py"
    runner_script.write_text(
        "\n".join(
            [
                "import json, sys",
                "json.loads(sys.stdin.readline())",
                "print(json.dumps({}), flush=True)",
                "sys.stdin.readline()",
            ]
        ),
        encoding="utf-8",
    )
    runner = DreameLawnMowerXp2pProcessRunner((sys.executable, runner_script))

    try:
        runner.start_live_stream(_runtime_inputs())
    except DreameLawnMowerVideoRuntimeError as err:
        message = str(err)
        assert "stream_url" in message
        assert "p2p-info-1" not in message
        assert "secret-key-1" not in message
    else:
        raise AssertionError("Expected missing stream_url to fail")


def test_process_runner_reports_startup_stderr_without_secret(tmp_path) -> None:
    runner_script = tmp_path / "xp2p_process_runner.py"
    runner_script.write_text(
        "\n".join(
            [
                "import json, sys",
                "json.loads(sys.stdin.readline())",
                "sys.stderr.write(",
                "    'xp2p auth failed for p2p-info-1 channel-1 secret-key-1\\n'",
                ")",
                "raise SystemExit(9)",
            ]
        ),
        encoding="utf-8",
    )
    runner = DreameLawnMowerXp2pProcessRunner((sys.executable, runner_script))

    try:
        runner.start_live_stream(_runtime_inputs())
    except DreameLawnMowerVideoRuntimeError as err:
        message = str(err)
        assert "stream metadata" in message
        assert "xp2p auth failed" in message
        assert "stderr=" in message
        assert "p2p-info-1" not in message
        assert "channel-1" not in message
        assert "secret-key-1" not in message
    else:
        raise AssertionError("Expected process runner startup failure to fail")


def test_stream_session_metadata_can_redact_runtime_identifiers() -> None:
    session = DreameLawnMowerXp2pLiveStreamSession(
        service_id="channel-1",
        stream_url="http://127.0.0.1:5544/ipc.flv",
        runtime="external_xp2p_runner",
        runner_session_id="runner-session-1",
    )

    redacted = session.as_dict(redact=True)

    assert "service_id" not in redacted
    assert "stream_url" not in redacted
    assert redacted["service_id_present"] is True
    assert redacted["stream_url_present"] is True
    assert redacted["runner_session_id_present"] is True
