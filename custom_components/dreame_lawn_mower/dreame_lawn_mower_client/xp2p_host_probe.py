"""Privacy-safe compatibility probe for the managed XP2P worker."""

from __future__ import annotations

import struct
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any

from .video_runner_diagnostics import safe_output_preview

_PROBE_REQUEST = b"invalid request"
_REQUEST_MAGIC = b"DXP1"
_REQUEST_FIELDS = 20
_RESPONSE_MAGIC = b"DXR1"
_BAD_REQUEST_STATUS = 1
_OK_STATUS = 0


def probe_xp2p_host_worker(
    command: Sequence[str],
    environment: Mapping[str, str],
    *,
    library_path: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Check that the worker and optional XP2P library load on this host."""
    request = (
        _PROBE_REQUEST if library_path is None else _library_probe_request(library_path)
    )
    expected_status = _BAD_REQUEST_STATUS if library_path is None else _OK_STATUS
    try:
        result = subprocess.run(
            command,
            input=request,
            capture_output=True,
            env=dict(environment),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ready": False,
            "stage": "response_wait",
            "exception": "TimeoutExpired",
        }
    except OSError as err:
        return {
            "ready": False,
            "stage": "process_start",
            "exception": type(err).__name__,
            "errno": err.errno,
        }

    response_status = _response_status(result.stdout)
    ready = result.returncode == expected_status and response_status == expected_status
    diagnostics: dict[str, Any] = {
        "ready": ready,
        "scope": "worker" if library_path is None else "library",
        "stage": "response_decode" if result.stdout else "response_wait",
        "returncode": result.returncode,
        "exit": format_process_returncode(result.returncode),
    }
    if response_status is not None:
        diagnostics["response_status"] = response_status
    native_trace = safe_output_preview(result.stderr, ())
    if native_trace:
        diagnostics["native_trace"] = native_trace
    return diagnostics


def _library_probe_request(library_path: str) -> bytes:
    """Return a credential-free request that stops after dlopen and dlsym."""
    fields = [""] * _REQUEST_FIELDS
    fields[0] = library_path
    fields[17] = "probe"
    payload = bytearray(_REQUEST_MAGIC)
    payload.extend(struct.pack("!I", _REQUEST_FIELDS))
    for field in fields:
        encoded = field.encode("utf-8")
        payload.extend(struct.pack("!I", len(encoded)))
        payload.extend(encoded)
    payload.extend(struct.pack("!QIIII", 1, 1, 0, 1, 0))
    return bytes(payload)


def format_process_returncode(returncode: int) -> str:
    """Return a stable description of a worker process exit."""
    if returncode < 0:
        return f"signal={-returncode}"
    return f"exit_code={returncode}"


def _response_status(payload: bytes) -> int | None:
    if len(payload) < 12 or payload[:4] != _RESPONSE_MAGIC:
        return None
    return struct.unpack("!I", payload[4:8])[0]
