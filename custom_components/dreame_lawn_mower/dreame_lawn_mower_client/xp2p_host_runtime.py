"""Python-managed Linux host runtime for Dreame/Tencent XP2P video."""

from __future__ import annotations

import os
import queue
import struct
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .lan_video import (
    DEFAULT_LAN_DISCOVERY_TIMEOUT,
    DreameLawnMowerLanVideoEndpoint,
    discover_lan_video_endpoint,
)
from .models import DreameLawnMowerCameraStreamRuntimeInputs
from .video_runner_diagnostics import (
    RUNNER_OUTPUT_PREVIEW_LIMIT,
    payload_sensitive_values,
    safe_output_preview,
)
from .video_runtime import (
    DEFAULT_COMMAND_TIMEOUT_US,
    DreameLawnMowerVideoRuntimeError,
    DreameLawnMowerXp2pLiveStreamRequest,
    DreameLawnMowerXp2pLiveStreamSession,
)
from .xp2p_config import (
    DEFAULT_XP2P_STUN_PORT,
    DreameLawnMowerXp2pDeviceConfig,
    resolve_xp2p_device_config,
)

_REQUEST_MAGIC = b"DXP1"
_RESPONSE_MAGIC = b"DXR1"
_REQUEST_FIELDS = 20
_MAX_RESPONSE_LENGTH = 64 * 1024
_DEFAULT_STATUS_ATTEMPTS = 60
_DEFAULT_RETRY_INTERVAL = 0.5
_DEFAULT_STUN_SERVER = "43.158.113.38:20002"
_STARTUP_TIMEOUT_MARGIN = 5.0
_WORKER_ERRORS = {
    1: "Invalid XP2P worker request.",
    2: "Could not load the XP2P runtime.",
    3: "The XP2P runtime is missing required symbols.",
    10: "XP2P QCloud credential setup failed.",
    11: "XP2P service startup failed.",
    12: "XP2P device configuration failed.",
    13: "XP2P device status command failed.",
    14: "The mower XP2P channel did not become ready.",
    15: "XP2P AV receive startup failed.",
    16: "XP2P did not expose a local FLV URL.",
    17: "XP2P did not reach its native ready event.",
}


def _startup_response_timeout(
    *,
    command_timeout_us: int = DEFAULT_COMMAND_TIMEOUT_US,
    device_status_attempts: int = _DEFAULT_STATUS_ATTEMPTS,
    device_status_retry_interval: float = _DEFAULT_RETRY_INTERVAL,
    delegate_attempts: int = _DEFAULT_STATUS_ATTEMPTS,
    delegate_retry_interval: float = _DEFAULT_RETRY_INTERVAL,
    minimum: float = 45.0,
    include_device_status: bool = True,
) -> float:
    """Return a parent timeout that covers every configured worker retry."""
    status_attempts = max(int(device_status_attempts), 1)
    status_interval = max(float(device_status_retry_interval), 0.0)
    delegate_count = max(int(delegate_attempts), 1)
    delegate_interval = max(float(delegate_retry_interval), 0.0)
    command_timeout = max(int(command_timeout_us), 1) / 1_000_000
    ready_event_budget = status_attempts * status_interval
    device_status_budget = (
        status_attempts * (command_timeout + status_interval)
        if include_device_status
        else 0.0
    )
    delegate_budget = delegate_count * delegate_interval
    return max(
        float(minimum),
        ready_event_budget
        + device_status_budget
        + delegate_budget
        + _STARTUP_TIMEOUT_MARGIN,
    )


DEFAULT_XP2P_HOST_STARTUP_TIMEOUT = _startup_response_timeout()


