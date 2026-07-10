"""Native XP2P runtime bindings for Dreame mower live video."""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from ctypes import (
    CDLL,
    POINTER,
    Structure,
    byref,
    c_bool,
    c_char_p,
    c_int,
    c_size_t,
    c_ubyte,
    c_uint16,
    c_uint64,
    c_void_p,
)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from .models import DreameLawnMowerCameraStreamRuntimeInputs
from .video_runner_diagnostics import (
    RUNNER_OUTPUT_PREVIEW_LIMIT,
    completed_process_preview,
    output_preview,
    payload_sensitive_values,
    safe_output_preview,
    timeout_expired_preview,
)

XP2P_PROTOCOL_AUTO = 0
XP2P_PROTOCOL_UDP = 1
XP2P_PROTOCOL_TCP = 2

DEFAULT_COMMAND_TIMEOUT_US = 7_500_000
ANDROID_XP2P_JNI_SYMBOL_MARKERS = (
    b"Java_com_tencent_xnet_XP2P",
    b"startServiceNative",
)
ANDROID_BIONIC_LIBRARY_MARKERS = (
    b"liblog.so",
    b"libandroid.so",
)
REQUIRED_XP2P_SYMBOLS = (
    "startService",
    "setDeviceXp2pInfo",
    "postCommandRequestSync",
    "startAvRecvService",
    "delegateHttpFlv",
)
OPTIONAL_XP2P_SYMBOLS = (
    "setQcloudApiCred",
    "setStunServerToXp2p",
    "setCrossStunTurn",
    "stopAvRecvService",
    "stopService",
)


class DreameLawnMowerVideoRuntimeError(RuntimeError):
    """Raised when the native video runtime cannot start a stream."""


class _NativeCallable(Protocol):
    argtypes: Any
    restype: Any

    def __call__(self, *args: Any) -> Any: ...


class _Xp2pAppConfig(Structure):
    _fields_ = [
        ("server", c_char_p),
        ("ip", c_char_p),
        ("port", c_uint64),
        ("type", c_int),
        ("cross", c_bool),
    ]


@dataclass(slots=True, frozen=True)
class DreameLawnMowerXp2pAppConfig:
    """XP2P native app configuration."""

    server: str = ""
    ip: str = ""
    port: int = 0
    protocol_type: int = XP2P_PROTOCOL_AUTO
    cross: bool = False
    stun_servers: tuple[str, ...] = ("43.158.113.38:20002",)
    stun_port: int = 20002

    def to_native(self) -> _Xp2pAppConfig:
        """Return the ctypes struct expected by Tencent's XP2P C ABI."""
        config = _Xp2pAppConfig()
        config.server = _encode(self.server)
        config.ip = _encode(self.ip)
        config.port = self.port
        config.type = self.protocol_type
        config.cross = self.cross
        return config


