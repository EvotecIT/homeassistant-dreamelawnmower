"""Cached XP2P startup lifecycle shared by the Home Assistant camera."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from .dreame_lawn_mower_client.stream_health import (
    DreameLawnMowerStreamUrlProbeResult,
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerXp2pLiveStreamSession,
)
from .video_stream_helpers import (
    probe_stream_health_and_route,
    stream_health_error,
)


@dataclass(slots=True, frozen=True)
class DreameLawnMowerCachedXp2pStartResult:
    """One health-checked cached startup result."""

    session: DreameLawnMowerXp2pLiveStreamSession | None = None
    health: DreameLawnMowerStreamUrlProbeResult | None = None
    error: str | None = None


async def async_start_cached_xp2p(
    runtime: Any,
    inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    *,
    start_session: Callable[..., Awaitable[DreameLawnMowerXp2pLiveStreamSession]],
    stop_session: Callable[..., Awaitable[None]],
    executor: Callable[..., Awaitable[Any]],
) -> DreameLawnMowerCachedXp2pStartResult:
    """Start cached XP2P, health-check it, and own failed-session cleanup."""
    session: DreameLawnMowerXp2pLiveStreamSession | None = None
    try:
        session = await start_session(
            runtime,
            inputs,
            camera_toggle_managed=False,
        )
        health = await executor(probe_stream_health_and_route, runtime, session)
    except asyncio.CancelledError:
        if session is not None:
            await stop_session(runtime, session)
        raise
    except Exception as err:  # noqa: BLE001 - Auto falls back to cloud.
        if session is not None:
            await stop_session(runtime, session)
        return DreameLawnMowerCachedXp2pStartResult(error=str(err))
    if not health.flv_header_present:
        await stop_session(runtime, session)
        return DreameLawnMowerCachedXp2pStartResult(
            error=stream_health_error(health)
        )
    return DreameLawnMowerCachedXp2pStartResult(session=session, health=health)
