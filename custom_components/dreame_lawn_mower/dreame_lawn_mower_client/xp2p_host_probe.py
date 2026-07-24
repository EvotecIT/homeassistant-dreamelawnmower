"""Privacy-safe compatibility probe for the managed XP2P worker."""

from __future__ import annotations

import struct
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any

from .video_runner_diagnostics import safe_output_preview

_PROBE_REQUEST = b"invalid request"
_RESPONSE_MAGIC = b"DXR1"
_EXPECTED_STATUS = 1


def probe_xp2p_host_worker(
    command: Sequence[str],
    environment: Mapping[str, str],
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Check that the isolated worker can read and answer on this host."""
    try:
        result = subprocess.run(
            command,
            input=_PROBE_REQUEST,
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
    ready = result.returncode == _EXPECTED_STATUS and response_status == (
        _EXPECTED_STATUS
    )
    diagnostics: dict[str, Any] = {
        "ready": ready,
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


def format_process_returncode(returncode: int) -> str:
    """Return a stable description of a worker process exit."""
    if returncode < 0:
        return f"signal={-returncode}"
    return f"exit_code={returncode}"


def _response_status(payload: bytes) -> int | None:
    if len(payload) < 12 or payload[:4] != _RESPONSE_MAGIC:
        return None
    return struct.unpack("!I", payload[4:8])[0]
