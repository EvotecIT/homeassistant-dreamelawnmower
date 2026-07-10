"""Contract tests for the Dreame XP2P video runtime boundary."""

from __future__ import annotations

import json
import sys
from ctypes import POINTER, c_int, c_size_t, c_ubyte, c_void_p, cast
from pathlib import Path
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
    _decode_device_status_code,
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


class _FakeStatusFunction(_FakeFunction):
    def __init__(self, payload: bytes = b'[{"status":0}]') -> None:
        super().__init__(0)
        self.payload = payload
        self._buffers: list[Any] = []

    def __call__(self, *args: Any) -> int:
        self.calls.append(args)
        buffer = (c_ubyte * len(self.payload)).from_buffer_copy(self.payload)
        self._buffers.append(buffer)
        response_pointer = cast(args[3], POINTER(POINTER(c_ubyte)))
        response_pointer[0] = cast(buffer, POINTER(c_ubyte))
        response_length = cast(args[4], POINTER(c_size_t))
        response_length[0] = len(self.payload)
        return 0


class _FakeXp2pLibrary:
    def __init__(self) -> None:
        self.startService = _FakeFunction(0)
        self.setDeviceXp2pInfo = _FakeFunction(0)
        self.postCommandRequestSync = _FakeStatusFunction()
        self.startAvRecvService = _FakeFunction(c_void_p(1234))
        self.stopAvRecvService = _FakeFunction(0)
        self.setQcloudApiCred = _FakeFunction(0)
        self.setStunServerToXp2p = _FakeFunction(0)
        self.setCrossStunTurn = _FakeFunction(None)
        self.delegateHttpFlv = _FakeFunction(b"http://127.0.0.1:54321/")
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

    assert request.service_id == "product-1/mower-camera-1"
    assert request.delegate_id == "channel-1"
    assert request.stream_channel == "0"
    assert request.product_id == "product-1"
    assert request.device_name == "mower-camera-1"
    assert request.secret_id == "secret-id-1"
    assert request.secret_key == "secret-key-1"
    assert (
        request.flv_path
        == "ipc.flv?action=live&channel=0&quality=high&_crypto=on"
    )
    assert request.live_command == "action=live"
    assert (
        request.device_status_command
        == "action=inner_define&channel=0&cmd=get_device_st&type=live&quality=standard"
    )
    redacted = request.as_dict(redact=True)
    for key in (
        "service_id",
        "delegate_id",
        "flv_channel_id",
        "product_id",
        "device_name",
        "flv_path",
    ):
        assert key not in redacted
        assert redacted[f"{key}_present"] is True
    assert request.as_dict()["flv_channel_id"] == "channel-1"
    assert "p2p_info" not in redacted
    assert redacted["p2p_info_present"] is True
    assert "secret_id" not in redacted
    assert "secret_key" not in redacted
    assert redacted["secret_id_present"] is True
    assert redacted["secret_key_present"] is True


def test_xp2p_live_stream_request_derives_cloud_channel_identity() -> None:
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="device-1",
        product_id="product-1",
        device_name="mower-camera-1",
        p2p_info="p2p-info-1",
    )

    request = DreameLawnMowerXp2pLiveStreamRequest.from_runtime_inputs(inputs)

    assert request.service_id == "product-1/mower-camera-1"
    assert request.delegate_id == request.service_id


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
        command_timeout_us=123,
    )

    assert (
        session.stream_url
        == "http://127.0.0.1:54321/"
        "ipc.flv?action=live&channel=0&quality=high&_crypto=on"
    )
    assert session.service_id == "product-1/mower-camera-1"
    assert session.delegate_id == "channel-1"
    assert session.command_result is None
    assert session.device_status_result == 0
    assert session.device_status_code == 0
    assert session.av_recv_handle is not None
    assert session.stun_file_path is not None
    stun_file_path = Path(session.stun_file_path)
    assert stun_file_path.exists()
    assert library.startService.calls
    start_call = library.startService.calls[0]
    assert start_call[0] == b"product-1/mower-camera-1"
    assert start_call[1] == b"product-1"
    assert start_call[2] == b"mower-camera-1"
    assert start_call[3] == XP2P_PROTOCOL_TCP
    assert library.startService.argtypes[3] is c_int
    assert library.setDeviceXp2pInfo.calls == [
        (b"product-1/mower-camera-1", b"p2p-info-1")
    ]
    assert library.setQcloudApiCred.calls == [(b"secret-id-1", b"secret-key-1")]
    assert library.setStunServerToXp2p.calls
    assert library.setCrossStunTurn.calls == [(False,)]
    stun_call = library.setStunServerToXp2p.calls[0]
    assert Path(stun_call[0].decode()) == stun_file_path
    assert stun_call[1] == 20002
    assert "43.158.113.38:20002" in stun_file_path.read_text(encoding="utf-8")
    assert len(library.postCommandRequestSync.calls) == 1
    status_call = library.postCommandRequestSync.calls[0]
    assert status_call[0] == b"channel-1"
    assert bytes(status_call[1][: status_call[2]]) == (
        b"action=inner_define&channel=0&cmd=get_device_st&type=live&quality=standard"
    )
    assert status_call[5] == 123
    assert (
        library.startAvRecvService.calls[0][1]
        == b"ipc.flv?action=live&channel=0&quality=high&_crypto=on"
    )
    assert library.startAvRecvService.calls[0][0] == b"channel-1"
    assert library.delegateHttpFlv.calls[0][0] == b"channel-1"

    runtime.stop_live_stream(session)

    assert not stun_file_path.exists()
    assert library.stopAvRecvService.calls
    assert library.stopAvRecvService.calls[0][0] == b"channel-1"
    assert library.stopService.calls == [(b"channel-1",)]


