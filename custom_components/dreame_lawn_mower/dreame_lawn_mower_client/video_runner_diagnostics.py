"""Redacted diagnostics for external XP2P video runners."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from typing import Any

RUNNER_OUTPUT_PREVIEW_LIMIT = 400
SENSITIVE_RUNNER_PAYLOAD_KEYS = {
    "device_name",
    "flv_path",
    "p2p_info",
    "product_id",
    "runner_session_id",
    "secret_id",
    "secret_key",
    "service_id",
    "stream_url",
}


def payload_sensitive_values(payload: Any) -> tuple[str, ...]:
    """Return redaction candidates from a runner request/session payload."""
    values: list[str] = []
    _collect_sensitive_values(payload, values)
    return tuple(dict.fromkeys(item for item in values if item))


def completed_process_preview(
    completed: subprocess.CompletedProcess[str],
    sensitive_values: Sequence[str],
) -> str:
    """Return sanitized stdout/stderr previews for a completed runner."""
    return (
        output_preview("stdout", completed.stdout, sensitive_values)
        + output_preview("stderr", completed.stderr, sensitive_values)
    )


def process_stderr_preview(process: Any, sensitive_values: Sequence[str]) -> str:
    """Return a sanitized stderr preview after a runner exits."""
    if process.stderr is None:
        return ""
    if process.poll() is None:
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            return ""
    try:
        stderr = process.stderr.read()
    except OSError:
        return ""
    return output_preview("stderr", stderr, sensitive_values)


def output_preview(
    label: str,
    value: Any,
    sensitive_values: Sequence[str],
) -> str:
    """Return a labeled sanitized preview fragment."""
    preview = safe_output_preview(value, sensitive_values)
    if not preview:
        return ""
    return f" {label}={preview!r}"


def safe_output_preview(value: Any, sensitive_values: Sequence[str]) -> str:
    """Return a bounded sanitized text preview."""
    text = _as_text(value)
    if not text:
        return ""
    text = " ".join(text.split())
    for sensitive in sorted(sensitive_values, key=len, reverse=True):
        if sensitive:
            text = text.replace(sensitive, "[redacted]")
    if len(text) > RUNNER_OUTPUT_PREVIEW_LIMIT:
        text = text[-RUNNER_OUTPUT_PREVIEW_LIMIT:]
    return text


def _collect_sensitive_values(value: Any, values: list[str], key: str = "") -> None:
    if isinstance(value, Mapping):
        for item_key, item_value in value.items():
            _collect_sensitive_values(item_value, values, str(item_key))
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for item in value:
            _collect_sensitive_values(item, values, key)
        return
    if key in SENSITIVE_RUNNER_PAYLOAD_KEYS:
        text = _as_text(value)
        if text:
            values.append(text)


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
