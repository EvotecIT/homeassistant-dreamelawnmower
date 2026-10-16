"""Conversion and playback ownership across cancellation and cold startup."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower import video_talk
from custom_components.dreame_lawn_mower.video_camera import DreameLawnMowerVideoCamera

from .test_video_camera import _uninitialized_entity
from .test_xp2p_talk import _flv, _Runtime, _session, _tag


def _hass(allowed=lambda _path: True):
    return SimpleNamespace(
        data={},
        config=SimpleNamespace(is_allowed_path=allowed),
        async_add_executor_job=lambda call, *args: (
            asyncio.get_running_loop().run_in_executor(None, call, *args)
        ),
    )


@pytest.mark.parametrize("timeout", [False, True])
def test_conversion_reaps_child_before_cancellation_or_timeout_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timeout: bool,
) -> None:
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"synthetic audio")

    async def run():
        entered, killed, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
        release = asyncio.Event()

        class Process:
            returncode = None

            async def communicate(self):
                entered.set()
                await release.wait()
                self.returncode = -9
                drained.set()
                return b"", b""

            def kill(self):
                killed.set()

            async def wait(self):
                return -9

        async def spawn(*_args, **_kwargs):
            return Process()

        monkeypatch.setattr(video_talk.asyncio, "create_subprocess_exec", spawn)
        if timeout:
            monkeypatch.setattr(video_talk, "_CONVERT_TIMEOUT", 0.01)
        task = asyncio.create_task(
            video_talk.async_convert_to_talk_flv(_hass(), str(path))
        )
        try:
            await asyncio.wait_for(entered.wait(), 1)
            if not timeout:
                task.cancel()
            await asyncio.wait_for(killed.wait(), 1)
            assert not task.done(), "conversion must drain the killed child's pipes"
            task.cancel()  # Repeated service cancellation must not interrupt cleanup.
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        assert drained.is_set()

    asyncio.run(run())


def test_path_authorization_runs_outside_the_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"audio")
    loop_thread = threading.get_ident()

    def allowed(_path):
        assert threading.get_ident() != loop_thread
        return False

    async def run():
        with pytest.raises(video_talk.HomeAssistantError, match="not allowed"):
            await video_talk.async_convert_to_talk_flv(_hass(allowed), str(path))

    asyncio.run(run())


@pytest.mark.parametrize(
    "path", ["https://example.invalid/audio.mp3", "file:///media/a.mp3", "pipe:0"]
)
def test_conversion_rejects_protocol_inputs_before_spawning(path, monkeypatch):
    async def spawn(*_args, **_kwargs):
        pytest.fail("a protocol-shaped input must not reach FFmpeg")

    monkeypatch.setattr(video_talk.asyncio, "create_subprocess_exec", spawn)

    async def run():
        with pytest.raises(video_talk.HomeAssistantError, match="local"):
            await video_talk.async_convert_to_talk_flv(_hass(), path)

    asyncio.run(run())


def test_cancelled_playback_keeps_lock_and_relay_until_executor_closes_channel(
    monkeypatch,
):
    entered, release = threading.Event(), threading.Event()
    events = []

    class Runtime(_Runtime):
        def send_talk_audio(self, session, chunk):
            entered.set()
            release.wait(3)
            events.append("send completed")

        def stop_talk(self, session):
            events.append("channel closed")

    async def convert(*_args):
        return _flv(_tag(8, 0, b"sequence"), _tag(8, 10000, b"later frame"))

    async def consume(*_args, **_kwargs):
        try:
            await asyncio.Future()
        finally:
            events.append("relay released")

    monkeypatch.setattr(video_talk, "async_convert_to_talk_flv", convert)
    monkeypatch.setattr(video_talk, "_async_consume_relay", consume)

    async def run():
        async def source():
            return "http://127.0.0.1/relay.flv"

        entity = SimpleNamespace(
            hass=_hass(),
            _talk_lock=asyncio.Lock(),
            _unverified_playback_session=None,
            _runtime=Runtime(),
            _session=_session(),
            stream_source=source,
        )
        task = asyncio.create_task(
            DreameLawnMowerVideoCamera.async_play_audio(entity, "/media/a.mp3")
        )
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            assert entity._talk_lock.locked()
            assert "relay released" not in events
            task.cancel()
            await asyncio.sleep(0)
            assert entity._talk_lock.locked()
        finally:
            release.set()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        assert events == ["send completed", "channel closed", "relay released"]
        assert not entity._talk_lock.locked()

    asyncio.run(run())


def test_relay_read_budget_covers_cold_session_readiness(monkeypatch):
    observed = []

    class Content:
        async def iter_chunked(self, _size):
            if False:
                yield b""

    class Response:
        content = Content()

        def raise_for_status(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

    def get(_url, *, timeout):
        observed.append(timeout)
        return Response()

    monkeypatch.setattr(
        video_talk, "async_get_clientsession", lambda _hass: SimpleNamespace(get=get)
    )
    asyncio.run(video_talk._async_consume_relay(_hass(), "http://127.0.0.1/relay"))
    assert (
        observed[0].sock_read is None
        or observed[0].sock_read >= video_talk._SESSION_READY_TIMEOUT
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is unavailable")
def test_allowed_playlist_cannot_read_a_denied_audio_file(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    denied = tmp_path / "denied.aac"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.1",
            "-c:a",
            "aac",
            str(denied),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    # A misleading extension must not bypass the content-based format check.
    playlist = allowed / "sound.mp3"
    playlist.write_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:0\n"
        f"#EXTINF:0.1,\n{denied}\n#EXT-X-ENDLIST\n"
    )
    normal = allowed / "sound.aac"
    shutil.copyfile(denied, normal)

    async def run():
        hass = _hass(lambda path: Path(path).is_relative_to(allowed))
        with pytest.raises(video_talk.HomeAssistantError, match="not allowed"):
            await video_talk.async_convert_to_talk_flv(hass, str(denied))
        with pytest.raises(video_talk.HomeAssistantError, match="conversion failed"):
            await video_talk.async_convert_to_talk_flv(hass, str(playlist))
        assert (
            await video_talk.async_convert_to_talk_flv(hass, str(normal))
        ).startswith(b"FLV")

    asyncio.run(run())


@pytest.mark.parametrize("remove", [False, True])
def test_camera_shutdown_drains_audio_before_stopping_video(remove, monkeypatch):
    events = []

    async def run():
        entity = _uninitialized_entity()
        entity._session = _session()
        started = asyncio.Event()

        async def playback():
            try:
                started.set()
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                events.append("audio closed")

        async def stop(*_args, **_kwargs):
            events.append("video stopped")
            entity._session = None

        async def base_remove(_self):
            events.append("entity removed")

        entity._async_stop_active_session = stop
        entity.async_write_ha_state = lambda: None
        entity._talk_task = asyncio.create_task(playback())
        monkeypatch.setattr(
            DreameLawnMowerVideoCamera.__mro__[1],
            "async_will_remove_from_hass",
            base_remove,
        )
        await started.wait()
        if remove:
            await entity.async_will_remove_from_hass()
        else:
            await entity.async_turn_off()
        assert entity._talk_task.done()

    asyncio.run(run())
    assert events[:2] == ["audio closed", "video stopped"]