def test_native_xp2p_runtime_uses_configured_stun_servers() -> None:
    library = _FakeXp2pLibrary()
    runtime = DreameLawnMowerNativeXp2pRuntime("fake-xp2p.so", library=library)

    session = runtime.start_live_stream(
        _runtime_inputs(),
        app_config=DreameLawnMowerXp2pAppConfig(
            stun_servers=("10.1.1.1:20002", "10.1.1.2:20002"),
            stun_port=20003,
        ),
    )
    stun_file_path = Path(session.stun_file_path or "")

    try:
        stun_call = library.setStunServerToXp2p.calls[0]
        assert Path(stun_call[0].decode()) == stun_file_path
        assert stun_call[1] == 20003
        assert stun_file_path.read_text(encoding="utf-8") == (
            "10.1.1.1:20002\n10.1.1.2:20002\n"
        )
    finally:
        runtime.stop_live_stream(session)


def test_native_xp2p_runtime_polls_until_flv_delegate_is_ready() -> None:
    library = _FakeXp2pLibrary()
    library.delegateHttpFlv = _FakeFunction(
        lambda *_: (
            b"http://127.0.0.1:54321/"
            if len(library.delegateHttpFlv.calls) >= 2
            else None
        )
    )
    runtime = DreameLawnMowerNativeXp2pRuntime("fake-xp2p.so", library=library)

    session = runtime.start_live_stream(
        _runtime_inputs(),
        delegate_attempts=3,
        delegate_retry_interval=0,
    )

    try:
        assert session.stream_url.startswith("http://127.0.0.1:54321/")
        assert len(library.delegateHttpFlv.calls) == 2
    finally:
        runtime.stop_live_stream(session)


def test_native_xp2p_runtime_fails_when_device_status_command_fails() -> None:
    library = _FakeXp2pLibrary()

    library.postCommandRequestSync = _FakeFunction(-9)
    runtime = DreameLawnMowerNativeXp2pRuntime("fake-xp2p.so", library=library)

    try:
        runtime.start_live_stream(_runtime_inputs(), device_status_attempts=1)
    except DreameLawnMowerVideoRuntimeError as err:
        assert "device status command" in str(err)
        assert "-9" in str(err)
    else:
        raise AssertionError("Expected failed device status command to fail")

    assert library.startAvRecvService.calls == []
    assert library.delegateHttpFlv.calls == []
    assert library.stopService.calls == [(b"channel-1",)]


def test_native_xp2p_runtime_requires_parseable_ready_status() -> None:
    library = _FakeXp2pLibrary()
    library.postCommandRequestSync = _FakeFunction(0)
    runtime = DreameLawnMowerNativeXp2pRuntime("fake-xp2p.so", library=library)

    try:
        runtime.start_live_stream(_runtime_inputs(), device_status_attempts=1)
    except DreameLawnMowerVideoRuntimeError as err:
        assert "did not report ready" in str(err)
        assert "None" in str(err)
    else:
        raise AssertionError("Expected an unknown device status to block AV receive")

    assert library.startAvRecvService.calls == []
    assert library.delegateHttpFlv.calls == []
    assert library.stopService.calls == [(b"channel-1",)]


