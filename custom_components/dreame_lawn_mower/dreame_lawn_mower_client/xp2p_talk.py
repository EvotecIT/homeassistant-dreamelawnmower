"""Two-way audio ("talk") over a live XP2P video session.

The MOVA app asks the mower whether its voice channel is ready, opens the
encrypted send channel on the same P2P session as the video, then streams an
audio-only FLV (AAC-LC, 16 kHz mono) in real time and closes the channel.
"""

from __future__ import annotations

import json
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .video_runtime import (
    DreameLawnMowerVideoRuntimeError,
    DreameLawnMowerXp2pLiveStreamSession,
)

VOICE_STATUS_COMMAND = (
    "action=inner_define&channel=0&cmd=get_device_st&type=voice&quality=standard"
)
TALK_SAMPLE_RATE = 16000
MAX_TALK_DURATION_SECONDS = 60
# Let the mower play the last frame before the send channel closes.
_TALK_TAIL_SECONDS = 0.5
_FLV_SIGNATURE = b"FLV"
_FLV_HEADER_LENGTH = 9
_FLV_TAG_HEADER_LENGTH = 11
_FLV_PREVIOUS_TAG_SIZE_LENGTH = 4
_FLV_TAG_AUDIO = 8
_FLV_TAG_VIDEO = 9
_FLV_TAG_SCRIPT = 18
_FLV_FLAG_AUDIO = 0x04


class TalkRuntime(Protocol):
    """Live-session controls used to send audio."""

    def post_device_command(
        self, session: DreameLawnMowerXp2pLiveStreamSession, command: str
    ) -> bytes: ...

    def start_talk(self, session: DreameLawnMowerXp2pLiveStreamSession) -> None: ...

    def send_talk_audio(
        self, session: DreameLawnMowerXp2pLiveStreamSession, chunk: bytes
    ) -> None: ...

    def stop_talk(self, session: DreameLawnMowerXp2pLiveStreamSession) -> None: ...


@dataclass(frozen=True, slots=True)
class FlvAudioChunk:
    """One audio tag ready to send, with its presentation time."""

    timestamp_ms: int
    data: bytes


def voice_channel_status(reply: bytes) -> int | None:
    """Return the device's voice readiness status (0 = ready), if present."""
    try:
        value: Any = json.loads(reply.decode("utf-8", "replace").strip("\0 \r\n"))
    except ValueError:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def split_flv_audio(data: bytes) -> list[FlvAudioChunk]:
    """Split an audio-only FLV into tags; the first one carries the FLV header."""
    if len(data) < _FLV_HEADER_LENGTH + _FLV_PREVIOUS_TAG_SIZE_LENGTH or (
        data[:3] != _FLV_SIGNATURE
    ):
        raise DreameLawnMowerVideoRuntimeError("The audio is not an FLV stream.")
    header_length = struct.unpack("!I", data[5:9])[0]
    if header_length < _FLV_HEADER_LENGTH or header_length > len(data):
        raise DreameLawnMowerVideoRuntimeError("The FLV header is invalid.")
    header = (
        data[:4]
        + bytes([_FLV_FLAG_AUDIO])
        + struct.pack("!I", _FLV_HEADER_LENGTH)
        + bytes(_FLV_PREVIOUS_TAG_SIZE_LENGTH)
    )
    chunks: list[FlvAudioChunk] = []
    cursor = header_length + _FLV_PREVIOUS_TAG_SIZE_LENGTH
    while cursor + _FLV_TAG_HEADER_LENGTH <= len(data):
        tag_type = data[cursor] & 0x1F
        size = int.from_bytes(data[cursor + 1 : cursor + 4], "big")
        timestamp = int.from_bytes(data[cursor + 4 : cursor + 7], "big") | (
            data[cursor + 7] << 24
        )
        end = cursor + _FLV_TAG_HEADER_LENGTH + size + _FLV_PREVIOUS_TAG_SIZE_LENGTH
        if end > len(data):
            raise DreameLawnMowerVideoRuntimeError("The FLV stream is truncated.")
        if tag_type == _FLV_TAG_VIDEO:
            raise DreameLawnMowerVideoRuntimeError("The FLV stream contains video.")
        if tag_type == _FLV_TAG_AUDIO:
            tag = data[cursor:end]
            chunks.append(
                FlvAudioChunk(timestamp, header + tag if not chunks else tag)
            )
        elif tag_type != _FLV_TAG_SCRIPT:
            raise DreameLawnMowerVideoRuntimeError(
                f"The FLV stream has an unknown tag type {tag_type}."
            )
        cursor = end
    if not chunks:
        raise DreameLawnMowerVideoRuntimeError("The FLV stream has no audio.")
    return chunks


def talk_ffmpeg_arguments(input_path: str) -> list[str]:
    """Return ffmpeg arguments producing the app's talk format on stdout."""
    return [
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        input_path,
        "-t",
        str(MAX_TALK_DURATION_SECONDS),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(TALK_SAMPLE_RATE),
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-flvflags",
        "no_metadata",
        "-f",
        "flv",
        "-",
    ]


def play_flv_audio(
    runtime: TalkRuntime,
    session: DreameLawnMowerXp2pLiveStreamSession,
    flv: bytes,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Check voice readiness, then stream the audio in real time and close."""
    chunks = split_flv_audio(flv)
    status = voice_channel_status(
        runtime.post_device_command(session, VOICE_STATUS_COMMAND)
    )
    if status != 0:
        raise DreameLawnMowerVideoRuntimeError(
            f"The mower's voice channel is not ready (status {status})."
        )
    runtime.start_talk(session)
    try:
        started = clock()
        for chunk in chunks:
            delay = started + chunk.timestamp_ms / 1000 - clock()
            if delay > 0:
                sleep(delay)
            runtime.send_talk_audio(session, chunk.data)
        sleep(_TALK_TAIL_SECONDS)
    except BaseException:
        # Keep the send failure; closing a dead channel would only hide it.
        try:
            runtime.stop_talk(session)
        except DreameLawnMowerVideoRuntimeError:
            pass
        raise
    runtime.stop_talk(session)
    return {
        "voice_status": status,
        "chunks": len(chunks),
        "duration_ms": chunks[-1].timestamp_ms,
    }
