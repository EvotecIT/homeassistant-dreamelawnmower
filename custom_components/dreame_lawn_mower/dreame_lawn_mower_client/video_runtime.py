"""Native XP2P runtime bindings for Dreame mower live video."""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Mapping, Sequence
from ctypes import (
    CDLL,
    POINTER,
    Structure,
    byref,
    c_bool,
    c_char,
    c_char_p,
    c_int,
    c_size_t,
    c_ubyte,
    c_uint64,
    c_void_p,
)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from .models import DreameLawnMowerCameraStreamRuntimeInputs
from .video_runner_diagnostics import (
    completed_process_preview,
    output_preview,
    payload_sensitive_values,
    process_stderr_preview,
    safe_output_preview,
)

XP2P_PROTOCOL_AUTO = 0
XP2P_PROTOCOL_UDP = 1
XP2P_PROTOCOL_TCP = 2

DEFAULT_COMMAND_TIMEOUT_US = 7_500_000
REQUIRED_XP2P_SYMBOLS = (
    "startService",
    "postCommandRequestSync",
    "startAvRecvService",
    "delegateHttpFlv",
)
OPTIONAL_XP2P_SYMBOLS = (
    "setQcloudApiCred",
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
        ("server", c_char * 256),
        ("ip", c_char * 64),
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

    def to_native(self) -> _Xp2pAppConfig:
        """Return the ctypes struct expected by Tencent's XP2P C ABI."""
        config = _Xp2pAppConfig()
        config.server = _encode_fixed(self.server, 256)
        config.ip = _encode_fixed(self.ip, 64)
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
    secret_id: str | None = field(default=None, repr=False)
    secret_key: str | None = field(default=None, repr=False)

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
        return cls(
            service_id=service_id,
            delegate_id=delegate_id,
            stream_channel=stream_channel,
            product_id=str(inputs.product_id),
            device_name=str(inputs.device_name),
            p2p_info=str(inputs.p2p_info),
            secret_id=inputs.secret_id,
            secret_key=inputs.secret_key,
            flv_path=flv_path,
            live_command=inputs.live_command,
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
            "flv_path": self.flv_path,
            "live_command": self.live_command,
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
                "flv_path",
            ):
                payload[f"{key}_present"] = bool(payload.pop(key, None))
        return payload


@dataclass(slots=True)
class DreameLawnMowerXp2pLiveStreamSession:
    """Started native XP2P stream session."""

    service_id: str
    stream_url: str
    runtime: str = "native_xp2p"
    start_result: int = 0
    command_result: int | None = None
    command_response: bytes | None = field(default=None, repr=False)
    av_recv_handle: Any | None = field(default=None, repr=False)
    runner_command: tuple[str, ...] = field(default=(), repr=False)
    runner_session_id: str | None = None
    runner_process: Any | None = field(default=None, repr=False)

    def as_dict(self, *, redact: bool = False) -> dict[str, Any]:
        """Return safe stream session metadata."""
        payload = {
            "service_id": self.service_id,
            "stream_url": self.stream_url,
            "runtime": self.runtime,
            "start_result": self.start_result,
            "command_result": self.command_result,
            "command_response_present": bool(self.command_response),
            "av_recv_started": self.av_recv_handle is not None,
            "runner_command": self.runner_command,
            "runner_session_id_present": bool(self.runner_session_id),
            "runner_process_alive": _process_alive(self.runner_process),
        }
        if redact:
            for key in ("service_id", "stream_url"):
                payload[f"{key}_present"] = bool(payload.pop(key, None))
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

        try:
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
            return DreameLawnMowerXp2pLiveStreamSession(
                service_id=_as_text(response.get("service_id")) or request.service_id,
                stream_url=stream_url,
                runtime="xp2p_process_runner",
                runner_command=self.command,
                runner_session_id=_as_text(response.get("runner_session_id")),
                runner_process=process,
            )
        except Exception:
            _terminate_process(process)
            raise

    def stop_live_stream(self, session: DreameLawnMowerXp2pLiveStreamSession) -> None:
        """Stop the persistent XP2P runner that owns the stream session."""
        process = session.runner_process
        if process is None:
            return
        if process.poll() is not None:
            return
        try:
            _write_json_line(
                process,
                {
                    "operation": "stop",
                    "session": {
                        "service_id": session.service_id,
                        "stream_url": session.stream_url,
                        "runner_session_id": session.runner_session_id,
                    },
                },
            )
            process.wait(timeout=self.timeout)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
            _terminate_process(process)


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
            [c_char_p, c_char_p, c_char_p, c_char_p, _Xp2pAppConfig],
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
    ) -> DreameLawnMowerXp2pLiveStreamSession:
        """Start native XP2P live video and return its local HTTP-FLV URL."""
        request = DreameLawnMowerXp2pLiveStreamRequest.from_runtime_inputs(inputs)
        config = app_config or DreameLawnMowerXp2pAppConfig()
        service_id = _encode(request.service_id)
        started = False
        av_recv_handle: Any | None = None
        try:
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
                    _encode(request.p2p_info),
                    config.to_native(),
                )
            )
            if start_result != 0:
                raise DreameLawnMowerVideoRuntimeError(
                    f"XP2P startService failed with code {start_result}."
                )
            started = True
            command_result, command_response = self._post_live_command(
                request,
                command_timeout_us=command_timeout_us,
            )
            if command_result != 0:
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P postCommandRequestSync failed with code "
                    f"{command_result}."
                )
            av_recv_handle = self._start_av_recv(
                service_id,
                _encode(request.flv_path),
                True,
            )
            stream_url_prefix_raw = self._delegate_http_flv(
                _encode(request.delegate_id)
            )
            stream_url_prefix = _decode(stream_url_prefix_raw)
            if not stream_url_prefix:
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P delegateHttpFlv did not return a stream URL."
                )
            stream_url = _append_flv_path(stream_url_prefix, request.flv_path)
            return DreameLawnMowerXp2pLiveStreamSession(
                service_id=request.service_id,
                stream_url=stream_url,
                start_result=start_result,
                command_result=command_result,
                command_response=command_response,
                av_recv_handle=av_recv_handle,
            )
        except Exception:
            if started:
                self.stop_live_stream(
                    DreameLawnMowerXp2pLiveStreamSession(
                        service_id=request.service_id,
                        stream_url="",
                        av_recv_handle=av_recv_handle,
                    )
                )
            raise

    def stop_live_stream(self, session: DreameLawnMowerXp2pLiveStreamSession) -> None:
        """Stop a native XP2P live stream session."""
        service_id = _encode(session.service_id)
        if session.av_recv_handle is not None and self._stop_av_recv is not None:
            self._stop_av_recv(service_id, session.av_recv_handle)
        if self._stop_service is not None:
            self._stop_service(service_id)

    def _post_live_command(
        self,
        request: DreameLawnMowerXp2pLiveStreamRequest,
        *,
        command_timeout_us: int,
    ) -> tuple[int, bytes | None]:
        command = _encode(request.live_command)
        command_buffer = (c_ubyte * len(command)).from_buffer_copy(command)
        response_buffer = POINTER(c_ubyte)()
        response_length = c_size_t()
        result = int(
            self._post_command(
                _encode(request.service_id),
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
    try:
        loaded_library = library if library is not None else CDLL(path)
    except OSError as err:
        return DreameLawnMowerXp2pRuntimeDiagnostics(
            library_path=path,
            loadable=False,
            ready=False,
            error=f"Could not load XP2P native library {path!r}: {err}",
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
        error=error,
    )


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
        raise DreameLawnMowerVideoRuntimeError(
            f"XP2P process runner timed out after {timeout:g}s."
            + process_stderr_preview(process, sensitive_values)
        )
    line = result["line"]
    if not line:
        raise DreameLawnMowerVideoRuntimeError(
            "XP2P process runner exited before returning stream metadata."
            + process_stderr_preview(process, sensitive_values)
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


def _terminate_process(process: Any) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)


def _format_flv_path(template: str, channel: str) -> str:
    channel_value = quote(channel, safe="")
    try:
        return template.format(channel=channel_value)
    except (KeyError, IndexError, ValueError) as err:
        raise DreameLawnMowerVideoRuntimeError(
            f"Invalid XP2P FLV path template: {template!r}."
        ) from err


def _append_flv_path(stream_url_prefix: str, flv_path: str) -> str:
    if "ipc.flv" in stream_url_prefix:
        return stream_url_prefix
    separator = (
        ""
        if stream_url_prefix.endswith("/") or flv_path.startswith("/")
        else "/"
    )
    return f"{stream_url_prefix}{separator}{flv_path.lstrip('/')}"


def _encode_fixed(value: str, size: int) -> bytes:
    encoded = _encode(value)
    if len(encoded) >= size:
        raise DreameLawnMowerVideoRuntimeError(
            f"XP2P app config value is too long for {size}-byte field."
        )
    return encoded