@dataclass(slots=True, frozen=True)
class DreameLawnMowerXp2pRuntimeDiagnostics:
    """Native XP2P runtime readiness diagnostics."""

    library_path: str
    loadable: bool
    ready: bool
    required_symbols: tuple[str, ...] = REQUIRED_XP2P_SYMBOLS
    optional_symbols: tuple[str, ...] = OPTIONAL_XP2P_SYMBOLS
    missing_required_symbols: tuple[str, ...] = ()
    missing_optional_symbols: tuple[str, ...] = ()
    file_format: str | None = None
    machine: str | None = None
    android_jni_symbols_present: bool = False
    android_bionic_dependencies_present: bool = False
    platform_hint: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe diagnostics payload."""
        return {
            "library_path": self.library_path,
            "loadable": self.loadable,
            "ready": self.ready,
            "required_symbols": self.required_symbols,
            "optional_symbols": self.optional_symbols,
            "missing_required_symbols": self.missing_required_symbols,
            "missing_optional_symbols": self.missing_optional_symbols,
            "file_format": self.file_format,
            "machine": self.machine,
            "android_jni_symbols_present": self.android_jni_symbols_present,
            "android_bionic_dependencies_present": (
                self.android_bionic_dependencies_present
            ),
            "platform_hint": self.platform_hint,
            "error": self.error,
        }


@dataclass(slots=True, frozen=True)
class DreameLawnMowerXp2pLiveStreamRequest:
    """Normalized request passed to a native XP2P runtime."""

    service_id: str
    delegate_id: str
    stream_channel: str
    product_id: str
    device_name: str
    p2p_info: str = field(repr=False)
    flv_path: str
    live_command: str = "action=live"
    device_status_command: str = (
        "action=inner_define&channel=0&cmd=get_device_st&type=live&quality=standard"
    )
    secret_id: str | None = field(default=None, repr=False)
    secret_key: str | None = field(default=None, repr=False)
    app_id: str | None = field(default=None, repr=False)
    app_secret: str | None = field(default=None, repr=False)

    @classmethod
    def from_runtime_inputs(
        cls,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    ) -> DreameLawnMowerXp2pLiveStreamRequest:
        """Build a native stream request from Dreame cloud runtime inputs."""
        missing = inputs.missing_required
        if missing:
            raise DreameLawnMowerVideoRuntimeError(
                "Cannot start XP2P stream; missing runtime fields: "
                + ", ".join(missing)
            )
        service_id = inputs.xp2p_id or inputs.channel_id or inputs.did
        if not service_id:
            raise DreameLawnMowerVideoRuntimeError(
                "Cannot start XP2P stream; missing service id."
            )
        delegate_id = inputs.channel_id or service_id
        stream_channel = str(inputs.stream_channel)
        flv_path = _format_flv_path(inputs.flv_path_template, stream_channel)
        device_status_command = _format_device_status_command(stream_channel)
        return cls(
            service_id=service_id,
            delegate_id=delegate_id,
            stream_channel=stream_channel,
            product_id=str(inputs.product_id),
            device_name=str(inputs.device_name),
            p2p_info=str(inputs.p2p_info),
            secret_id=inputs.secret_id,
            secret_key=inputs.secret_key,
            app_id=inputs.app_id,
            app_secret=inputs.app_secret,
            flv_path=flv_path,
            live_command=inputs.live_command,
            device_status_command=device_status_command,
        )

    def as_dict(self, *, redact: bool = False) -> dict[str, Any]:
        """Return a JSON-safe request payload."""
        payload = {
            "service_id": self.service_id,
            "delegate_id": self.delegate_id,
            "flv_channel_id": self.delegate_id,
            "stream_channel": self.stream_channel,
            "product_id": self.product_id,
            "device_name": self.device_name,
            "p2p_info": self.p2p_info,
            "secret_id": self.secret_id,
            "secret_key": self.secret_key,
            "app_id": self.app_id,
            "app_secret": self.app_secret,
            "flv_path": self.flv_path,
            "live_command": self.live_command,
            "device_status_command": self.device_status_command,
        }
        if redact:
            for key in (
                "service_id",
                "delegate_id",
                "flv_channel_id",
                "product_id",
                "device_name",
                "p2p_info",
                "secret_id",
                "secret_key",
                "app_id",
                "app_secret",
                "flv_path",
            ):
                payload[f"{key}_present"] = bool(payload.pop(key, None))
        return payload


@dataclass(slots=True)
class DreameLawnMowerXp2pLiveStreamSession:
    """Started native XP2P stream session."""

    service_id: str
    stream_url: str
    delegate_id: str | None = None
    runtime: str = "native_xp2p"
    start_result: int = 0
    command_result: int | None = None
    command_response: bytes | None = field(default=None, repr=False)
    device_status_result: int | None = None
    device_status_code: int | None = None
    device_status_response: bytes | None = field(default=None, repr=False)
    av_recv_handle: Any | None = field(default=None, repr=False)
    stun_file_path: str | None = field(default=None, repr=False)
    runner_command: tuple[str, ...] = field(default=(), repr=False)
    runner_session_id: str | None = None
    runner_process: Any | None = field(default=None, repr=False)
    runner_stdout_thread: threading.Thread | None = field(default=None, repr=False)
    runner_stderr_thread: threading.Thread | None = field(default=None, repr=False)

    def as_dict(self, *, redact: bool = False) -> dict[str, Any]:
        """Return safe stream session metadata."""
        payload = {
            "service_id": self.service_id,
            "delegate_id": self.delegate_id,
            "stream_url": self.stream_url,
            "runtime": self.runtime,
            "start_result": self.start_result,
            "command_result": self.command_result,
            "command_response_present": bool(self.command_response),
            "device_status_result": self.device_status_result,
            "device_status_code": self.device_status_code,
            "device_status_response_present": bool(self.device_status_response),
            "av_recv_started": self.av_recv_handle is not None,
            "stun_configured": bool(self.stun_file_path),
            "runner_command": self.runner_command,
            "runner_session_id_present": bool(self.runner_session_id),
            "runner_process_alive": _process_alive(self.runner_process),
        }
        if redact:
            for key in ("service_id", "delegate_id", "stream_url"):
                payload[f"{key}_present"] = bool(payload.pop(key, None))
            payload["runner_command_present"] = bool(
                payload.pop("runner_command", ())
            )
        return payload


class DreameLawnMowerXp2pExternalRunner:
    """JSON stdin/stdout adapter for an external XP2P playback runner."""

    def __init__(self, command: Sequence[str | Path], *, timeout: float = 15.0) -> None:
        self.command = tuple(str(part) for part in command)
        if not self.command:
            raise DreameLawnMowerVideoRuntimeError(
                "XP2P external runner command cannot be empty."
            )
        self.timeout = timeout

    def start_live_stream(
        self,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
        *,
        command_timeout_us: int = DEFAULT_COMMAND_TIMEOUT_US,
    ) -> DreameLawnMowerXp2pLiveStreamSession:
        """Start live video through an external XP2P runner."""
        request = DreameLawnMowerXp2pLiveStreamRequest.from_runtime_inputs(inputs)
        payload = {
            "operation": "start",
            "request": request.as_dict(redact=False),
            "command_timeout_us": command_timeout_us,
        }
        response = self._run_json(payload)
        stream_url = _as_text(response.get("stream_url"))
        if not stream_url:
            raise DreameLawnMowerVideoRuntimeError(
                "XP2P external runner did not return stream_url."
            )
        return DreameLawnMowerXp2pLiveStreamSession(
            service_id=_as_text(response.get("service_id")) or request.service_id,
            stream_url=stream_url,
            delegate_id=(
                _as_text(response.get("delegate_id")) or request.delegate_id
            ),
            runtime="external_xp2p_runner",
            runner_command=self.command,
            runner_session_id=_as_text(response.get("runner_session_id")),
        )

    def stop_live_stream(self, session: DreameLawnMowerXp2pLiveStreamSession) -> None:
        """Ask the external XP2P runner to stop a live stream."""
        self._run_json(
            {
                "operation": "stop",
                "session": {
                    "service_id": session.service_id,
                    "delegate_id": session.delegate_id,
                    "stream_url": session.stream_url,
                    "runner_session_id": session.runner_session_id,
                },
            }
        )

    def _run_json(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        sensitive_values = payload_sensitive_values(payload)
        try:
            completed = subprocess.run(
                self.command,
                input=json.dumps(payload),
                capture_output=True,
                check=False,
                encoding="utf-8",
                timeout=self.timeout,
            )
        except OSError as err:
            raise DreameLawnMowerVideoRuntimeError(
                f"Could not start XP2P external runner {self.command!r}: {err}"
            ) from err
        except subprocess.TimeoutExpired as err:
            raise DreameLawnMowerVideoRuntimeError(
                f"XP2P external runner timed out after {self.timeout:g}s."
                + timeout_expired_preview(err, sensitive_values)
            ) from err
        if completed.returncode != 0:
            raise DreameLawnMowerVideoRuntimeError(
                "XP2P external runner failed with exit code "
                f"{completed.returncode}."
                + completed_process_preview(completed, sensitive_values)
            )
        try:
            response = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as err:
            raise DreameLawnMowerVideoRuntimeError(
                "XP2P external runner returned invalid JSON."
                + completed_process_preview(completed, sensitive_values)
            ) from err
        if not isinstance(response, dict):
            raise DreameLawnMowerVideoRuntimeError(
                "XP2P external runner response must be a JSON object."
            )
        if response.get("error"):
            raise DreameLawnMowerVideoRuntimeError(
                "XP2P external runner failed: "
                f"{safe_output_preview(response.get('error'), sensitive_values)}"
            )
        return response


class DreameLawnMowerXp2pProcessRunner:
    """Persistent JSON-line adapter for an XP2P runner that owns the FLV server."""

    def __init__(self, command: Sequence[str | Path], *, timeout: float = 15.0) -> None:
        self.command = tuple(str(part) for part in command)
        if not self.command:
            raise DreameLawnMowerVideoRuntimeError(
                "XP2P process runner command cannot be empty."
            )
        self.timeout = timeout

    def start_live_stream(
        self,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
        *,
        command_timeout_us: int = DEFAULT_COMMAND_TIMEOUT_US,
    ) -> DreameLawnMowerXp2pLiveStreamSession:
        """Start a persistent XP2P runner and return its local FLV URL."""
        request = DreameLawnMowerXp2pLiveStreamRequest.from_runtime_inputs(inputs)
        try:
            process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding="utf-8",
            )
        except OSError as err:
            raise DreameLawnMowerVideoRuntimeError(
                f"Could not start XP2P process runner {self.command!r}: {err}"
            ) from err

        stderr_tail: list[str] = []
        stderr_thread: threading.Thread | None = None
        try:
            stderr_thread = _start_stream_drain_thread(
                process.stderr,
                name="dreame-xp2p-stderr",
                tail=stderr_tail,
            )
            _write_json_line(
                process,
                {
                    "operation": "start",
                    "request": request.as_dict(redact=False),
                    "command_timeout_us": command_timeout_us,
                },
            )
            sensitive_values = payload_sensitive_values(
                {
                    "request": request.as_dict(redact=False),
                }
            )
            response = _read_json_line(
                process,
                timeout=self.timeout,
                sensitive_values=sensitive_values,
                stderr_thread=stderr_thread,
                stderr_tail=stderr_tail,
            )
            if response.get("error"):
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P process runner failed: "
                    f"{safe_output_preview(response.get('error'), sensitive_values)}"
                )
            stream_url = _as_text(response.get("stream_url"))
            if not stream_url:
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P process runner did not return stream_url."
                )
            stdout_thread = _start_stream_drain_thread(
                process.stdout,
                name="dreame-xp2p-stdout",
            )
            return DreameLawnMowerXp2pLiveStreamSession(
                service_id=_as_text(response.get("service_id")) or request.service_id,
                stream_url=stream_url,
                delegate_id=(
                    _as_text(response.get("delegate_id")) or request.delegate_id
                ),
                runtime="xp2p_process_runner",
                runner_command=self.command,
                runner_session_id=_as_text(response.get("runner_session_id")),
                runner_process=process,
                runner_stdout_thread=stdout_thread,
                runner_stderr_thread=stderr_thread,
            )
        except Exception:
            _terminate_process(process)
            _join_stream_drain_thread(stderr_thread)
            raise

    def stop_live_stream(self, session: DreameLawnMowerXp2pLiveStreamSession) -> None:
        """Stop the persistent XP2P runner that owns the stream session."""
        process = session.runner_process
        if process is None:
            return
        if process.poll() is not None:
            _join_stream_drain_thread(session.runner_stdout_thread)
            _join_stream_drain_thread(session.runner_stderr_thread)
            return
        try:
            _write_json_line(
                process,
                {
                    "operation": "stop",
                    "session": {
                        "service_id": session.service_id,
                        "delegate_id": session.delegate_id,
                        "stream_url": session.stream_url,
                        "runner_session_id": session.runner_session_id,
                    },
                },
            )
            process.wait(timeout=self.timeout)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            _terminate_process(process)
        finally:
            _join_stream_drain_thread(session.runner_stdout_thread)
            _join_stream_drain_thread(session.runner_stderr_thread)


class DreameLawnMowerNativeXp2pRuntime:
    """Thin ctypes wrapper around Tencent's native XP2P C ABI."""

    def __init__(self, library_path: str | Path, *, library: Any | None = None) -> None:
        self.library_path = str(library_path)
        try:
            self._library = library if library is not None else CDLL(self.library_path)
        except OSError as err:
            raise DreameLawnMowerVideoRuntimeError(
                f"Could not load XP2P native library {self.library_path!r}: {err}"
            ) from err
        self._start_service = self._bind(
            "startService",
            [c_char_p, c_char_p, c_char_p, _Xp2pAppConfig],
            c_int,
        )
        self._set_device_xp2p_info = self._bind(
            "setDeviceXp2pInfo",
            [c_char_p, c_char_p],
            c_int,
        )
        self._post_command = self._bind(
            "postCommandRequestSync",
            [
                c_char_p,
                POINTER(c_ubyte),
                c_size_t,
                POINTER(POINTER(c_ubyte)),
                POINTER(c_size_t),
                c_uint64,
            ],
            c_int,
        )
        self._start_av_recv = self._bind(
            "startAvRecvService",
            [c_char_p, c_char_p, c_bool],
            c_void_p,
        )
        self._stop_av_recv = self._bind(
            "stopAvRecvService",
            [c_char_p, c_void_p],
            c_int,
            required=False,
        )
        self._set_qcloud_api_cred = self._bind(
            "setQcloudApiCred",
            [c_char_p, c_char_p],
            c_int,
            required=False,
        )
        self._set_stun_server = self._bind(
            "setStunServerToXp2p",
            [c_char_p, c_uint16],
            None,
            required=False,
        )
        self._set_cross_stun_turn = self._bind(
            "setCrossStunTurn",
            [c_bool],
            None,
            required=False,
        )
        self._delegate_http_flv = self._bind(
            "delegateHttpFlv",
            [c_char_p],
            c_char_p,
        )
        self._stop_service = self._bind(
            "stopService",
            [c_char_p],
            None,
            required=False,
        )

    def start_live_stream(
        self,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
        *,
        app_config: DreameLawnMowerXp2pAppConfig | None = None,
        command_timeout_us: int = DEFAULT_COMMAND_TIMEOUT_US,
        device_status_attempts: int = 60,
        device_status_retry_interval: float = 0.5,
        delegate_attempts: int = 60,
        delegate_retry_interval: float = 0.5,
    ) -> DreameLawnMowerXp2pLiveStreamSession:
        """Start native XP2P live video and return its local HTTP-FLV URL."""
        request = DreameLawnMowerXp2pLiveStreamRequest.from_runtime_inputs(inputs)
        config = app_config or DreameLawnMowerXp2pAppConfig()
        service_id = _encode(request.service_id)
        started = False
        av_recv_handle: Any | None = None
        stun_file_path: Path | None = None
        try:
            stun_file_path = self._set_stun_server_for_config(config)
            self._set_cross_stun_turn_for_config(config)
            qcloud_result = self._set_qcloud_api_cred_for_request(request)
            if qcloud_result not in (None, 0):
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P setQcloudApiCred failed with code "
                    f"{qcloud_result}."
                )
            start_result = int(
                self._start_service(
                    service_id,
                    _encode(request.product_id),
                    _encode(request.device_name),
                    config.to_native(),
                )
            )
            if start_result != 0:
                raise DreameLawnMowerVideoRuntimeError(
                    f"XP2P startService failed with code {start_result}."
                )
            started = True
            xp2p_info_result = int(
                self._set_device_xp2p_info(service_id, _encode(request.p2p_info))
            )
            if xp2p_info_result != 0:
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P setDeviceXp2pInfo failed with code "
                    f"{xp2p_info_result}."
                )
            device_status_result, device_status_response, device_status_code = (
                self._wait_for_ready_device_status(
                    request,
                    command_timeout_us=command_timeout_us,
                    attempts=device_status_attempts,
                    retry_interval=device_status_retry_interval,
                )
            )
            if device_status_result != 0:
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P device status command failed with code "
                    f"{device_status_result}."
                )
            if device_status_code != 0:
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P device status did not report ready; code "
                    f"{device_status_code}."
                )
            av_recv_handle = self._start_av_recv(
                _encode(request.delegate_id),
                _encode(request.flv_path),
                True,
            )
            if not av_recv_handle:
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P startAvRecvService returned a null handle."
                )
            stream_url_prefix = self._wait_for_flv_url_prefix(
                request,
                delegate_attempts=delegate_attempts,
                delegate_retry_interval=delegate_retry_interval,
            )
            if not stream_url_prefix:
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P delegateHttpFlv did not return a stream URL."
                )
            stream_url = _append_flv_path(stream_url_prefix, request.flv_path)
            return DreameLawnMowerXp2pLiveStreamSession(
                service_id=request.service_id,
                stream_url=stream_url,
                delegate_id=request.delegate_id,
                start_result=start_result,
                device_status_result=device_status_result,
                device_status_code=device_status_code,
                device_status_response=device_status_response,
                av_recv_handle=av_recv_handle,
                stun_file_path=str(stun_file_path) if stun_file_path else None,
            )
        except Exception:
            if started:
                self.stop_live_stream(
                    DreameLawnMowerXp2pLiveStreamSession(
                        service_id=request.service_id,
                        stream_url="",
                        delegate_id=request.delegate_id,
                        av_recv_handle=av_recv_handle,
                        stun_file_path=(
                            str(stun_file_path) if stun_file_path else None
                        ),
                    )
                )
            else:
                _remove_stun_file(stun_file_path)
            raise

    def stop_live_stream(self, session: DreameLawnMowerXp2pLiveStreamSession) -> None:
        """Stop a native XP2P live stream session."""
        runtime_id = _encode(session.delegate_id or session.service_id)
        if session.av_recv_handle is not None and self._stop_av_recv is not None:
            self._stop_av_recv(runtime_id, session.av_recv_handle)
        if self._stop_service is not None:
            self._stop_service(runtime_id)
        _remove_stun_file(session.stun_file_path)

    def _post_live_command(
        self,
        request: DreameLawnMowerXp2pLiveStreamRequest,
        *,
        command_timeout_us: int,
    ) -> tuple[int, bytes | None]:
        command = _encode(request.live_command)
        return self._post_command_bytes(
            request.service_id,
            command,
            command_timeout_us=command_timeout_us,
        )

    def _post_device_status_command(
        self,
        request: DreameLawnMowerXp2pLiveStreamRequest,
        *,
        command_timeout_us: int,
    ) -> tuple[int, bytes | None]:
        command = _encode(request.device_status_command)
        return self._post_command_bytes(
            request.delegate_id,
            command,
            command_timeout_us=command_timeout_us,
        )

    def _wait_for_ready_device_status(
        self,
        request: DreameLawnMowerXp2pLiveStreamRequest,
        *,
        command_timeout_us: int,
        attempts: int,
        retry_interval: float,
    ) -> tuple[int, bytes | None, int | None]:
        last_result = 0
        last_response = None
        last_code = None
        max_attempts = max(int(attempts), 1)
        for attempt in range(1, max_attempts + 1):
            last_result, last_response = self._post_device_status_command(
                request,
                command_timeout_us=command_timeout_us,
            )
            last_code = _decode_device_status_code(last_response)
            if last_result == 0 and last_code == 0:
                return last_result, last_response, last_code
            if attempt < max_attempts:
                time.sleep(max(retry_interval, 0.0))
        return last_result, last_response, last_code

    def _post_command_bytes(
        self,
        service_id: str,
        command: bytes,
        *,
        command_timeout_us: int,
    ) -> tuple[int, bytes | None]:
        command_buffer = (c_ubyte * len(command)).from_buffer_copy(command)
        response_buffer = POINTER(c_ubyte)()
        response_length = c_size_t()
        result = int(
            self._post_command(
                _encode(service_id),
                command_buffer,
                len(command),
                byref(response_buffer),
                byref(response_length),
                command_timeout_us,
            )
        )
        response = None
        if result == 0 and response_buffer and response_length.value:
            response = bytes(response_buffer[: response_length.value])
        return result, response

    def _wait_for_flv_url_prefix(
        self,
        request: DreameLawnMowerXp2pLiveStreamRequest,
        *,
        delegate_attempts: int,
        delegate_retry_interval: float,
    ) -> str | None:
        attempts = max(int(delegate_attempts), 1)
        delegate_id = _encode(request.delegate_id)
        for attempt in range(1, attempts + 1):
            stream_url_prefix = _decode(self._delegate_http_flv(delegate_id))
            if stream_url_prefix:
                return stream_url_prefix
            if attempt < attempts:
                time.sleep(max(delegate_retry_interval, 0.0))
        return None

    def _set_qcloud_api_cred_for_request(
        self,
        request: DreameLawnMowerXp2pLiveStreamRequest,
    ) -> int | None:
        if self._set_qcloud_api_cred is None:
            return None
        if not request.secret_id or not request.secret_key:
            return None
        return int(
            self._set_qcloud_api_cred(
                _encode(request.secret_id),
                _encode(request.secret_key),
            )
        )

    def _set_cross_stun_turn_for_config(
        self,
        config: DreameLawnMowerXp2pAppConfig,
    ) -> None:
        if self._set_cross_stun_turn is not None:
            self._set_cross_stun_turn(bool(config.cross))

    def _set_stun_server_for_config(
        self,
        config: DreameLawnMowerXp2pAppConfig,
    ) -> Path | None:
        if self._set_stun_server is None or not config.stun_servers:
            return None
        stun_file = _write_stun_file(config.stun_servers)
        try:
            self._set_stun_server(
                _encode(str(stun_file)),
                int(config.stun_port),
            )
        except Exception:
            _remove_stun_file(stun_file)
            raise
        return stun_file

    def _bind(
        self,
        name: str,
        argtypes: list[Any],
        restype: Any,
        *,
        required: bool = True,
    ) -> _NativeCallable | None:
        function = getattr(self._library, name, None)
        if function is None:
            if required:
                raise DreameLawnMowerVideoRuntimeError(
                    f"XP2P native library is missing required symbol: {name}."
                )
            return None
        try:
            function.argtypes = argtypes
            function.restype = restype
        except AttributeError:
            pass
        return function