@dataclass(slots=True, frozen=True)
class DreameLawnMowerXp2pHostAssets:
    """Verified files used to launch the Bionic XP2P worker on Linux."""

    worker_path: Path
    linker_path: Path
    library_path: Path
    library_search_paths: tuple[Path, ...]
    qemu_path: Path | None = None

    def command(self) -> tuple[str, ...]:
        """Return the non-secret worker command."""
        worker = str(self.worker_path)
        linker = str(self.linker_path)
        if self.qemu_path is None:
            return (linker, worker)
        return (
            str(self.qemu_path),
            "-cpu",
            "max",
            "-E",
            f"LD_LIBRARY_PATH={self.library_search_path}",
            linker,
            worker,
        )

    @property
    def library_search_path(self) -> str:
        """Return the guest Bionic library search path."""
        return os.pathsep.join(str(path) for path in self.library_search_paths)

    def environment(self) -> dict[str, str]:
        """Return only the process environment required by the worker."""
        environment: dict[str, str] = {}
        if self.qemu_path is None:
            environment["LD_LIBRARY_PATH"] = self.library_search_path
        return environment

    def validate(self) -> None:
        """Reject incomplete runtime layouts before starting video."""
        required = {
            "worker": self.worker_path,
            "Bionic linker": self.linker_path,
            "Tencent XP2P library": self.library_path,
        }
        if self.qemu_path is not None:
            required["qemu-aarch64"] = self.qemu_path
        missing = [name for name, path in required.items() if not path.is_file()]
        missing.extend(
            f"library directory {path}"
            for path in self.library_search_paths
            if not path.is_dir()
        )
        executables = {
            "worker": self.worker_path,
            "Bionic linker": self.linker_path,
        }
        if self.qemu_path is not None:
            executables["qemu-aarch64"] = self.qemu_path
        missing.extend(
            f"executable {name}"
            for name, path in executables.items()
            if path.is_file() and not os.access(path, os.X_OK)
        )
        if missing:
            raise DreameLawnMowerVideoRuntimeError(
                "XP2P host runtime is incomplete: " + ", ".join(missing) + "."
            )


