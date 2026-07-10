"""Contract tests for the Home Assistant Dreame live-video entity."""

from __future__ import annotations

import asyncio
import sys
from contextlib import suppress
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
from custom_components.dreame_lawn_mower.const import CONF_XP2P_RUNNER_COMMAND
from custom_components.dreame_lawn_mower.video_camera import (
    DreameLawnMowerVideoCamera,
)

from .fixture_data import load_json_fixture


def _uninitialized_entity(*, snapshot: object | None = None):
    entity = object.__new__(DreameLawnMowerVideoCamera)
    entity._entry = SimpleNamespace(
        options={CONF_XP2P_RUNNER_COMMAND: "xp2p-runner"}
    )
    entity.coordinator = SimpleNamespace(data=snapshot)
    entity._session = None
    entity._attr_is_on = True
    return entity


def test_video_camera_advertises_stop_control() -> None:
    features = _uninitialized_entity().supported_features

    assert features & CameraEntityFeature.STREAM
    assert features & CameraEntityFeature.ON_OFF
    assert "async_turn_on" in DreameLawnMowerVideoCamera.__dict__
    assert "async_turn_off" in DreameLawnMowerVideoCamera.__dict__


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
        entity.stream_source = lambda: asyncio.sleep(
            0,
            result="http://127.0.0.1/live.flv",
        )

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


def test_video_camera_cancellation_stops_completed_native_startup() -> None:
    async def _run() -> int:
        entity = _uninitialized_entity()
        start_job = asyncio.get_running_loop().create_future()
        entity.hass = SimpleNamespace(
            async_add_executor_job=lambda *args: start_job,
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
        start_job.set_result(object())
        with suppress(asyncio.CancelledError):
            await task
        return stopped

    assert asyncio.run(_run()) == 1
