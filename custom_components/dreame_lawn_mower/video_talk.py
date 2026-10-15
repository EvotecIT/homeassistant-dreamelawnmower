"""Play an audio file through the mower speaker over the live video session."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerVideoRuntimeError,
    DreameLawnMowerXp2pLiveStreamSession,
)
from .dreame_lawn_mower_client.xp2p_talk import (
    play_flv_audio,
    talk_ffmpeg_arguments,
)

_CONVERT_TIMEOUT = 30.0
# hass.data key of the core ffmpeg integration (DATA_FFMPEG); importing that
# integration needs ha-ffmpeg, which this integration does not require.
_FFMPEG_DATA_KEY = "ffmpeg"
_MAX_TALK_FLV_BYTES = 2 * 1024 * 1024


async def async_convert_to_talk_flv(hass: HomeAssistant, file_path: str) -> bytes:
    """Convert an allowed local audio file to the mower's talk format."""
    if not hass.config.is_allowed_path(file_path):
        raise HomeAssistantError(f"Access to {file_path} is not allowed.")
    process = await asyncio.create_subprocess_exec(
        _ffmpeg_binary(hass),
        *talk_ffmpeg_arguments(file_path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        output, errors = await asyncio.wait_for(
            process.communicate(), timeout=_CONVERT_TIMEOUT
        )
    except TimeoutError as err:
        process.kill()
        await process.wait()
        raise HomeAssistantError("Audio conversion timed out.") from err
    if process.returncode != 0:
        detail = errors.decode("utf-8", "replace").strip()[-200:]
        raise HomeAssistantError(f"Audio conversion failed: {detail}")
    if len(output) > _MAX_TALK_FLV_BYTES:
        raise HomeAssistantError("The converted audio is too large.")
    return output


def _ffmpeg_binary(hass: HomeAssistant) -> str:
    """Use the binary configured for Home Assistant's ffmpeg, else the default."""
    manager = hass.data.get(_FFMPEG_DATA_KEY)
    return str(getattr(manager, "binary", None) or "ffmpeg")


async def async_play_audio_on_session(
    hass: HomeAssistant,
    runtime: Any,
    session: DreameLawnMowerXp2pLiveStreamSession | None,
    file_path: str,
) -> dict[str, Any]:
    """Send one audio file on the active live session and report what was sent."""
    if session is None or not callable(getattr(runtime, "start_talk", None)):
        raise HomeAssistantError(
            "Start the live video first: talking uses its connection."
        )
    if session.transport != "cloud":
        raise HomeAssistantError("Talking needs the cloud video transport.")
    flv = await async_convert_to_talk_flv(hass, file_path)
    try:
        return await hass.async_add_executor_job(
            play_flv_audio, runtime, session, flv
        )
    except DreameLawnMowerVideoRuntimeError as err:
        raise HomeAssistantError(str(err)) from err
