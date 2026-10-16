"""Play an audio file through the mower speaker over the live video session."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerVideoRuntimeError,
    DreameLawnMowerXp2pLiveStreamSession,
)
from .dreame_lawn_mower_client.xp2p_talk import (
    play_flv_audio,
    talk_ffmpeg_arguments,
)

_CONVERT_TIMEOUT = 30.0
_MAX_TALK_FLV_BYTES = 2 * 1024 * 1024
# First playback may also install the XP2P runtime before the mower answers.
_SESSION_READY_TIMEOUT = 120.0
_SESSION_POLL_INTERVAL = 0.25
_CONSUMER_CHUNK_BYTES = 64 * 1024
# hass.data key of the core ffmpeg integration (DATA_FFMPEG); importing that
# integration needs ha-ffmpeg, which this integration does not require.
_FFMPEG_DATA_KEY = "ffmpeg"

ReadySession = Callable[[], tuple[Any, DreameLawnMowerXp2pLiveStreamSession | None]]


async def async_convert_to_talk_flv(hass: HomeAssistant, file_path: str) -> bytes:
    """Convert an allowed local audio file to the mower's talk format."""
    local_path = await hass.async_add_executor_job(_allowed_audio_path, hass, file_path)
    process = await asyncio.create_subprocess_exec(
        _ffmpeg_binary(hass),
        *talk_ffmpeg_arguments(local_path),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    communication = asyncio.create_task(process.communicate())
    try:
        output, errors = await asyncio.wait_for(
            asyncio.shield(communication), timeout=_CONVERT_TIMEOUT
        )
    except BaseException as err:
        # Keep the pipe readers alive while killing/reaping the child. Shield
        # cleanup from repeated service cancellation as well as the first one.
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        await _async_drain(communication)
        if isinstance(err, TimeoutError):
            raise HomeAssistantError("Audio conversion timed out.") from err
        raise
    if process.returncode != 0:
        detail = errors.decode("utf-8", "replace").strip()[-200:]
        raise HomeAssistantError(f"Audio conversion failed: {detail}")
    if len(output) > _MAX_TALK_FLV_BYTES:
        raise HomeAssistantError("The converted audio is too large.")
    return output


def _allowed_audio_path(hass: HomeAssistant, file_path: str) -> str:
    """Resolve and authorize one regular local file outside the event loop."""
    path = Path(file_path)
    if not path.is_absolute():
        raise HomeAssistantError("Audio needs an absolute local file path.")
    if not hass.config.is_allowed_path(file_path):
        raise HomeAssistantError(f"Access to {file_path} is not allowed.")
    if not path.is_file():
        raise HomeAssistantError("Audio needs a regular local file.")
    return str(path.resolve(strict=True))


async def _async_drain(operation: asyncio.Future[Any]) -> None:
    """Retain resource ownership until cleanup finishes, including repeat cancels."""
    while not operation.done():
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:
            continue
        except Exception:  # noqa: BLE001 - retrieve the worker failure below.
            break
    with contextlib.suppress(asyncio.CancelledError, Exception):
        operation.result()


def _ffmpeg_binary(hass: HomeAssistant) -> str:
    """Use the binary configured for Home Assistant's ffmpeg, else the default."""
    manager = hass.data.get(_FFMPEG_DATA_KEY)
    return str(getattr(manager, "binary", None) or "ffmpeg")


async def async_play_audio_with_live_video(
    hass: HomeAssistant,
    file_path: str,
    *,
    relay_url: Callable[[], Awaitable[str | None]],
    ready_session: ReadySession,
    stream_error: Callable[[], str | None],
    ready_timeout: float = _SESSION_READY_TIMEOUT,
) -> dict[str, Any]:
    """Hold the live video open as a relay consumer while the audio is sent."""
    flv = await async_convert_to_talk_flv(hass, file_path)
    url = await relay_url()
    if url is None:
        raise HomeAssistantError(stream_error() or "The live video is unavailable.")
    consumer = asyncio.ensure_future(_async_consume_relay(hass, url))
    try:
        runtime, session = await _async_wait_for_session(
            ready_session, consumer, stream_error, ready_timeout
        )
        return await async_play_audio_on_session(hass, runtime, session, flv)
    finally:
        consumer.cancel()
        # Its failures were already reported while waiting for the session.
        await _async_drain(consumer)


async def _async_consume_relay(hass: HomeAssistant, url: str) -> None:
    """Read and discard the relay stream; this keeps the video session open."""
    # The readiness owner bounds cold startup at 120 seconds. A shorter HTTP
    # body-read deadline would abort runtime preparation halfway through it.
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
    async with async_get_clientsession(hass).get(url, timeout=timeout) as response:
        response.raise_for_status()
        async for _chunk in response.content.iter_chunked(_CONSUMER_CHUNK_BYTES):
            pass


async def _async_wait_for_session(
    ready_session: ReadySession,
    consumer: asyncio.Future[None],
    stream_error: Callable[[], str | None],
    timeout: float,
) -> tuple[Any, DreameLawnMowerXp2pLiveStreamSession]:
    deadline = time.monotonic() + timeout
    while True:
        runtime, session = ready_session()
        if session is not None:
            return runtime, session
        if consumer.done():
            error = consumer.exception()
            raise HomeAssistantError(
                stream_error() or f"The live video stopped before it was ready: {error}"
            )
        if time.monotonic() >= deadline:
            raise HomeAssistantError(
                stream_error() or "The live video did not start in time."
            )
        await asyncio.sleep(_SESSION_POLL_INTERVAL)


async def async_play_audio_on_session(
    hass: HomeAssistant,
    runtime: Any,
    session: DreameLawnMowerXp2pLiveStreamSession,
    flv: bytes,
) -> dict[str, Any]:
    """Send converted audio on a verified live session and report what was sent."""
    if not callable(getattr(runtime, "start_talk", None)):
        raise HomeAssistantError("This video runtime cannot send audio.")
    if session.transport != "cloud":
        raise HomeAssistantError("Talking needs the cloud video transport.")
    cancelled = threading.Event()
    operation = hass.async_add_executor_job(
        partial(play_flv_audio, runtime, session, flv, cancelled=cancelled)
    )
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        cancelled.set()
        await _async_drain(operation)
        raise
    except DreameLawnMowerVideoRuntimeError as err:
        raise HomeAssistantError(str(err)) from err