def diagnose_native_xp2p_runtime(
    library_path: str | Path,
    *,
    library: Any | None = None,
) -> DreameLawnMowerXp2pRuntimeDiagnostics:
    """Return load/symbol readiness for a native XP2P runtime library."""
    path = str(library_path)
    inspection = _inspect_native_library_file(Path(path)) if library is None else {}
    try:
        loaded_library = library if library is not None else CDLL(path)
    except OSError as err:
        error = f"Could not load XP2P native library {path!r}: {err}"
        if inspection.get("platform_hint") == "android_jni":
            error += (
                " The file looks like Dreamehome's Android JNI XP2P library; "
                "it is not a Home Assistant host runtime. Provide a XP2P "
                "library or runner built for the Home Assistant operating "
                "system and CPU architecture."
            )
        elif inspection.get("platform_hint") == "android_bionic":
            error += (
                " The file looks like Tencent's Android XP2P runtime. It may "
                "export the expected C symbols, but Android/Bionic shared "
                "libraries cannot be loaded as a Home Assistant host runtime. "
                "Provide a XP2P library or runner built for the Home Assistant "
                "operating system and CPU architecture."
            )
        elif inspection.get("file_format") == "static_archive":
            error += (
                " The file is a static library archive, not a loadable Home "
                "Assistant runtime. Provide a dynamic XP2P library or runner "
                "built for the Home Assistant operating system and CPU "
                "architecture."
            )
        elif inspection.get("file_format") == "macho":
            error += (
                " The file is a Mach-O Apple-platform library. It cannot be "
                "loaded on a non-Apple Home Assistant host, and iOS builds "
                "are not Home Assistant host runtimes. Provide a dynamic XP2P "
                "library or runner built for the Home Assistant operating "
                "system and CPU architecture."
            )
        return DreameLawnMowerXp2pRuntimeDiagnostics(
            library_path=path,
            loadable=False,
            ready=False,
            error=error,
            **inspection,
        )

    missing_required = tuple(
        name
        for name in REQUIRED_XP2P_SYMBOLS
        if getattr(loaded_library, name, None) is None
    )
    missing_optional = tuple(
        name
        for name in OPTIONAL_XP2P_SYMBOLS
        if getattr(loaded_library, name, None) is None
    )
    ready = not missing_required
    error = None
    if missing_required:
        error = (
            "XP2P native library is missing required symbols: "
            + ", ".join(missing_required)
        )
    return DreameLawnMowerXp2pRuntimeDiagnostics(
        library_path=path,
        loadable=True,
        ready=ready,
        missing_required_symbols=missing_required,
        missing_optional_symbols=missing_optional,
        **inspection,
        error=error,
    )