class DreameLawnMowerXp2pHostRuntime:
    """Own the XP2P compatibility worker entirely from the Python client."""

    def __init__(
        self,
        assets: DreameLawnMowerXp2pHostAssets,
        *,
        startup_timeout: float = 45.0,
        config_fetcher: Callable[
            [DreameLawnMowerCameraStreamRuntimeInputs],
            DreameLawnMowerXp2pDeviceConfig,
        ] = resolve_xp2p_device_config,
        lan_discoverer: Callable[..., DreameLawnMowerLanVideoEndpoint] = (
            discover_lan_video_endpoint
        ),
    ) -> None:
        self.assets = assets
        self.startup_timeout = startup_timeout
        self.config_fetcher = config_fetcher
        self.lan_discoverer = lan_discoverer
        self.last_failure: dict[str, Any] | None = None

    def start_live_stream(
        self,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
        *,
        command_timeout_us: int = DEFAULT_COMMAND_TIMEOUT_US,
        device_status_attempts: int = _DEFAULT_STATUS_ATTEMPTS,
        device_status_retry_interval: float = _DEFAULT_RETRY_INTERVAL,
        delegate_attempts: int = _DEFAULT_STATUS_ATTEMPTS,
        delegate_retry_interval: float = _DEFAULT_RETRY_INTERVAL,
    ) -> DreameLawnMowerXp2pLiveStreamSession:
        """Start a host-owned local HTTP-FLV session."""
        self.last_failure = None
        self.assets.validate()
        request = DreameLawnMowerXp2pLiveStreamRequest.from_runtime_inputs(inputs)
        device_config = self.config_fetcher(inputs)
        stun_file = _write_stun_file(_stun_servers(device_config))
        return self._start_worker(
            request,
            transport="cloud",
            endpoint=None,
            stun_file=stun_file,
            device_config=device_config,
            command_timeout_us=command_timeout_us,
            device_status_attempts=device_status_attempts,
            device_status_retry_interval=device_status_retry_interval,
            delegate_attempts=delegate_attempts,
            delegate_retry_interval=delegate_retry_interval,
        )

    def start_lan_stream(
        self,
        inputs: DreameLawnMowerCameraStreamRuntimeInputs,
        *,
        endpoint: DreameLawnMowerLanVideoEndpoint | None = None,
        preferred_address: str | None = None,
        discovery_timeout: float = DEFAULT_LAN_DISCOVERY_TIMEOUT,
        device_status_attempts: int = _DEFAULT_STATUS_ATTEMPTS,
        device_status_retry_interval: float = _DEFAULT_RETRY_INTERVAL,
        delegate_attempts: int = _DEFAULT_STATUS_ATTEMPTS,
        delegate_retry_interval: float = _DEFAULT_RETRY_INTERVAL,
    ) -> DreameLawnMowerXp2pLiveStreamSession:
        """Start direct same-LAN HTTP-FLV without cloud config or credentials."""
        self.last_failure = None
        self.assets.validate()
        request = DreameLawnMowerXp2pLiveStreamRequest.from_lan_runtime_inputs(inputs)
        if endpoint is None:
            endpoint = self.lan_discoverer(
                request.product_id,
                device_name=request.device_name,
                client_token=inputs.lan_client_token,
                preferred_address=preferred_address,
                timeout=discovery_timeout,
            )
        if (
            endpoint.product_id != request.product_id
            or endpoint.device_name != request.device_name
        ):
            raise DreameLawnMowerVideoRuntimeError(
                "The discovered LAN endpoint does not match the mower video identity."
            )
        return self._start_worker(
            request,
            transport="lan",
            endpoint=endpoint,
            stun_file=None,
            device_config=None,
            command_timeout_us=DEFAULT_COMMAND_TIMEOUT_US,
            device_status_attempts=device_status_attempts,
            device_status_retry_interval=device_status_retry_interval,
            delegate_attempts=delegate_attempts,
            delegate_retry_interval=delegate_retry_interval,
        )

    def _start_worker(
        self,
        request: DreameLawnMowerXp2pLiveStreamRequest,
        *,
        transport: str,
        endpoint: DreameLawnMowerLanVideoEndpoint | None,
        stun_file: Path | None,
        device_config: DreameLawnMowerXp2pDeviceConfig | None,
        command_timeout_us: int,
        device_status_attempts: int,
        device_status_retry_interval: float,
        delegate_attempts: int,
        delegate_retry_interval: float,
    ) -> DreameLawnMowerXp2pLiveStreamSession:
        """Launch the isolated Bionic worker for cloud or direct-LAN transport."""
        command = self.assets.command()
        sensitive_values = payload_sensitive_values(
            {"request": request.as_dict(redact=False)}
        )
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.assets.environment(),
            )
        except OSError as err:
            if stun_file is not None:
                stun_file.unlink(missing_ok=True)
            self.last_failure = {
                "stage": "process_start",
                "exception": type(err).__name__,
                "errno": err.errno,
            }
            raise DreameLawnMowerVideoRuntimeError(
                f"Could not start the XP2P host worker: {err}"
            ) from err

        stderr_tail: list[bytes] = []
        stderr_thread = _start_binary_drain_thread(
            process.stderr,
            name="dreame-xp2p-host-stderr",
            tail=stderr_tail,
            limit=_redaction_buffer_limit(sensitive_values),
        )
        startup_stage = "request_encode"
        worker_status: int | None = None
        try:
            payload = _encode_request(
                self.assets,
                stun_file,
                request,
                device_config,
                transport=transport,
                endpoint=endpoint,
                command_timeout_us=command_timeout_us,
                device_status_attempts=device_status_attempts,
                device_status_retry_interval=device_status_retry_interval,
                delegate_attempts=delegate_attempts,
                delegate_retry_interval=delegate_retry_interval,
            )
            if process.stdin is None:
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P host worker stdin is unavailable."
                )
            startup_stage = "request_write"
            process.stdin.write(payload)
            process.stdin.flush()
            startup_stage = "response_wait"
            worker_status, response = _read_response(
                process.stdout,
                timeout=_startup_response_timeout(
                    command_timeout_us=command_timeout_us,
                    device_status_attempts=device_status_attempts,
                    device_status_retry_interval=device_status_retry_interval,
                    delegate_attempts=delegate_attempts,
                    delegate_retry_interval=delegate_retry_interval,
                    minimum=self.startup_timeout,
                    include_device_status=transport != "lan",
                ),
            )
            startup_stage = "response_decode"
            response_text = response.decode("utf-8", "replace")
            if worker_status != 0:
                message = _WORKER_ERRORS.get(
                    worker_status,
                    "XP2P host worker failed.",
                )
                detail = safe_output_preview(response_text, sensitive_values)
                if detail and detail != message:
                    message = f"{message} {detail}"
                native_tail = safe_output_preview(
                    _tail_text(stderr_tail),
                    sensitive_values,
                )
                if native_tail:
                    message = f"{message} Native diagnostics: {native_tail}"
                raise DreameLawnMowerVideoRuntimeError(message)
            stream_link_mode, stream_url = _decode_success_payload(response_text)
            if not stream_url.startswith(("http://", "https://")):
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P host worker returned an invalid stream URL."
                )
            if process.poll() is not None:
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P host worker exited before the stream could be used."
                )
            return DreameLawnMowerXp2pLiveStreamSession(
                service_id=request.service_id,
                stream_url=stream_url,
                delegate_id=request.delegate_id,
                runtime="xp2p_python_host_runtime",
                transport=transport,
                lan_endpoint_address=(endpoint.address if endpoint else None),
                lan_endpoint_port=(endpoint.port if endpoint else None),
                stream_link_mode=stream_link_mode,
                stun_file_path=str(stun_file) if stun_file is not None else None,
                runner_command=command,
                runner_process=process,
                runner_stderr_thread=stderr_thread,
            )
        except Exception as err:
            failure_returncode = _failure_returncode(process)
            _terminate_process(process)
            _join_thread(stderr_thread)
            if stun_file is not None:
                stun_file.unlink(missing_ok=True)
            details = [
                f"stage={startup_stage}",
                f"exception={type(err).__name__}",
            ]
            if failure_returncode is not None:
                details.append(_format_worker_returncode(failure_returncode))
            error_detail = safe_output_preview(err, sensitive_values)
            if error_detail and not isinstance(err, DreameLawnMowerVideoRuntimeError):
                details.append(f"error={error_detail}")
            native_detail = safe_output_preview(
                _tail_text(stderr_tail),
                sensitive_values,
            )
            if native_detail:
                details.append(f"native={native_detail}")
            failure = {
                "stage": startup_stage,
                "exception": type(err).__name__,
            }
            if worker_status is not None:
                failure["worker_status"] = worker_status
            if failure_returncode is not None:
                failure["returncode"] = failure_returncode
                failure["exit"] = _format_worker_returncode(failure_returncode)
            if native_detail:
                failure["native_trace"] = native_detail
            self.last_failure = failure
            if isinstance(err, DreameLawnMowerVideoRuntimeError):
                if startup_stage != "response_wait":
                    raise
                message = error_detail or "XP2P host worker response failed."
                raise DreameLawnMowerVideoRuntimeError(
                    message + " (" + ", ".join(details) + ")."
                ) from err
            raise DreameLawnMowerVideoRuntimeError(
                "XP2P host worker could not start (" + ", ".join(details) + ")."
            ) from err

    def stop_live_stream(self, session: DreameLawnMowerXp2pLiveStreamSession) -> None:
        """Stop the worker and remove its transient STUN configuration."""
        process = session.runner_process
        try:
            if process is not None and process.poll() is None:
                try:
                    if process.stdin is not None:
                        process.stdin.write(b"\0")
                        process.stdin.flush()
                    process.wait(timeout=10.0)
                except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                    _terminate_process(process)
        finally:
            _join_thread(session.runner_stderr_thread)
            if session.stun_file_path:
                Path(session.stun_file_path).unlink(missing_ok=True)

    def refresh_stream_link_mode(
        self,
        session: DreameLawnMowerXp2pLiveStreamSession,
        *,
        attempts: int = 5,
        retry_interval: float = 0.2,
        timeout: float = 2.0,
    ) -> int | None:
        """Ask the live worker whether media is direct (62) or relayed (63)."""
        process = session.runner_process
        if (
            process is None
            or process.poll() is not None
            or process.stdin is None
            or process.stdout is None
        ):
            return session.stream_link_mode
        for attempt in range(max(int(attempts), 1)):
            try:
                process.stdin.write(b"Q")
                process.stdin.flush()
                status, response = _read_response(process.stdout, timeout=timeout)
            except (BrokenPipeError, OSError, DreameLawnMowerVideoRuntimeError):
                return session.stream_link_mode
            if status == 0:
                value = response.decode("ascii", "replace").strip()
                if value in {"62", "63"}:
                    session.stream_link_mode = int(value)
                    return session.stream_link_mode
            if attempt + 1 < attempts:
                time.sleep(max(float(retry_interval), 0.0))
        return session.stream_link_mode


