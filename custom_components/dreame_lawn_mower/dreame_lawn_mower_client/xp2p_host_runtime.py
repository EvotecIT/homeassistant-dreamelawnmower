"""Python-managed Linux host runtime for Dreame/Tencent XP2P video."""

from __future__ import annotations

import os
import queue
import struct
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

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
    DreameLawnMowerXp2pDeviceConfig,
    resolve_xp2p_device_config,
)

_REQUEST_MAGIC = b"DXP1"
_RESPONSE_MAGIC = b"DXR1"
_REQUEST_FIELDS = 17
_MAX_RESPONSE_LENGTH = 64 * 1024
_DEFAULT_STATUS_ATTEMPTS = 60
_DEFAULT_RETRY_INTERVAL = 0.5
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
        """Return the process environment without any mower credentials."""
        environment = dict(os.environ)
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
    ) -> None:
        self.assets = assets
        self.startup_timeout = startup_timeout
        self.config_fetcher = config_fetcher

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
        self.assets.validate()
        request = DreameLawnMowerXp2pLiveStreamRequest.from_runtime_inputs(inputs)
        device_config = self.config_fetcher(inputs)
        stun_file = _write_stun_file(("43.158.113.38:20002",))
        command = self.assets.command()
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.assets.environment(),
            )
        except OSError as err:
            stun_file.unlink(missing_ok=True)
            raise DreameLawnMowerVideoRuntimeError(
                f"Could not start the XP2P host worker: {err}"
            ) from err

        stderr_tail: list[bytes] = []
        stderr_thread = _start_binary_drain_thread(
            process.stderr,
            name="dreame-xp2p-host-stderr",
            tail=stderr_tail,
        )
        sensitive_values = payload_sensitive_values(
            {"request": request.as_dict(redact=False)}
        )
        try:
            payload = _encode_request(
                self.assets,
                stun_file,
                request,
                device_config,
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
            process.stdin.write(payload)
            process.stdin.flush()
            status, response = _read_response(
                process.stdout,
                timeout=self.startup_timeout,
            )
            response_text = response.decode("utf-8", "replace")
            if status != 0:
                message = _WORKER_ERRORS.get(status, "XP2P host worker failed.")
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
            if not response_text.startswith(("http://", "https://")):
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P host worker returned an invalid stream URL."
                )
            if process.poll() is not None:
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P host worker exited before the stream could be used."
                )
            return DreameLawnMowerXp2pLiveStreamSession(
                service_id=request.service_id,
                stream_url=response_text,
                delegate_id=request.delegate_id,
                runtime="xp2p_python_host_runtime",
                stun_file_path=str(stun_file),
                runner_command=command,
                runner_process=process,
                runner_stderr_thread=stderr_thread,
            )
        except Exception as err:
            _terminate_process(process)
            _join_thread(stderr_thread)
            stun_file.unlink(missing_ok=True)
            if isinstance(err, DreameLawnMowerVideoRuntimeError):
                raise
            stderr = _tail_text(stderr_tail)
            detail = safe_output_preview(stderr, sensitive_values)
            raise DreameLawnMowerVideoRuntimeError(
                "XP2P host worker could not start." + (f" {detail}" if detail else "")
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


def _encode_request(
    assets: DreameLawnMowerXp2pHostAssets,
    stun_file: Path,
    request: DreameLawnMowerXp2pLiveStreamRequest,
    device_config: DreameLawnMowerXp2pDeviceConfig,
    *,
    command_timeout_us: int,
    device_status_attempts: int,
    device_status_retry_interval: float,
    delegate_attempts: int,
    delegate_retry_interval: float,
) -> bytes:
    fields = (
        str(assets.library_path),
        str(stun_file),
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
        device_config.server,
        device_config.ip,
        str(device_config.port),
        str(device_config.protocol_type),
        "1" if device_config.cross else "0",
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

    thread = threading.Thread(target=_read, name="dreame-xp2p-host-response")
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


def _start_binary_drain_thread(
    stream: BinaryIO | None,
    *,
    name: str,
    tail: list[bytes] | None = None,
) -> threading.Thread | None:
    if stream is None:
        return None

    def _drain() -> None:
        try:
            while chunk := stream.read(8192):
                if tail is not None:
                    previous = tail[0] if tail else b""
                    tail[:] = [(previous + chunk)[-RUNNER_OUTPUT_PREVIEW_LIMIT:]]
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


def _seconds_to_milliseconds(value: float) -> int:
    return max(int(max(value, 0.0) * 1000), 0)