def _inspect_native_library_file(path: Path) -> dict[str, Any]:
    inspection: dict[str, Any] = {}
    try:
        data = path.read_bytes()
    except OSError:
        return inspection

    if data.startswith(b"\x7fELF"):
        inspection["file_format"] = "elf"
        inspection["machine"] = _elf_machine_name(data)
    elif data.startswith(b"MZ"):
        inspection["file_format"] = "pe"
    elif data.startswith(b"!<arch>\n"):
        inspection["file_format"] = "static_archive"
        inspection["platform_hint"] = "static_archive"
    elif data.startswith(b"\xcf\xfa\xed\xfe") or data.startswith(b"\xfe\xed\xfa\xcf"):
        inspection["file_format"] = "macho"
        inspection["platform_hint"] = "apple_macho"

    android_jni = (
        inspection.get("file_format") == "elf"
        and any(marker in data for marker in ANDROID_XP2P_JNI_SYMBOL_MARKERS)
    )
    android_bionic = (
        inspection.get("file_format") == "elf"
        and any(marker in data for marker in ANDROID_BIONIC_LIBRARY_MARKERS)
    )
    inspection["android_jni_symbols_present"] = android_jni
    inspection["android_bionic_dependencies_present"] = android_bionic
    if android_jni:
        inspection["platform_hint"] = "android_jni"
    elif android_bionic:
        inspection["platform_hint"] = "android_bionic"
    return inspection


