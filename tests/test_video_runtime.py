"""Contract tests for the Dreame XP2P video runtime boundary."""

from __future__ import annotations

from ctypes import c_void_p
from typing import Any

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerNativeXp2pRuntime,
    DreameLawnMowerVideoRuntimeError,
    DreameLawnMowerXp2pAppConfig,
    DreameLawnMowerXp2pLiveStreamRequest,
    XP2P_PROTOCOL_TCP,
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
    )


def test_xp2p_live_stream_request_uses_runtime_contract() -> None:
    request = DreameLawnMowerXp2pLiveStreamRequest.from_runtime_inputs(
        _runtime_inputs()
    )

    assert request.service_id == "channel-1"
    assert request.product_id == "product-1"
    assert request.device_name == "mower-camera-1"
    assert request.live_command == "action=live"
    redacted = request.as_dict(redact=True)
    assert "p2p_info" not in redacted
    assert redacted["p2p_info_present"] is True


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
    command_call = library.postCommandRequestSync.calls[0]
    assert command_call[0] == b"channel-1"
    assert bytes(command_call[1][: command_call[2]]) == b"action=live"
    assert command_call[5] == 123
    assert library.startAvRecvService.calls[0][1] == b"action=live"
    assert library.delegateHttpFlv.calls[0][0] == b"channel-1"

    runtime.stop_live_stream(session)

    assert library.stopAvRecvService.calls
    assert library.stopService.calls == [(b"channel-1",)]


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
