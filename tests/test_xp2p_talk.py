"""Contract tests for two-way audio over the live XP2P session."""

from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest
import yaml

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    xp2p_host_runtime,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerVideoRuntimeError,
    DreameLawnMowerXp2pLiveStreamSession,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.xp2p_talk import (
    VOICE_STATUS_COMMAND,
    play_flv_audio,
    split_flv_audio,
    talk_ffmpeg_arguments,
    voice_channel_status,
)


def _tag(tag_type: int, timestamp: int, body: bytes) -> bytes:
    header = (
        bytes([tag_type])
        + len(body).to_bytes(3, "big")
        + (timestamp & 0xFFFFFF).to_bytes(3, "big")
        + bytes([timestamp >> 24])
        + b"\0\0\0"
    )
    return header + body + struct.pack("!I", len(header) + len(body))


def _flv(*tags: bytes, flags: int = 0x05) -> bytes:
    return b"FLV\x01" + bytes([flags]) + struct.pack("!I", 9) + b"\0\0\0\0" + b"".join(
        tags
    )


class _Runtime:
    def __init__(self, reply: bytes = b'[{"status":0}]') -> None:
        self.reply = reply
        self.calls: list[tuple[str, object]] = []

    def post_device_command(self, _session, command: str) -> bytes:
        self.calls.append(("command", command))
        return self.reply

    def start_talk(self, _session) -> None:
        self.calls.append(("start", None))

    def send_talk_audio(self, _session, chunk: bytes) -> None:
        self.calls.append(("send", chunk))

    def stop_talk(self, _session) -> None:
        self.calls.append(("stop", None))


def _session(process: object | None = None) -> DreameLawnMowerXp2pLiveStreamSession:
    return DreameLawnMowerXp2pLiveStreamSession(
        service_id="product/device",
        stream_url="http://127.0.0.1/stream.flv",
        runner_process=process,
    )


def test_voice_status_command_matches_live_status_form() -> None:
    assert VOICE_STATUS_COMMAND == (
        "action=inner_define&channel=0&cmd=get_device_st&type=voice&quality=standard"
    )


@pytest.mark.parametrize(
    ("reply", "status"),
    [
        (b'[{"status":0}]', 0),
        (b'[{"status":"1"}]\0', 1),
        (b'{"status":-1}', -1),
        (b"[]", None),
        (b"not json", None),
    ],
)
def test_voice_channel_status_reads_device_reply(reply: bytes, status) -> None:
    assert voice_channel_status(reply) == status


def test_split_flv_audio_drops_metadata_and_prefixes_audio_header() -> None:
    sequence = _tag(8, 0, b"\xaf\x00\x14\x08")
    frame = _tag(8, 64, b"\xaf\x01abc")
    chunks = split_flv_audio(_flv(_tag(18, 0, b"meta"), sequence, frame))

    assert [chunk.timestamp_ms for chunk in chunks] == [0, 64]
    assert chunks[0].data == b"FLV\x01\x04\x00\x00\x00\x09\0\0\0\0" + sequence
    assert chunks[1].data == frame


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"RIFF0000000000", "not an FLV"),
        (_flv(_tag(9, 0, b"v")), "contains video"),
        (_flv(_tag(18, 0, b"meta")), "no audio"),
        (_flv(_tag(8, 0, b"abcd"))[:-3], "truncated"),
    ],
)
def test_split_flv_audio_rejects_unusable_streams(data: bytes, message: str) -> None:
    with pytest.raises(DreameLawnMowerVideoRuntimeError, match=message):
        split_flv_audio(data)


def test_play_flv_audio_checks_voice_then_paces_and_closes() -> None:
    runtime = _Runtime()
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(round(seconds, 3))
        now[0] += seconds

    result = play_flv_audio(
        runtime,
        _session(),
        _flv(_tag(8, 0, b"seq"), _tag(8, 64, b"a"), _tag(8, 128, b"b")),
        sleep=sleep,
        clock=lambda: now[0],
    )

    assert [call[0] for call in runtime.calls] == [
        "command", "start", "send", "send", "send", "stop",
    ]
    assert runtime.calls[0][1] == VOICE_STATUS_COMMAND
    assert sleeps == [0.064, 0.064, 0.5]
    assert result == {"voice_status": 0, "chunks": 3, "duration_ms": 128}


def test_play_flv_audio_does_not_open_channel_when_voice_is_not_ready() -> None:
    runtime = _Runtime(b'[{"status":1}]')

    with pytest.raises(DreameLawnMowerVideoRuntimeError, match="status 1"):
        play_flv_audio(runtime, _session(), _flv(_tag(8, 0, b"seq")))

    assert [call[0] for call in runtime.calls] == ["command"]


def test_play_flv_audio_keeps_send_error_when_close_also_fails() -> None:
    class _Failing(_Runtime):
        def send_talk_audio(self, _session, chunk: bytes) -> None:
            raise DreameLawnMowerVideoRuntimeError("XP2P audio send failed.")

        def stop_talk(self, _session) -> None:
            raise DreameLawnMowerVideoRuntimeError("worker is not running")

    with pytest.raises(DreameLawnMowerVideoRuntimeError, match="send failed"):
        play_flv_audio(
            _Failing(), _session(), _flv(_tag(8, 0, b"seq")), sleep=lambda _s: None
        )