def _decode_success_payload(value: str) -> tuple[int | None, str]:
    """Decode the worker's route mode while accepting legacy URL-only workers."""
    first, separator, remainder = value.partition("\n")
    if separator and first in {"0", "62", "63"}:
        mode = int(first)
        return (mode or None), remainder
    return None, value


def _encode_request(
    assets: DreameLawnMowerXp2pHostAssets,
    stun_file: Path | None,
    request: DreameLawnMowerXp2pLiveStreamRequest,
    device_config: DreameLawnMowerXp2pDeviceConfig | None,
    *,
    transport: str = "cloud",
    endpoint: DreameLawnMowerLanVideoEndpoint | None = None,
    command_timeout_us: int,
    device_status_attempts: int,
    device_status_retry_interval: float,
    delegate_attempts: int,
    delegate_retry_interval: float,
) -> bytes:
    config = device_config or DreameLawnMowerXp2pDeviceConfig()
    fields = (
        str(assets.library_path),
        str(stun_file) if stun_file is not None else "",
        request.service_id,
        request.delegate_id,
        request.product_id,
        request.device_name,
        request.p2p_info,
        request.secret_id or "",
        request.secret_key or "",
        request.flv_path,
        request.device_status_command,
        request.live_command,
        config.server,
        config.ip,
        str(config.port),
        str(config.protocol_type),
        "1" if config.cross else "0",
        transport,
        endpoint.address if endpoint is not None else "",
        str(endpoint.port) if endpoint is not None else "",
    )
    encoded = [value.encode("utf-8") for value in fields]
    payload = bytearray(_REQUEST_MAGIC)
    payload.extend(struct.pack("!I", _REQUEST_FIELDS))
    for value in encoded:
        payload.extend(struct.pack("!I", len(value)))
        payload.extend(value)
    payload.extend(
        struct.pack(
            "!QIIII",
            max(int(command_timeout_us), 1),
            max(int(device_status_attempts), 1),
            _seconds_to_milliseconds(device_status_retry_interval),
            max(int(delegate_attempts), 1),
            _seconds_to_milliseconds(delegate_retry_interval),
        )
    )
    return bytes(payload)


