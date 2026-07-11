"""Decoding, health, and configuration helpers for the live video entity."""

from __future__ import annotations

import io
import platform
import shlex
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .dreame_lawn_mower_client.stream_health import (
    DreameLawnMowerStreamUrlProbeResult,
    probe_stream_url,
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerVideoRuntimeError,
)

_STREAM_HEALTH_ATTEMPTS = 3
_STREAM_HEALTH_RETRY_INTERVAL = 0.5
_STREAM_HEALTH_TIMEOUT = 3.0
_STREAM_HEALTH_BYTES = 16
_STILL_CONNECT_TIMEOUT = 3.0
_STILL_READ_TIMEOUT = 7.0


def option_text(entry: ConfigEntry, key: str) -> str | None:
    """Return a trimmed non-empty string option."""
    value = entry.options.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def safe_state_attribute(value: Any, *, max_depth: int = 4) -> Any:
    """Return a bounded JSON-safe value for Home Assistant attributes."""
    if max_depth < 0:
        return repr(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {
            str(key): safe_state_attribute(item, max_depth=max_depth - 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [safe_state_attribute(item, max_depth=max_depth - 1) for item in value]
    return repr(value)


def decode_flv_jpeg(
    stream_url: str,
    width: int | None,
    height: int | None,
) -> bytes | None:
    """Decode the first FLV video frame to JPEG without optional TurboJPEG."""
    import av
    from PIL import Image

    with av.open(
        stream_url,
        timeout=(_STILL_CONNECT_TIMEOUT, _STILL_READ_TIMEOUT),
    ) as container:
        for frame in container.decode(video=0):
            image = frame.to_image().convert("RGB")
            if width or height:
                target_width = max(int(width or image.width), 1)
                target_height = max(int(height or image.height), 1)
                image.thumbnail(
                    (target_width, target_height),
                    Image.Resampling.LANCZOS,
                )
            encoded = io.BytesIO()
            image.save(encoded, format="JPEG", quality=90)
            return encoded.getvalue()
    return None


def split_runner_command(command: str) -> tuple[str, ...]:
    """Split a configured runner command into executable and arguments."""
    try:
        windows = platform.system().casefold() == "windows"
        parsed = shlex.split(command, posix=not windows)
        if windows:
            parsed = [
                part[1:-1]
                if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'"
                else part
                for part in parsed
            ]
        parts = tuple(parsed)
    except ValueError as err:
        raise DreameLawnMowerVideoRuntimeError(
            f"Invalid XP2P runner command: {err}"
        ) from err
    if not parts:
        raise DreameLawnMowerVideoRuntimeError("XP2P runner command cannot be empty.")
    return parts


def managed_runtime_supported() -> bool:
    """Return whether the self-managed runtime supports this HA host."""
    if platform.system().casefold() != "linux":
        return False
    machine = platform.machine().casefold()
    return machine in {"amd64", "x64", "x86_64", "aarch64", "arm64"}


def probe_stream_health(
    stream_url: str,
    *,
    on_stream_open: Callable[[], Any] | None = None,
) -> DreameLawnMowerStreamUrlProbeResult:
    """Check the local stream before Home Assistant advertises it."""
    return probe_stream_url(
        stream_url,
        timeout=_STREAM_HEALTH_TIMEOUT,
        read_bytes=_STREAM_HEALTH_BYTES,
        attempts=_STREAM_HEALTH_ATTEMPTS,
        retry_interval=_STREAM_HEALTH_RETRY_INTERVAL,
        on_stream_open=on_stream_open,
    )


def stream_health_error(health: DreameLawnMowerStreamUrlProbeResult) -> str:
    """Render a redacted reason for a local stream URL that did not serve FLV."""
    details = [f"error_category={health.error_category or 'unknown'}"]
    if health.status_code is not None:
        details.append(f"status_code={health.status_code}")
    if health.bytes_read:
        details.append(f"bytes_read={health.bytes_read}")
    if health.error:
        details.append(f"error={health.error}")
    return (
        "XP2P runtime returned a local stream URL, but it did not emit an FLV "
        f"header ({', '.join(details)})."
    )
