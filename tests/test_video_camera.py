"""Contract tests for the Home Assistant Dreame live-video entity."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import suppress
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

try:
    import turbojpeg  # noqa: F401
except ModuleNotFoundError:
    turbojpeg_stub = ModuleType("turbojpeg")

    class _UnavailableTurboJPEG:
        def __init__(self) -> None:
            raise RuntimeError("TurboJPEG is unavailable in the lightweight test job")

    turbojpeg_stub.TurboJPEG = _UnavailableTurboJPEG
    sys.modules["turbojpeg"] = turbojpeg_stub

from homeassistant.components.camera import CameraEntityFeature

import custom_components.dreame_lawn_mower.video_camera as video_camera_module
from custom_components.dreame_lawn_mower.const import (
    CONF_XP2P_LIBRARY_PATH,
    CONF_XP2P_RUNNER_COMMAND,
)
from custom_components.dreame_lawn_mower.video_camera import (
    DreameLawnMowerVideoCamera,
    _split_runner_command,
)

from .fixture_data import load_json_fixture


def _uninitialized_entity(*, snapshot: object | None = None):
    entity = object.__new__(DreameLawnMowerVideoCamera)
    entity._entry = SimpleNamespace(
        options={CONF_XP2P_RUNNER_COMMAND: "xp2p-runner"}
    )
    entity.coordinator = SimpleNamespace(data=snapshot)
    entity._session = None
    entity._runtime = None
    entity._attr_is_on = True
    entity._stream_lock = asyncio.Lock()
    entity._snapshot_lock = asyncio.Lock()
    return entity


def test_video_camera_advertises_stop_control() -> None:
    features = _uninitialized_entity().supported_features

    assert features & CameraEntityFeature.STREAM
    assert features & CameraEntityFeature.ON_OFF
    assert "async_turn_on" in DreameLawnMowerVideoCamera.__dict__
    assert "async_turn_off" in DreameLawnMowerVideoCamera.__dict__


def test_video_manifest_declares_home_assistant_stream_dependency() -> None:
    manifest = json.loads(
        Path("custom_components/dreame_lawn_mower/manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert "stream" in manifest["dependencies"]


def test_video_camera_available_from_a2_video_metadata() -> None:
    payload = load_json_fixture("a2_paused_diagnostics.json")
    snapshot = SimpleNamespace(**payload["data"]["snapshot"])
    entity = _uninitialized_entity(snapshot=snapshot)

    assert "video" not in snapshot.capabilities
    assert entity.available is True


def test_video_camera_unavailable_without_video_metadata() -> None:
    snapshot = SimpleNamespace(
        capabilities=("map", "lidar_navigation"),
        raw_info={"deviceInfo": {"feature": "map", "permit": "pincode"}},
    )
    entity = _uninitialized_entity(snapshot=snapshot)

    assert entity.available is False


def test_video_camera_available_from_live_key_definition() -> None:
    snapshot = SimpleNamespace(
        capabilities=(),
        raw_info={"deviceInfo": {"liveKeyDefine": {"monitor": "available"}}},
    )
    entity = _uninitialized_entity(snapshot=snapshot)

    assert entity.available is True


def test_video_camera_available_from_top_level_video_status() -> None:
    snapshot = SimpleNamespace(
        capabilities=(),
        raw_info={"deviceInfo": {}, "videoStatus": 0},
    )
    entity = _uninitialized_entity(snapshot=snapshot)

    assert entity.available is True


def test_runner_command_uses_windows_backslash_and_quote_semantics() -> None:
    with patch.object(video_camera_module.platform, "system", return_value="Windows"):
        command = _split_runner_command(
            '"C:\\Program Files\\Dreame\\xp2p-runner.exe" --mode process'
        )

    assert command == (
        "C:\\Program Files\\Dreame\\xp2p-runner.exe",
        "--mode",
        "process",
    )


def test_native_library_runtime_receives_device_config_fetcher() -> None:
    entity = _uninitialized_entity()
    entity._entry = SimpleNamespace(
        options={CONF_XP2P_LIBRARY_PATH: "/tmp/fake-xp2p.so"}
    )
    entity._prepared_runtime = None
    entity._last_native_runtime_diagnostics = None
    diagnostics = SimpleNamespace(ready=True, error=None, as_dict=lambda: {})
    runtime = object()

    with (
        patch.object(
            video_camera_module,
            "diagnose_native_xp2p_runtime",
            return_value=diagnostics,
        ),
        patch.object(
            video_camera_module,
            "DreameLawnMowerNativeXp2pRuntime",
            return_value=runtime,
        ) as runtime_type,
    ):
        result = entity._create_runtime()

    assert result is runtime
    assert (
        runtime_type.call_args.kwargs["config_fetcher"]
        is video_camera_module.resolve_xp2p_device_config
    )


def test_video_camera_serializes_concurrent_stream_starts() -> None:
    async def _run() -> tuple[list[str | None], int, int]:
        entity = _uninitialized_entity()
        entity._stream_lock = asyncio.Lock()
        active = 0
        maximum_active = 0
        start_count = 0

        async def _start() -> str:
            nonlocal active, maximum_active, start_count
            start_count += 1
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0)
            active -= 1
            entity._session = SimpleNamespace(
                stream_url="http://127.0.0.1/live.flv"
            )
            return entity._session.stream_url

        entity._async_start_stream = _start
        results = await asyncio.gather(
            entity.stream_source(),
            entity.stream_source(),
        )
        return results, maximum_active, start_count

    results, maximum_active, start_count = asyncio.run(_run())

    assert results == [
        "http://127.0.0.1/live.flv",
        "http://127.0.0.1/live.flv",
    ]
    assert maximum_active == 1
    assert start_count == 1


def test_video_camera_does_not_start_while_turned_off() -> None:
    async def _run() -> tuple[str | None, int]:
        entity = _uninitialized_entity()
        entity._stream_lock = asyncio.Lock()
        entity._attr_is_on = False
        starts = 0

        async def _start() -> str:
            nonlocal starts
            starts += 1
            return "http://127.0.0.1/live.flv"

        entity._async_start_stream = _start
        entity._set_stream_error = lambda _error: None
        return await entity.stream_source(), starts

    source, starts = asyncio.run(_run())

    assert source is None
    assert starts == 0


def test_video_camera_restarts_a_dead_host_worker() -> None:
    async def _run() -> tuple[str | None, int, int]:
        entity = _uninitialized_entity()
        entity._stream_lock = asyncio.Lock()
        entity._session = SimpleNamespace(
            stream_url="http://127.0.0.1/stale.flv",
            runner_process=SimpleNamespace(poll=lambda: 7),
        )
        stops = 0
        starts = 0

        async def _stop() -> None:
            nonlocal stops
            stops += 1
            entity._session = None

        async def _start() -> str:
            nonlocal starts
            starts += 1
            return "http://127.0.0.1/fresh.flv"

        entity._async_stop_active_session = _stop
        entity._async_start_stream = _start
        return await entity.stream_source(), stops, starts

    source, stops, starts = asyncio.run(_run())

    assert source == "http://127.0.0.1/fresh.flv"
    assert stops == 1
    assert starts == 1


def test_video_camera_creates_home_assistant_stream_from_live_source() -> None:
    async def _run() -> tuple[object, object]:
        entity = _uninitialized_entity()
        entity._create_stream_lock = None
        entity.stream = None
        entity.stream_options = {"use_wallclock_as_timestamps": True}
        entity.entity_id = "camera.dreame_live_video"
        entity.async_write_ha_state = lambda: None
        session = SimpleNamespace(stream_url="http://127.0.0.1/live.flv")

        async def _source() -> str:
            entity._session = session
            return session.stream_url

        entity.stream_source = _source

        dynamic_settings = object()

        class _Preferences:
            async def get_dynamic_stream_settings(self, entity_id: str) -> object:
                assert entity_id == entity.entity_id
                return dynamic_settings

        fake_stream = SimpleNamespace(set_update_callback=lambda callback: None)
        entity.hass = SimpleNamespace(
            data={video_camera_module.DATA_CAMERA_PREFS: _Preferences()}
        )
        with patch.object(
            video_camera_module,
            "create_stream",
            return_value=fake_stream,
        ) as create_stream:
            result = await entity.async_create_stream()
        return result, create_stream.call_args

    result, call = asyncio.run(_run())

    assert result is not None
    assert call.args[1] == "http://127.0.0.1/live.flv"
    assert call.kwargs["stream_label"] == "camera.dreame_live_video"


def test_video_camera_replaces_cached_stream_after_worker_exit() -> None:
    async def _run() -> tuple[object, int]:
        entity = _uninitialized_entity()
        entity._create_stream_lock = None
        entity._stream_lock = asyncio.Lock()
        entity.stream_options = {}
        entity.entity_id = "camera.dreame_live_video"
        entity.async_write_ha_state = lambda: None
        entity.stream = object()
        entity._session = SimpleNamespace(
            stream_url="http://127.0.0.1/stale.flv",
            runner_process=SimpleNamespace(poll=lambda: 7),
        )
        stops = 0

        async def _stop() -> None:
            nonlocal stops
            stops += 1
            entity.stream = None
            entity._session = None

        fresh_session = SimpleNamespace(
            stream_url="http://127.0.0.1/fresh.flv",
            runner_process=SimpleNamespace(poll=lambda: None),
        )

        async def _source() -> str:
            entity._session = fresh_session
            return fresh_session.stream_url

        class _Preferences:
            async def get_dynamic_stream_settings(self, _entity_id: str) -> object:
                return object()

        fresh_stream = SimpleNamespace(set_update_callback=lambda _callback: None)
        entity._async_stop_active_session = _stop
        entity.stream_source = _source
        entity.hass = SimpleNamespace(
            data={video_camera_module.DATA_CAMERA_PREFS: _Preferences()}
        )
        with patch.object(
            video_camera_module,
            "create_stream",
            return_value=fresh_stream,
        ):
            result = await entity.async_create_stream()
        return result, stops

    result, stops = asyncio.run(_run())

    assert result is not None
    assert stops == 1


def test_video_camera_does_not_cache_stream_after_turn_off_race() -> None:
    async def _run() -> tuple[object | None, int]:
        entity = _uninitialized_entity()
        entity._create_stream_lock = None
        entity._stream_lock = asyncio.Lock()
        entity.stream_options = {}
        entity.entity_id = "camera.dreame_live_video"
        entity.async_write_ha_state = lambda: None
        entity.stream = None
        session = SimpleNamespace(stream_url="http://127.0.0.1/live.flv")
        preferences_started = asyncio.Event()
        release_preferences = asyncio.Event()
        stops = 0

        async def _source() -> str:
            entity._session = session
            return session.stream_url

        async def _stop() -> None:
            nonlocal stops
            stops += 1
            entity.stream = None
            entity._session = None

        class _Preferences:
            async def get_dynamic_stream_settings(self, _entity_id: str) -> object:
                preferences_started.set()
                await release_preferences.wait()
                return object()

        entity.stream_source = _source
        entity._async_stop_active_session = _stop
        entity.hass = SimpleNamespace(
            data={video_camera_module.DATA_CAMERA_PREFS: _Preferences()}
        )
        create_task = asyncio.create_task(entity.async_create_stream())
        await preferences_started.wait()
        await entity.async_turn_off()
        release_preferences.set()
        result = await create_task
        return result, stops

    with patch.object(video_camera_module, "create_stream") as create_stream:
        result, stops = asyncio.run(_run())

    assert result is None
    assert stops == 1
    create_stream.assert_not_called()


def test_video_camera_cancellation_cleans_unadopted_session() -> None:
    async def _run() -> tuple[int, object | None]:
        entity = _uninitialized_entity()
        entity._create_stream_lock = None
        entity._stream_lock = asyncio.Lock()
        entity.stream_options = {}
        entity.entity_id = "camera.dreame_live_video"
        entity.async_write_ha_state = lambda: None
        entity.stream = None
        session = SimpleNamespace(stream_url="http://127.0.0.1/live.flv")
        preferences_started = asyncio.Event()
        stops = 0

        async def _source() -> str:
            entity._session = session
            return session.stream_url

        async def _stop() -> None:
            nonlocal stops
            stops += 1
            entity.stream = None
            entity._session = None

        class _Preferences:
            async def get_dynamic_stream_settings(self, _entity_id: str) -> object:
                preferences_started.set()
                await asyncio.Future()

        entity.stream_source = _source
        entity._async_stop_active_session = _stop
        entity.hass = SimpleNamespace(
            data={video_camera_module.DATA_CAMERA_PREFS: _Preferences()}
        )
        task = asyncio.create_task(entity.async_create_stream())
        await preferences_started.wait()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return stops, entity._session

    stops, session = asyncio.run(_run())

    assert stops == 1
    assert session is None


def test_video_camera_returns_jpeg_from_managed_flv_source() -> None:
    async def _run() -> tuple[
        bytes | None,
        tuple[object, object, object],
        int,
    ]:
        entity = _uninitialized_entity()
        calls: list[tuple[object, object, object]] = []
        entity._last_image = None
        entity.stream = None
        entity._stream_lock = asyncio.Lock()
        entity._create_stream_lock = None
        snapshot_session = SimpleNamespace(
            stream_url="http://127.0.0.1/live.flv"
        )
        stops = 0

        async def _source() -> str:
            entity._session = snapshot_session
            return snapshot_session.stream_url

        async def _stop() -> None:
            nonlocal stops
            stops += 1
            entity._session = None

        entity.stream_source = _source
        entity._async_stop_active_session = _stop
        entity.hass = SimpleNamespace(
            async_add_executor_job=lambda function, *args: asyncio.sleep(
                0,
                result=function(*args),
            )
        )

        def _decode(source: str, width: int | None, height: int | None) -> bytes:
            calls.append((source, width, height))
            return b"\xff\xd8real-jpeg\xff\xd9"

        with patch.object(video_camera_module, "_decode_flv_jpeg", _decode):
            image = await entity.async_camera_image(width=640, height=360)
        return image, calls[0], stops

    image, call, stops = asyncio.run(_run())

    assert image == b"\xff\xd8real-jpeg\xff\xd9"
    assert call == ("http://127.0.0.1/live.flv", 640, 360)
    assert stops == 1


def test_video_camera_snapshot_does_not_stop_session_adopted_by_hls() -> None:
    async def _run() -> int:
        entity = _uninitialized_entity()
        session = SimpleNamespace(stream_url="http://127.0.0.1/live.flv")
        entity._session = session
        entity.stream = None
        entity._stream_lock = asyncio.Lock()
        entity._create_stream_lock = asyncio.Lock()
        stops = 0

        async def _stop() -> None:
            nonlocal stops
            stops += 1

        entity._async_stop_active_session = _stop
        await entity._create_stream_lock.acquire()
        cleanup = asyncio.create_task(entity._async_stop_snapshot_session(session))
        await asyncio.sleep(0)
        entity.stream = object()
        entity._create_stream_lock.release()
        await cleanup
        return stops

    assert asyncio.run(_run()) == 0


def test_video_camera_snapshot_stops_after_failed_hls_adoption() -> None:
    async def _run() -> int:
        entity = _uninitialized_entity()
        session = SimpleNamespace(stream_url="http://127.0.0.1/live.flv")
        entity._session = session
        entity.stream = None
        entity._stream_lock = asyncio.Lock()
        entity._create_stream_lock = asyncio.Lock()
        stops = 0

        async def _stop() -> None:
            nonlocal stops
            stops += 1
            entity._session = None

        entity._async_stop_active_session = _stop
        await entity._create_stream_lock.acquire()
        cleanup = asyncio.create_task(entity._async_stop_snapshot_session(session))
        await asyncio.sleep(0)
        entity._create_stream_lock.release()
        await cleanup
        return stops

    assert asyncio.run(_run()) == 1


def test_video_camera_serializes_concurrent_snapshot_sessions() -> None:
    async def _run() -> tuple[int, int, int]:
        entity = _uninitialized_entity()
        entity._last_image = None
        entity.stream = None
        entity._stream_lock = asyncio.Lock()
        entity._create_stream_lock = None
        first_decode_started = asyncio.Event()
        release_first_decode = asyncio.Event()
        source_calls = 0
        decode_calls = 0
        stops = 0

        async def _source() -> str:
            nonlocal source_calls
            source_calls += 1
            session = SimpleNamespace(
                stream_url=f"http://127.0.0.1/live-{source_calls}.flv"
            )
            entity._session = session
            return session.stream_url

        async def _decode_job(_function, *_args) -> bytes:
            nonlocal decode_calls
            decode_calls += 1
            if decode_calls == 1:
                first_decode_started.set()
                await release_first_decode.wait()
            return b"\xff\xd8real-jpeg\xff\xd9"

        async def _stop() -> None:
            nonlocal stops
            stops += 1
            entity._session = None

        entity.stream_source = _source
        entity._async_stop_active_session = _stop
        entity.hass = SimpleNamespace(async_add_executor_job=_decode_job)

        first = asyncio.create_task(entity.async_camera_image())
        await first_decode_started.wait()
        second = asyncio.create_task(entity.async_camera_image())
        await asyncio.sleep(0)
        assert decode_calls == 1
        assert stops == 0
        release_first_decode.set()
        await asyncio.gather(first, second)
        return source_calls, decode_calls, stops

    assert asyncio.run(_run()) == (2, 2, 2)


def test_video_camera_stop_discards_cached_home_assistant_stream() -> None:
    async def _run() -> tuple[object | None, int]:
        entity = _uninitialized_entity()
        entity._attr_is_streaming = True
        stopped = 0

        class _Stream:
            async def stop(self) -> None:
                nonlocal stopped
                stopped += 1

        entity.stream = _Stream()
        await entity._async_stop_active_session()
        return entity.stream, stopped

    stream, stopped = asyncio.run(_run())

    assert stream is None
    assert stopped == 1


def test_video_camera_stop_continues_after_cached_stream_failure() -> None:
    async def _run() -> tuple[int, int]:
        entity = _uninitialized_entity()
        runtime = object()
        session = object()
        entity._runtime = runtime
        entity._session = session
        entity._attr_is_streaming = True
        runtime_stops = 0
        video_disables = 0

        class _Stream:
            async def stop(self) -> None:
                raise RuntimeError("HA stream stop failed")

        async def _stop_session(actual_runtime: object, actual_session: object) -> None:
            nonlocal runtime_stops
            assert actual_runtime is runtime
            assert actual_session is session
            runtime_stops += 1

        async def _disable() -> None:
            nonlocal video_disables
            video_disables += 1

        entity.stream = _Stream()
        entity._async_stop_session = _stop_session
        entity._async_disable_camera_stream = _disable
        entity.async_write_ha_state = lambda: None
        await entity._async_stop_active_session()
        return runtime_stops, video_disables

    assert asyncio.run(_run()) == (1, 1)


def test_video_camera_cancellation_stops_completed_native_startup() -> None:
    async def _run() -> tuple[int, bool]:
        entity = _uninitialized_entity()
        start_job = asyncio.get_running_loop().create_future()
        entity.hass = SimpleNamespace(
            async_add_executor_job=lambda *args: start_job,
            async_create_task=lambda coroutine: asyncio.create_task(coroutine),
        )
        stopped = 0

        async def _stop(runtime: object, session: object) -> None:
            nonlocal stopped
            stopped += 1

        entity._async_stop_session = _stop
        task = asyncio.create_task(
            entity._async_start_runtime_session(
                SimpleNamespace(start_live_stream=lambda inputs: None),
                object(),
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        with suppress(asyncio.CancelledError):
            await task
        returned_before_native_start = not start_job.done()
        start_job.set_result(object())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return stopped, returned_before_native_start

    assert asyncio.run(_run()) == (1, True)


def test_video_camera_disables_video_when_enable_attempt_raises() -> None:
    async def _run() -> tuple[str | None, list[bool]]:
        entity = _uninitialized_entity()
        calls: list[bool] = []

        class _Client:
            async def async_get_camera_stream_runtime_inputs(self) -> object:
                return SimpleNamespace(
                    ready=True,
                    source="dreame_third_video_tx",
                    missing_required=(),
                )

            async def async_set_camera_stream_enabled(self, enabled: bool) -> None:
                calls.append(enabled)
                if enabled:
                    raise RuntimeError("enable response was lost")

        entity.coordinator = SimpleNamespace(client=_Client(), data=object())
        entity.stream = None
        entity._attr_is_streaming = False
        entity._runtime_preparation_error = None
        entity._last_stream_disable_error = None
        entity._async_stop_active_session = lambda: asyncio.sleep(0)
        entity._create_runtime = lambda: object()
        entity._set_stream_error = lambda _error: None
        entity.hass = SimpleNamespace(
            async_add_executor_job=lambda function, *args: asyncio.sleep(
                0,
                result=function(*args),
            )
        )

        result = await entity._async_start_stream()
        return result, calls

    assert asyncio.run(_run()) == (None, [True, False])