def test_native_xp2p_runtime_rejects_null_av_receive_handle() -> None:
    library = _FakeXp2pLibrary()
    library.startAvRecvService = _FakeFunction(None)
    runtime = DreameLawnMowerNativeXp2pRuntime("fake-xp2p.so", library=library)

    try:
        runtime.start_live_stream(_runtime_inputs())
    except DreameLawnMowerVideoRuntimeError as err:
        assert "startAvRecvService returned a null handle" in str(err)
    else:
        raise AssertionError("Expected a null AV receive handle to fail")

    assert library.delegateHttpFlv.calls == []
    assert library.stopAvRecvService.calls == []
    assert library.stopService.calls == [(b"channel-1",)]


def test_device_status_decoder_reads_app_status_payload() -> None:
    assert _decode_device_status_code(b'[{"status":0}]') == 0
    assert _decode_device_status_code(b'[{"status":405}]') == 405
    assert _decode_device_status_code(b"not-json") is None
    assert _decode_device_status_code(None) is None


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


def test_native_xp2p_runtime_diagnostics_identify_android_jni_library(
    tmp_path,
) -> None:
    library_path = tmp_path / "libxnet-android.so"
    elf_header = bytearray(64)
    elf_header[0:4] = b"\x7fELF"
    elf_header[4] = 2
    elf_header[5] = 1
    elf_header[18:20] = (183).to_bytes(2, "little")
    library_path.write_bytes(
        bytes(elf_header)
        + b"Java_com_tencent_xnet_XP2P"
        + b"setDeviceXp2pInfo"
        + b"startServiceNative"
    )

    diagnostics = diagnose_native_xp2p_runtime(library_path)

    assert diagnostics.ready is False
    assert diagnostics.loadable is False
    assert diagnostics.file_format == "elf"
    assert diagnostics.machine == "aarch64"
    assert diagnostics.android_jni_symbols_present is True
    assert diagnostics.platform_hint == "android_jni"
    assert "Android JNI XP2P library" in str(diagnostics.error)
    payload = diagnostics.as_dict()
    assert payload["platform_hint"] == "android_jni"


def test_native_xp2p_runtime_diagnostics_identify_android_bionic_library(
    tmp_path,
) -> None:
    library_path = tmp_path / "libiot_video_demo.so"
    elf_header = bytearray(64)
    elf_header[0:4] = b"\x7fELF"
    elf_header[4] = 2
    elf_header[5] = 1
    elf_header[18:20] = (183).to_bytes(2, "little")
    library_path.write_bytes(
        bytes(elf_header)
        + b"startService"
        + b"setDeviceXp2pInfo"
        + b"delegateHttpFlv"
        + b"liblog.so"
    )

    diagnostics = diagnose_native_xp2p_runtime(library_path)

    assert diagnostics.ready is False
    assert diagnostics.loadable is False
    assert diagnostics.file_format == "elf"
    assert diagnostics.machine == "aarch64"
    assert diagnostics.android_jni_symbols_present is False
    assert diagnostics.android_bionic_dependencies_present is True
    assert diagnostics.platform_hint == "android_bionic"
    assert "Android XP2P runtime" in str(diagnostics.error)
    assert diagnostics.as_dict()["platform_hint"] == "android_bionic"


def test_native_xp2p_runtime_diagnostics_identify_static_archive(tmp_path) -> None:
    library_path = tmp_path / "libxp2p-ios.a"
    library_path.write_bytes(b"!<arch>\n")

    diagnostics = diagnose_native_xp2p_runtime(library_path)

    assert diagnostics.ready is False
    assert diagnostics.loadable is False
    assert diagnostics.file_format == "static_archive"
    assert diagnostics.platform_hint == "static_archive"
    assert "static library archive" in str(diagnostics.error)