def _read_response(
    stream: BinaryIO | None,
    *,
    timeout: float,
) -> tuple[int, bytes]:
    if stream is None:
        raise DreameLawnMowerVideoRuntimeError(
            "XP2P host worker stdout is unavailable."
        )
    result: queue.Queue[tuple[int, bytes] | BaseException] = queue.Queue(maxsize=1)

    def _read() -> None:
        try:
            header = _read_exact(stream, 12)
            if header[:4] != _RESPONSE_MAGIC:
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P host worker returned an invalid response."
                )
            status, length = struct.unpack("!II", header[4:])
            if length > _MAX_RESPONSE_LENGTH:
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P host worker response is too large."
                )
            result.put((status, _read_exact(stream, length)))
        except BaseException as err:  # noqa: BLE001 - handed to caller thread.
            result.put(err)

    thread = threading.Thread(
        target=_read,
        name="dreame-xp2p-host-response",
        daemon=True,
    )
    thread.start()
    try:
        value = result.get(timeout=max(timeout, 0.1))
    except queue.Empty as err:
        raise DreameLawnMowerVideoRuntimeError(
            f"XP2P host worker timed out after {timeout:g}s."
        ) from err
    if isinstance(value, BaseException):
        raise value
    thread.join(timeout=0.5)
    return value


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = stream.read(length - len(result))
        if not chunk:
            raise DreameLawnMowerVideoRuntimeError(
                "XP2P host worker closed its response pipe."
            )
        result.extend(chunk)
    return bytes(result)


