"""Cached XP2P startup lifecycle shared by the Home Assistant camera."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerXp2pLiveStreamSession,
)


@dataclass(slots=True, frozen=True)
class DreameLawnMowerCachedXp2pStartResult:
    """One cached startup result reserved for Home Assistant's sole consumer."""

    session: DreameLawnMowerXp2pLiveStreamSession | None = None
    error: str | None = None


async def async_start_cached_xp2p(
    runtime: object,
    inputs: DreameLawnMowerCameraStreamRuntimeInputs,
    *,
    start_session: Callable[..., Awaitable[DreameLawnMowerXp2pLiveStreamSession]],
) -> DreameLawnMowerCachedXp2pStartResult:
    """Start cached XP2P without consuming its single-reader FLV endpoint.

    Home Assistant must be the first and only reader of the returned endpoint.
    Playback verification therefore happens after the session is adopted by the
    camera stream instead of through a throwaway preflight connection.
    """
    try:
        session = await start_session(
            runtime,
            inputs,
            camera_toggle_managed=False,
        )
    except Exception as err:  # noqa: BLE001 - Auto falls back to cloud.
        return DreameLawnMowerCachedXp2pStartResult(error=str(err))
    return DreameLawnMowerCachedXp2pStartResult(session=session)