def _elf_machine_name(data: bytes) -> str | None:
    if len(data) < 20:
        return None
    endian = data[5]
    if endian == 1:
        machine = int.from_bytes(data[18:20], "little")
    elif endian == 2:
        machine = int.from_bytes(data[18:20], "big")
    else:
        return None
    return {
        3: "x86",
        40: "arm",
        62: "x86_64",
        183: "aarch64",
    }.get(machine, f"elf_machine_{machine}")


def _encode(value: str) -> bytes:
    return value.encode("utf-8")


def _decode(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _write_json_line(process: Any, payload: Mapping[str, Any]) -> None:
    if process.stdin is None:
        raise DreameLawnMowerVideoRuntimeError(
            "XP2P process runner stdin is not available."
        )
    process.stdin.write(json.dumps(payload) + "\n")
    process.stdin.flush()


def _read_json_line(
    process: Any,
    *,
    timeout: float,
    sensitive_values: Sequence[str] = (),
    stderr_thread: threading.Thread | None = None,
    stderr_tail: Sequence[str] = (),
) -> dict[str, Any]:
    if process.stdout is None:
        raise DreameLawnMowerVideoRuntimeError(
            "XP2P process runner stdout is not available."
        )

    result: dict[str, str | None] = {"line": None}

    def _readline() -> None:
        result["line"] = process.stdout.readline()

    thread = threading.Thread(target=_readline, daemon=True)
    thread.start()
    thread.join(timeout=max(timeout, 0.1))
    if thread.is_alive():
        _terminate_process(process)
        thread.join(timeout=0.5)
        _join_stream_drain_thread(stderr_thread)
        raise DreameLawnMowerVideoRuntimeError(
            f"XP2P process runner timed out after {timeout:g}s."
            + output_preview("stdout", result["line"], sensitive_values)
            + output_preview("stderr", _stream_tail_text(stderr_tail), sensitive_values)
        )
    line = result["line"]
    if not line:
        _join_stream_drain_thread(stderr_thread)
        raise DreameLawnMowerVideoRuntimeError(
            "XP2P process runner exited before returning stream metadata."
            + output_preview("stderr", _stream_tail_text(stderr_tail), sensitive_values)
        )
    try:
        response = json.loads(line)
    except json.JSONDecodeError as err:
        raise DreameLawnMowerVideoRuntimeError(
            "XP2P process runner returned invalid JSON."
            + output_preview("stdout", line, sensitive_values)
        ) from err
    if not isinstance(response, dict):
        raise DreameLawnMowerVideoRuntimeError(
            "XP2P process runner response must be a JSON object."
        )
    return response


def _process_alive(process: Any | None) -> bool:
    return bool(process is not None and process.poll() is None)


def _start_stream_drain_thread(
    stream: Any | None,
    *,
    name: str,
    tail: list[str] | None = None,
) -> threading.Thread | None:
    """Drain a runner output stream so persistent output cannot block it."""
    if stream is None:
        return None

    def _drain() -> None:
        try:
            while chunk := stream.read(8192):
                if tail is not None:
                    previous = tail[0] if tail else ""
                    tail[:] = [(previous + chunk)[-RUNNER_OUTPUT_PREVIEW_LIMIT:]]
        except (OSError, ValueError):
            return

    thread = threading.Thread(
        target=_drain,
        name=name,
        daemon=True,
    )
    thread.start()
    return thread


def _stream_tail_text(tail: Sequence[str]) -> str:
    """Return the bounded tail collected by a process output drain."""
    return tail[0] if tail else ""


def _join_stream_drain_thread(thread: threading.Thread | None) -> None:
    """Allow a completed runner output drain to settle without blocking cleanup."""
    if thread is not None:
        thread.join(timeout=0.5)


def _terminate_process(process: Any) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)