def test_native_xp2p_runtime_diagnostics_identify_apple_macho(tmp_path) -> None:
    library_path = tmp_path / "TencentENET"
    library_path.write_bytes(
        b"\xcf\xfa\xed\xfe"
        + b"\0" * 60
        + b"Java_com_tencent_xnet_XP2P"
        + b"setDeviceXp2pInfo"
    )

    diagnostics = diagnose_native_xp2p_runtime(library_path)

    assert diagnostics.ready is False
    assert diagnostics.loadable is False
    assert diagnostics.file_format == "macho"
    assert diagnostics.platform_hint == "apple_macho"
    assert diagnostics.android_jni_symbols_present is False
    assert "Mach-O Apple-platform library" in str(diagnostics.error)


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
    assert session.service_id == "product-1/mower-camera-1"
    assert session.delegate_id == "channel-1"
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
        == "ipc.flv?action=live&channel=0&quality=high&_crypto=on"
    )
    assert calls[1] == {
        "operation": "stop",
        "session": {
            "delegate_id": "channel-1",
            "runner_session_id": "runner-session-1",
            "service_id": "product-1/mower-camera-1",
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
    assert session.service_id == "product-1/mower-camera-1"
    assert session.delegate_id == "channel-1"
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
            "delegate_id": "channel-1",
            "runner_session_id": "process-session-1",
            "service_id": "product-1/mower-camera-1",
            "stream_url": "http://127.0.0.1:5544/ipc.flv",
        },
    }


def test_process_runner_drains_persistent_output_until_stop(tmp_path) -> None:
    completed_marker = tmp_path / "output-drained.txt"
    runner_script = tmp_path / "xp2p_noisy_process_runner.py"
    runner_script.write_text(
        "\n".join(
            [
                "import json, pathlib, sys",
                f"marker = pathlib.Path({str(completed_marker)!r})",
                "start = json.loads(sys.stdin.readline())",
                "request = start['request']",
                "print(json.dumps({",
                "    'service_id': request['service_id'],",
                "    'stream_url': 'http://127.0.0.1:5544/ipc.flv'",
                "}), flush=True)",
                "sys.stdout.write('y' * 262144)",
                "sys.stdout.flush()",
                "sys.stderr.write('x' * 262144)",
                "sys.stderr.flush()",
                "marker.write_text('drained', encoding='utf-8')",
                "json.loads(sys.stdin.readline())",
            ]
        ),
        encoding="utf-8",
    )
    runner = DreameLawnMowerXp2pProcessRunner(
        (sys.executable, runner_script),
        timeout=2,
    )

    session = runner.start_live_stream(_runtime_inputs())
    runner.stop_live_stream(session)

    assert session.runner_process is not None
    assert session.runner_process.returncode == 0
    assert completed_marker.read_text(encoding="utf-8") == "drained"


def test_process_runner_drains_stderr_before_startup_metadata(tmp_path) -> None:
    completed_marker = tmp_path / "startup-stderr-drained.txt"
    runner_script = tmp_path / "xp2p_noisy_startup_runner.py"
    runner_script.write_text(
        "\n".join(
            [
                "import json, pathlib, sys",
                f"marker = pathlib.Path({str(completed_marker)!r})",
                "start = json.loads(sys.stdin.readline())",
                "request = start['request']",
                "sys.stderr.write('x' * 262144)",
                "sys.stderr.flush()",
                "print(json.dumps({",
                "    'service_id': request['service_id'],",
                "    'stream_url': 'http://127.0.0.1:5544/ipc.flv'",
                "}), flush=True)",
                "marker.write_text('drained', encoding='utf-8')",
                "json.loads(sys.stdin.readline())",
            ]
        ),
        encoding="utf-8",
    )
    runner = DreameLawnMowerXp2pProcessRunner(
        (sys.executable, runner_script),
        timeout=2,
    )

    session = runner.start_live_stream(_runtime_inputs())
    runner.stop_live_stream(session)

    assert session.runner_process is not None
    assert session.runner_process.returncode == 0
    assert completed_marker.read_text(encoding="utf-8") == "drained"


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
        delegate_id="channel-1",
        runtime="external_xp2p_runner",
        runner_command=("xp2p-runner", "--token", "runner-secret"),
        runner_session_id="runner-session-1",
    )

    redacted = session.as_dict(redact=True)

    assert "service_id" not in redacted
    assert "delegate_id" not in redacted
    assert "stream_url" not in redacted
    assert "runner_command" not in redacted
    assert "runner-secret" not in repr(redacted)
    assert redacted["service_id_present"] is True
    assert redacted["delegate_id_present"] is True
    assert redacted["stream_url_present"] is True
    assert redacted["runner_command_present"] is True
    assert redacted["runner_session_id_present"] is True