def _write_stun_file(stun_servers: Sequence[str]) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w",
        delete=False,
        encoding="utf-8",
        prefix="dreame-xp2p-host-stun-",
        suffix=".txt",
    )
    with handle:
        for server in stun_servers:
            handle.write(server + "\n")
    return Path(handle.name)


def _stun_servers(config: DreameLawnMowerXp2pDeviceConfig) -> tuple[str, ...]:
    """Return fetched regional STUN endpoints with the known SDK fallback."""
    port = int(config.port)
    if not 0 < port <= 65535:
        port = DEFAULT_XP2P_STUN_PORT
    endpoints = tuple(
        dict.fromkeys(
            f"{host.strip()}:{port}"
            for host in (config.server, config.ip)
            if host and host.strip()
        )
    )
    return endpoints or (_DEFAULT_STUN_SERVER,)


def _start_binary_drain_thread(
    stream: BinaryIO | None,
    *,
    name: str,
    tail: list[bytes] | None = None,
    limit: int = RUNNER_OUTPUT_PREVIEW_LIMIT,
) -> threading.Thread | None:
    if stream is None:
        return None

    def _drain() -> None:
        try:
            while chunk := stream.read(8192):
                if tail is not None:
                    previous = tail[0] if tail else b""
                    tail[:] = [(previous + chunk)[-max(limit, 1) :]]
        except (OSError, ValueError):
            return

    thread = threading.Thread(target=_drain, name=name, daemon=True)
    thread.start()
    return thread


def _tail_text(tail: Sequence[bytes]) -> str:
    return tail[0].decode("utf-8", "replace") if tail else ""


def _join_thread(thread: threading.Thread | None) -> None:
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


def _failure_returncode(process: Any) -> int | None:
    """Capture an already-exited worker without mistaking cleanup for failure."""
    returncode = process.poll()
    if returncode is not None:
        return int(returncode)
    try:
        return int(process.wait(timeout=0.2))
    except subprocess.TimeoutExpired:
        return None


def _format_worker_returncode(returncode: int) -> str:
    """Return a stable, privacy-safe worker exit description."""
    if returncode < 0:
        return f"signal={-returncode}"
    return f"exit_code={returncode}"


def _redaction_buffer_limit(sensitive_values: Sequence[str]) -> int:
    """Retain enough raw tail to redact a credential crossing the preview edge."""
    longest_secret = max(
        (len(value.encode("utf-8")) for value in sensitive_values),
        default=0,
    )
    return RUNNER_OUTPUT_PREVIEW_LIMIT + longest_secret


def _seconds_to_milliseconds(value: float) -> int:
    return max(int(max(value, 0.0) * 1000), 0)