def _write_stun_file(stun_servers: Sequence[str]) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        encoding="utf-8",
        prefix="dreame-xp2p-stun-",
        suffix=".txt",
    )
    try:
        with handle:
            for server in stun_servers:
                if server:
                    handle.write(str(server).strip() + "\n")
    except Exception:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return Path(handle.name)


def _remove_stun_file(stun_file_path: str | Path | None) -> None:
    if not stun_file_path:
        return
    try:
        Path(stun_file_path).unlink(missing_ok=True)
    except OSError:
        pass


def _format_flv_path(template: str, channel: str) -> str:
    channel_value = quote(channel, safe="")
    try:
        return template.format(channel=channel_value)
    except (KeyError, IndexError, ValueError) as err:
        raise DreameLawnMowerVideoRuntimeError(
            f"Invalid XP2P FLV path template: {template!r}."
        ) from err


def _format_device_status_command(channel: str) -> str:
    channel_value = quote(channel, safe="")
    return (
        "action=inner_define&channel="
        f"{channel_value}&cmd=get_device_st&type=live&quality=standard"
    )


def _decode_device_status_code(response: bytes | None) -> int | None:
    if not response:
        return None
    try:
        payload = json.loads(response.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None
    if isinstance(payload, Sequence) and payload:
        first = payload[0]
    else:
        first = payload
    if not isinstance(first, Mapping):
        return None
    status = first.get("status")
    if isinstance(status, bool):
        return None
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def _append_flv_path(stream_url_prefix: str, flv_path: str) -> str:
    if "ipc.flv" in stream_url_prefix:
        return stream_url_prefix
    separator = (
        ""
        if stream_url_prefix.endswith("/") or flv_path.startswith("/")
        else "/"
    )
    return f"{stream_url_prefix}{separator}{flv_path.lstrip('/')}"