def test_talk_ffmpeg_arguments_produce_app_audio_format() -> None:
    arguments = talk_ffmpeg_arguments("/media/hello.mp3")
    joined = " ".join(arguments)

    assert arguments[arguments.index("-i") + 1] == "/media/hello.mp3"
    assert "-ac 1 -ar 16000 -c:a aac -b:a 96k" in joined
    assert "-flvflags no_metadata -f flv -" in joined
    assert "-t 60" in joined


class _Process:
    def __init__(self, responses: bytes) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(responses)

    @staticmethod
    def poll():
        return None


def _response(status: int, payload: bytes = b"") -> bytes:
    return b"DXR1" + struct.pack("!II", status, len(payload)) + payload


def _runtime(tmp_path: Path) -> xp2p_host_runtime.DreameLawnMowerXp2pHostRuntime:
    return xp2p_host_runtime.DreameLawnMowerXp2pHostRuntime(
        xp2p_host_runtime.DreameLawnMowerXp2pHostAssets(
            worker_path=tmp_path / "worker",
            linker_path=tmp_path / "linker",
            library_path=tmp_path / "library",
            library_search_paths=(tmp_path,),
        )
    )


def test_runtime_talk_controls_use_length_prefixed_worker_frames(
    tmp_path: Path,
) -> None:
    process = _Process(
        _response(0, b'[{"status":0}]')
        + _response(0, b"talk")
        + _response(0)
        + _response(0)
    )
    session = _session(process)
    runtime = _runtime(tmp_path)

    assert runtime.post_device_command(session, "cmd=x") == b'[{"status":0}]'
    runtime.start_talk(session)
    runtime.send_talk_audio(session, b"\x01\x02")
    runtime.stop_talk(session)

    assert process.stdin.getvalue() == (
        b"C\0\0\0\x05cmd=x"
        + b"T\0\0\0\x09channel=0"
        + b"D\0\0\0\x02\x01\x02"
        + b"S"
    )


def test_runtime_talk_control_reports_worker_refusal(tmp_path: Path) -> None:
    process = _Process(_response(18, b"The audio channel is not open."))

    with pytest.raises(DreameLawnMowerVideoRuntimeError, match="not open"):
        _runtime(tmp_path).send_talk_audio(_session(process), b"x")


def test_runtime_talk_control_requires_running_worker(tmp_path: Path) -> None:
    with pytest.raises(DreameLawnMowerVideoRuntimeError, match="not running"):
        _runtime(tmp_path).stop_talk(_session(None))


def test_play_audio_service_is_declared_for_live_video_cameras() -> None:
    services = yaml.safe_load(
        (
            Path(__file__).parents[1]
            / "custom_components/dreame_lawn_mower/services.yaml"
        ).read_text()
    )

    service = services["play_audio"]
    assert service["target"]["entity"]["domain"] == "camera"
    assert service["fields"]["file_path"]["required"] is True


def _hass(allowed: bool = True, ffmpeg: object | None = None):
    from types import SimpleNamespace

    data = {} if ffmpeg is None else {"ffmpeg": ffmpeg}
    return SimpleNamespace(
        data=data,
        config=SimpleNamespace(is_allowed_path=lambda _path: allowed),
    )


def test_play_audio_requires_an_active_live_video_session() -> None:
    import asyncio

    from homeassistant.exceptions import HomeAssistantError

    from custom_components.dreame_lawn_mower.video_talk import (
        async_play_audio_on_session,
    )

    with pytest.raises(HomeAssistantError, match="Start the live video first"):
        asyncio.run(
            async_play_audio_on_session(_hass(), _Runtime(), None, "/media/a.mp3")
        )


def test_play_audio_refuses_the_lan_video_transport() -> None:
    import asyncio

    from homeassistant.exceptions import HomeAssistantError

    from custom_components.dreame_lawn_mower.video_talk import (
        async_play_audio_on_session,
    )

    session = _session()
    session.transport = "lan"
    with pytest.raises(HomeAssistantError, match="cloud video transport"):
        asyncio.run(
            async_play_audio_on_session(_hass(), _Runtime(), session, "/media/a.mp3")
        )


def test_play_audio_refuses_paths_outside_the_allowlist() -> None:
    import asyncio

    from homeassistant.exceptions import HomeAssistantError

    from custom_components.dreame_lawn_mower.video_talk import (
        async_convert_to_talk_flv,
    )

    with pytest.raises(HomeAssistantError, match="not allowed"):
        asyncio.run(
            async_convert_to_talk_flv(_hass(allowed=False), "/private/sound.mp3")
        )


def test_play_audio_uses_home_assistant_ffmpeg_binary_when_configured() -> None:
    from types import SimpleNamespace

    from custom_components.dreame_lawn_mower.video_talk import _ffmpeg_binary

    assert _ffmpeg_binary(_hass()) == "ffmpeg"
    assert (
        _ffmpeg_binary(_hass(ffmpeg=SimpleNamespace(binary="/usr/bin/ffmpeg")))
        == "/usr/bin/ffmpeg"
    )
