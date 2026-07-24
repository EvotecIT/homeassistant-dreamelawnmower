"""Decoding, health, and configuration helpers for the live video entity."""

from __future__ import annotations

import os
import platform
import shlex
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_VIDEO_TRANSPORT,
    DEFAULT_VIDEO_TRANSPORT,
    VIDEO_TRANSPORT_AUTO,
    VIDEO_TRANSPORT_CLOUD,
)
from .dreame_lawn_mower_client.stream_health import (
    DreameLawnMowerStreamUrlProbeResult,
    probe_stream_url,
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerVideoRuntimeError,
    DreameLawnMowerXp2pLiveStreamSession,
)

_STREAM_HEALTH_ATTEMPTS = 3
_STREAM_HEALTH_RETRY_INTERVAL = 0.5
_STREAM_HEALTH_TIMEOUT = 3.0
_STREAM_HEALTH_BYTES = 16
def option_text(entry: ConfigEntry, key: str) -> str | None:
    """Return a trimmed non-empty string option."""
    value = entry.options.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def video_transport(entry: ConfigEntry) -> str:
    """Return one validated camera transport option."""
    value = entry.options.get(CONF_VIDEO_TRANSPORT)
    if value in {
        VIDEO_TRANSPORT_AUTO,
        VIDEO_TRANSPORT_CLOUD,
    }:
        return str(value)
    # Same-LAN-only was never supported by the tested A2 production firmware.
    # Normalize prerelease/unknown values to the proven default.
    return DEFAULT_VIDEO_TRANSPORT


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
    return managed_runtime_environment()["supported"]


def managed_runtime_environment() -> dict[str, str | bool | int]:
    """Return privacy-safe host facts needed to diagnose managed XP2P startup."""
    system = platform.system().strip().casefold() or "unknown"
    machine = platform.machine().strip().casefold() or "unknown"
    if machine in {"amd64", "x64", "x86_64"}:
        normalized_machine = "x86_64"
    elif machine in {"aarch64", "arm64"}:
        normalized_machine = "aarch64"
    else:
        normalized_machine = machine
    supported = system == "linux" and normalized_machine in {"x86_64", "aarch64"}
    execution_mode = (
        "qemu_aarch64"
        if supported and normalized_machine == "x86_64"
        else "native_aarch64"
        if supported
        else "unsupported"
    )
    libc_name, libc_version = platform.libc_ver()
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        page_size = 0
    return {
        "system": system,
        "machine": normalized_machine,
        "execution_mode": execution_mode,
        "supported": supported,
        "kernel_release": platform.release().strip() or "unknown",
        "page_size": page_size,
        "libc": libc_name.strip().casefold() or "unknown",
        "libc_version": libc_version.strip() or "unknown",
    }


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


def probe_stream_health_and_route(
    runtime: Any,
    session: DreameLawnMowerXp2pLiveStreamSession,
) -> DreameLawnMowerStreamUrlProbeResult:
    """Probe FLV while querying Tencent's opaque network-type metadata."""
    refresh = getattr(runtime, "refresh_stream_link_mode", None)
    callback = (lambda: refresh(session)) if callable(refresh) else None
    return probe_stream_health(session.stream_url, on_stream_open=callback)


def format_video_start_failures(
    cloud_error: str,
    *,
    lan_error: str | None,
    cached_xp2p_error: str | None,
) -> str:
    """Preserve Auto-mode failures without leaking runtime inputs."""
    failures: list[str] = []
    if lan_error:
        failures.append(f"Same-LAN service failed: {lan_error}")
    if cached_xp2p_error:
        failures.append(f"Cached XP2P failed: {cached_xp2p_error}")
    failures.append(f"Cloud fallback failed: {cloud_error}")
    return " ".join(failures) if len(failures) > 1 else cloud_error
