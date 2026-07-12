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

import custom_components.dreame_lawn_mower.video_cached_xp2p as cached_xp2p_module
import custom_components.dreame_lawn_mower.video_camera as video_camera_module
import custom_components.dreame_lawn_mower.video_stream_helpers as video_helpers_module
from custom_components.dreame_lawn_mower import (
    video_provisioning_cache as provisioning_cache_module,
)
from custom_components.dreame_lawn_mower.const import (
    CONF_VIDEO_TRANSPORT,
    CONF_XP2P_LIBRARY_PATH,
    CONF_XP2P_RUNNER_COMMAND,
    VIDEO_TRANSPORT_AUTO,
    VIDEO_TRANSPORT_CLOUD,
    VIDEO_TRANSPORT_LAN,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.stream_health import (
    DreameLawnMowerStreamUrlProbeResult,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerXp2pLiveStreamSession,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.xp2p_config import (
    XP2P_PROTOCOL_AUTO,
    XP2P_PROTOCOL_TCP,
    DreameLawnMowerXp2pDeviceConfig,
)
from custom_components.dreame_lawn_mower.video_camera import (
    DreameLawnMowerVideoCamera,
)
from custom_components.dreame_lawn_mower.video_session_lifecycle import (
    DreameLawnMowerHaStreamIdleMonitor,
)
from custom_components.dreame_lawn_mower.video_stream_helpers import (
    split_runner_command,
)

from .fixture_data import load_json_fixture


def _uninitialized_entity(*, snapshot: object | None = None):
    class _ProvisioningCache:
        def __init__(self) -> None:
            self.loaded = True
            self.inputs = None
            self.device_config = None
            self._staged = None

        def stage_fresh_device_config(
            self,
            inputs: DreameLawnMowerCameraStreamRuntimeInputs,
        ) -> DreameLawnMowerXp2pDeviceConfig:
            config = provisioning_cache_module.resolve_xp2p_device_config(inputs)
            self._staged = (inputs, config)
            return config

        def resolve_device_config(
            self,
            inputs: DreameLawnMowerCameraStreamRuntimeInputs,
        ) -> DreameLawnMowerXp2pDeviceConfig | None:
            if self._staged is not None and self._staged[0] is inputs:
                return self._staged[1]
            return None

        @staticmethod
        async def async_save(
            _inputs: DreameLawnMowerCameraStreamRuntimeInputs,
            _config: DreameLawnMowerXp2pDeviceConfig,
        ) -> None:
            return None

        def resolve_for_transport(
            self,
            inputs: DreameLawnMowerCameraStreamRuntimeInputs,
            *,
            auto: bool,
        ) -> DreameLawnMowerXp2pDeviceConfig:
            config = self.resolve_device_config(inputs)
            if config is None:
                config = self.stage_fresh_device_config(inputs)
            if not auto:
                return config
            return DreameLawnMowerXp2pDeviceConfig(
                server=config.server,
                ip=config.ip,
                port=config.port,
                protocol_type=XP2P_PROTOCOL_AUTO,
                cross=False,
            )

    entity = object.__new__(DreameLawnMowerVideoCamera)
    entity._entry = SimpleNamespace(
        options={
            CONF_XP2P_RUNNER_COMMAND: "xp2p-runner",
            CONF_VIDEO_TRANSPORT: VIDEO_TRANSPORT_CLOUD,
        }
    )
    entity.coordinator = SimpleNamespace(data=snapshot, last_update_success=True)
    entity._session = None
    entity._runtime = None
    entity._prepared_runtime = None
    entity._runtime_prepare_task = None
    entity._stream_idle_monitor = SimpleNamespace(
        schedule=lambda _stream, _session: None,
        async_cancel=lambda: asyncio.sleep(0),
    )
    entity._attr_is_on = True
    entity._stream_lock = asyncio.Lock()
    entity._snapshot_lock = asyncio.Lock()
    entity._lan_cache = SimpleNamespace(inputs=None, endpoint=None)
    entity._provisioning_cache = _ProvisioningCache()
    entity._provisioning_cache_error = None
    entity._last_cached_xp2p_error = None
    entity._runtime_input_config = None
    entity._last_lan_error = None
    entity._last_video_transport = None
    return entity


def test_video_camera_advertises_stop_control() -> None:
    features = _uninitialized_entity().supported_features

    assert features & CameraEntityFeature.STREAM
    assert features & CameraEntityFeature.ON_OFF
    assert "async_turn_on" in DreameLawnMowerVideoCamera.__dict__
    assert "async_turn_off" in DreameLawnMowerVideoCamera.__dict__


def test_video_camera_auto_policy_prefers_direct_capable_sdk_negotiation() -> None:
    entity = _uninitialized_entity()
    entity._entry.options[CONF_VIDEO_TRANSPORT] = VIDEO_TRANSPORT_AUTO
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="test",
        did="did-1",
    )
    fetched = DreameLawnMowerXp2pDeviceConfig(
        server="stun.example.test",
        ip="192.0.2.1",
        port=20003,
        protocol_type=XP2P_PROTOCOL_TCP,
        cross=True,
    )

    with patch.object(
        provisioning_cache_module,
        "resolve_xp2p_device_config",
        return_value=fetched,
    ):
        config = entity._resolve_xp2p_config(inputs)

    assert config.server == fetched.server
    assert config.ip == fetched.ip
    assert config.port == fetched.port
    assert config.protocol_type == XP2P_PROTOCOL_AUTO
    assert config.cross is False


def test_video_camera_normalizes_legacy_lan_only_policy_to_cloud() -> None:
    entity = _uninitialized_entity()
    entity._entry.options[CONF_VIDEO_TRANSPORT] = VIDEO_TRANSPORT_LAN

    assert entity._video_transport == VIDEO_TRANSPORT_CLOUD


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


def test_cloud_video_camera_unavailable_when_coordinator_polling_fails() -> None:
    payload = load_json_fixture("a2_paused_diagnostics.json")
    snapshot = SimpleNamespace(**payload["data"]["snapshot"])
    entity = _uninitialized_entity(snapshot=snapshot)
    entity.coordinator.last_update_success = False

    assert entity.available is False


def test_cached_auto_video_remains_available_when_coordinator_polling_fails() -> None:
    entity = _uninitialized_entity(snapshot=None)
    entity._entry.options[CONF_VIDEO_TRANSPORT] = VIDEO_TRANSPORT_AUTO
    entity._lan_cache.inputs = object()
    entity._lan_cache.endpoint = object()
    entity.coordinator.last_update_success = False

    assert entity.available is True


def test_cached_auto_video_is_unavailable_for_confirmed_offline_snapshot() -> None:
    payload = load_json_fixture("a2_paused_diagnostics.json")
    snapshot = SimpleNamespace(
        **{
            **payload["data"]["snapshot"],
            "available": False,
        }
    )
    entity = _uninitialized_entity(snapshot=snapshot)
    entity._entry.options[CONF_VIDEO_TRANSPORT] = VIDEO_TRANSPORT_AUTO
    entity._provisioning_cache.inputs = object()
    entity._provisioning_cache.device_config = object()

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
    with patch.object(video_helpers_module.platform, "system", return_value="Windows"):
        command = split_runner_command(
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
    config_fetcher = runtime_type.call_args.kwargs["config_fetcher"]
    assert config_fetcher.__self__ is entity
    assert config_fetcher.__func__ is DreameLawnMowerVideoCamera._resolve_xp2p_config


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
            entity._session = SimpleNamespace(stream_url="http://127.0.0.1/live.flv")
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
        snapshot_session = SimpleNamespace(stream_url="http://127.0.0.1/live.flv")
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

        with patch.object(
            video_camera_module.video_helpers,
            "decode_flv_jpeg",
            _decode,
        ):
            image = await entity.async_camera_image(width=640, height=360)
        return image, calls[0], stops

    image, call, stops = asyncio.run(_run())

    assert image == b"\xff\xd8real-jpeg\xff\xd9"
    assert call == ("http://127.0.0.1/live.flv", 640, 360)
    assert stops == 1


def test_video_camera_snapshot_start_timeout_returns_last_image() -> None:
    async def _run() -> tuple[bytes | None, bool]:
        entity = _uninitialized_entity()
        entity._last_image = b"\xff\xd8cached-jpeg\xff\xd9"
        cancelled = False

        async def _source() -> str:
            nonlocal cancelled
            try:
                await asyncio.Future()
            finally:
                cancelled = True
            raise AssertionError("unreachable")

        entity.stream_source = _source
        with patch.object(
            video_camera_module,
            "_SNAPSHOT_STREAM_START_TIMEOUT",
            0.01,
        ):
            image = await entity.async_camera_image()
        return image, cancelled

    assert asyncio.run(_run()) == (b"\xff\xd8cached-jpeg\xff\xd9", True)


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


def test_video_camera_late_native_cleanup_preserves_newer_session() -> None:
    async def _run() -> int:
        entity = _uninitialized_entity()
        runtime = object()
        entity._runtime = runtime
        entity._session = SimpleNamespace(service_id="product-1/device-1")
        stopped = 0

        async def _stop(_runtime: object, _session: object) -> None:
            nonlocal stopped
            stopped += 1

        entity._async_stop_session = _stop
        await entity._async_cleanup_late_start(
            runtime,
            SimpleNamespace(service_id="product-1/device-1"),
        )
        return stopped

    assert asyncio.run(_run()) == 0


def test_video_camera_late_process_cleanup_stops_distinct_worker() -> None:
    async def _run() -> int:
        entity = _uninitialized_entity()
        runtime = object()
        entity._runtime = runtime
        entity._session = SimpleNamespace(
            service_id="product-1/device-1",
            runner_process=object(),
        )
        stopped = 0

        async def _stop(_runtime: object, _session: object) -> None:
            nonlocal stopped
            stopped += 1

        entity._async_stop_session = _stop
        await entity._async_cleanup_late_start(
            runtime,
            SimpleNamespace(
                service_id="product-1/device-1",
                runner_process=object(),
            ),
        )
        return stopped

    assert asyncio.run(_run()) == 1


def test_video_camera_setup_does_not_wait_for_runtime_preparation() -> None:
    async def _run() -> tuple[bool, bool]:
        entity = _uninitialized_entity(
            snapshot=SimpleNamespace(capabilities=("video",), raw_info={})
        )
        entity._lan_cache = SimpleNamespace(loaded=True, inputs=None)
        entity._runtime_prepare_task = None
        preparation = asyncio.get_running_loop().create_future()
        entity.hass = SimpleNamespace(
            async_create_task=asyncio.create_task,
            async_add_executor_job=lambda *_args: preparation,
        )

        async def _base_added(_entity: object) -> None:
            return None

        with patch.object(
            video_camera_module.CoordinatorEntity,
            "async_added_to_hass",
            new=_base_added,
        ):
            await entity.async_added_to_hass()
        await asyncio.sleep(0)
        task = entity._runtime_prepare_task
        assert task is not None
        pending = not task.done()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return pending, preparation.cancelled()

    assert asyncio.run(_run()) == (True, True)


def test_video_camera_stream_reuses_inflight_runtime_preparation() -> None:
    async def _run() -> tuple[bool, bool]:
        entity = _uninitialized_entity()
        prepared_runtime = object()
        entity._prepared_runtime = None

        async def _prepare() -> None:
            await asyncio.sleep(0)
            entity._prepared_runtime = prepared_runtime

        preparation = asyncio.create_task(_prepare())
        entity._runtime_prepare_task = preparation

        def _create() -> object:
            raise AssertionError("Prepared runtime should be reused")

        entity._create_runtime = _create

        async def _executor(function, *args):
            return function(*args)

        entity.hass = SimpleNamespace(async_add_executor_job=_executor)
        runtime = await entity._async_get_runtime()
        return (
            runtime is prepared_runtime,
            entity._runtime_prepare_task is None,
        )

    assert asyncio.run(_run()) == (True, True)


def test_video_camera_hls_idle_stops_owned_session() -> None:
    async def _run() -> int:
        entity = _uninitialized_entity()
        session = SimpleNamespace(service_id="product-1/device-1")
        outputs_calls = 0
        stops = 0

        class _Stream:
            def outputs(self) -> dict[str, object]:
                nonlocal outputs_calls
                outputs_calls += 1
                return {"hls": object()} if outputs_calls == 1 else {}

        stream = _Stream()
        entity.stream = stream
        entity._session = session

        async def _stop() -> None:
            nonlocal stops
            stops += 1
            entity.stream = None
            entity._session = None

        entity._async_stop_active_session = _stop
        monitor = DreameLawnMowerHaStreamIdleMonitor(
            SimpleNamespace(async_create_task=asyncio.create_task),
            stream_lock=entity._stream_lock,
            is_current=lambda actual_stream, actual_session: (
                entity.stream is actual_stream and entity._session is actual_session
            ),
            stop_active=entity._async_stop_active_session,
            poll_interval=0,
        )
        entity._stream_idle_monitor = monitor
        monitor.schedule(stream, session)
        while stops == 0:
            await asyncio.sleep(0)
        return stops

    assert asyncio.run(_run()) == 1


def test_video_camera_auto_refreshes_after_stale_cached_lan_endpoint() -> None:
    async def _run() -> tuple[str | None, int, int, list[object]]:
        entity = _uninitialized_entity()
        entity._entry.options[CONF_VIDEO_TRANSPORT] = VIDEO_TRANSPORT_AUTO
        cached_inputs = DreameLawnMowerCameraStreamRuntimeInputs(
            source="lan_video_cache",
            did="did-1",
            product_id="product-1",
            device_name="device-1",
        )
        fresh_inputs = DreameLawnMowerCameraStreamRuntimeInputs(
            source="dreame_third_video_tx",
            did="did-1",
            product_id="product-1",
            device_name="device-1",
            lan_client_token="fresh-access-token",
        )

        class _Cache:
            def __init__(self) -> None:
                self.inputs = cached_inputs
                self.endpoint = object()
                self.clears = 0

            async def async_clear_endpoint(self) -> None:
                self.clears += 1
                self.endpoint = None

            async def async_save_identity(self, _inputs: object) -> None:
                return None

        class _Client:
            def __init__(self) -> None:
                self.fetches = 0

            async def async_get_camera_stream_runtime_inputs(self) -> object:
                self.fetches += 1
                return fresh_inputs

            async def async_set_camera_stream_enabled(self, _enabled: bool) -> None:
                raise AssertionError(
                    "Fresh LAN retry should succeed before cloud video"
                )

        cache = _Cache()
        client = _Client()
        attempts: list[object] = []
        entity._lan_cache = cache
        entity.coordinator = SimpleNamespace(client=client, data=object())
        entity._lan_cache_error = None
        entity._runtime_preparation_error = None
        entity._async_stop_active_session = lambda: asyncio.sleep(0)
        entity._create_runtime = lambda: object()

        async def _executor(function, *args):
            return function(*args)

        async def _try_lan(_runtime: object, inputs: object) -> str | None:
            attempts.append(inputs)
            if inputs is cached_inputs:
                entity._last_lan_error = "cached endpoint did not respond"
                return None
            return "http://127.0.0.1/fresh-lan.flv"

        entity.hass = SimpleNamespace(async_add_executor_job=_executor)
        entity._async_try_lan_stream = _try_lan
        source = await entity._async_start_stream()
        return source, cache.clears, client.fetches, attempts

    source, clears, fetches, attempts = asyncio.run(_run())

    assert source == "http://127.0.0.1/fresh-lan.flv"
    assert clears == 1
    assert fetches == 1
    assert attempts[0].source == "lan_video_cache"
    assert attempts[1].lan_client_token == "fresh-access-token"


def test_video_camera_disables_video_when_enable_attempt_raises() -> None:
    async def _run() -> tuple[str | None, list[bool]]:
        entity = _uninitialized_entity()
        calls: list[bool] = []

        class _Client:
            async def async_get_camera_stream_runtime_inputs(self) -> object:
                return DreameLawnMowerCameraStreamRuntimeInputs(
                    source="dreame_third_video_tx",
                    did="did-1",
                    product_id="product-1",
                    device_name="device-1",
                    p2p_info="p2p-info-1",
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


def test_video_camera_caches_provisioning_only_after_healthy_flv() -> None:
    async def _attempt(*, healthy: bool) -> tuple[str | None, int]:
        entity = _uninitialized_entity()
        inputs = DreameLawnMowerCameraStreamRuntimeInputs(
            source="dreame_third_video_tx",
            did="did-1",
            channel_id="channel-1",
            product_id="product-1",
            device_name="device-1",
            p2p_info="p2p-info-1",
            secret_id="secret-id-1",
            secret_key="secret-key-1",
            app_id="app-id-1",
            app_secret="app-secret-1",
        )
        config = DreameLawnMowerXp2pDeviceConfig(
            server="stun.example.test",
            ip="192.0.2.1",
            port=20002,
            protocol_type=XP2P_PROTOCOL_TCP,
        )

        class _ProvisioningCache:
            inputs = None
            device_config = None

            def __init__(self) -> None:
                self.staged_inputs = None
                self.saves = 0

            def stage_fresh_device_config(self, actual_inputs: object) -> object:
                self.staged_inputs = actual_inputs
                return config

            def resolve_device_config(self, actual_inputs: object) -> object | None:
                return config if actual_inputs is self.staged_inputs else None

            async def async_save(
                self,
                actual_inputs: object,
                actual_config: object,
            ) -> None:
                assert actual_inputs is inputs
                assert actual_config is config
                self.saves += 1

        class _LanCache:
            inputs = None
            endpoint = None

            @staticmethod
            async def async_save_identity(_inputs: object) -> None:
                return None

        class _Client:
            async def async_get_camera_stream_runtime_inputs(self) -> object:
                return inputs

            async def async_set_camera_stream_enabled(self, _enabled: bool) -> None:
                return None

        class _Runtime:
            @staticmethod
            def start_live_stream(
                actual_inputs: object,
            ) -> DreameLawnMowerXp2pLiveStreamSession:
                assert actual_inputs is inputs
                return DreameLawnMowerXp2pLiveStreamSession(
                    service_id="product-1/device-1",
                    stream_url="http://127.0.0.1/cloud.flv",
                )

            @staticmethod
            def stop_live_stream(_session: object) -> None:
                return None

        cache = _ProvisioningCache()
        entity._provisioning_cache = cache
        entity._lan_cache = _LanCache()
        entity.coordinator = SimpleNamespace(client=_Client(), data=object())
        entity._prepared_runtime = _Runtime()
        entity._runtime_preparation_error = None
        entity._last_stream_health = None
        entity._last_error = None
        entity._attr_is_streaming = False
        entity._lan_cache_error = None
        entity._last_stream_disable_error = None
        entity.stream = None
        entity.async_write_ha_state = lambda: None

        async def _executor(function, *args):
            return function(*args)

        entity.hass = SimpleNamespace(async_add_executor_job=_executor)
        health = DreameLawnMowerStreamUrlProbeResult(
            available=healthy,
            flv_header_present=healthy,
            error_category=None if healthy else "invalid_header",
        )
        with patch.object(
            video_camera_module.video_helpers,
            "probe_stream_health_and_route",
            return_value=health,
        ):
            source = await entity._async_start_stream()
        return source, cache.saves

    assert asyncio.run(_attempt(healthy=False)) == (None, 0)
    assert asyncio.run(_attempt(healthy=True)) == (
        "http://127.0.0.1/cloud.flv",
        1,
    )


def test_video_camera_auto_uses_cached_xp2p_without_video_cloud_calls() -> None:
    async def _run() -> tuple[str | None, int, str | None, bool]:
        entity = _uninitialized_entity()
        entity._entry.options[CONF_VIDEO_TRANSPORT] = VIDEO_TRANSPORT_AUTO
        inputs = DreameLawnMowerCameraStreamRuntimeInputs(
            source="video_provisioning_cache",
            did="did-1",
            product_id="product-1",
            device_name="device-1",
            p2p_info="p2p-info-1",
        )
        config = DreameLawnMowerXp2pDeviceConfig(protocol_type=XP2P_PROTOCOL_TCP)
        entity._provisioning_cache = SimpleNamespace(
            inputs=inputs,
            device_config=config,
        )

        class _Client:
            async def async_get_camera_stream_runtime_inputs(self) -> object:
                raise AssertionError("Cached XP2P must not fetch video-cloud inputs")

            async def async_set_camera_stream_enabled(self, _enabled: bool) -> None:
                raise AssertionError("Cached XP2P must not toggle cloud video")

        class _Runtime:
            def __init__(self) -> None:
                self.starts = 0

            def start_live_stream(
                self,
                actual_inputs: object,
            ) -> DreameLawnMowerXp2pLiveStreamSession:
                assert actual_inputs is inputs
                self.starts += 1
                return DreameLawnMowerXp2pLiveStreamSession(
                    service_id="product-1/device-1",
                    stream_url="http://127.0.0.1/cached.flv",
                )

            def stop_live_stream(self, _session: object) -> None:
                return None

        class _Health:
            flv_header_present = True

            @staticmethod
            def as_dict() -> dict[str, object]:
                return {"flv_header_present": True}

        runtime = _Runtime()
        entity.coordinator = SimpleNamespace(client=_Client(), data=None)
        entity._prepared_runtime = runtime
        entity._runtime_preparation_error = None
        entity._last_stream_health = None
        entity._last_error = None
        entity._attr_is_streaming = False
        entity._last_stream_disable_error = None
        entity.async_write_ha_state = lambda: None

        async def _executor(function, *args):
            return function(*args)

        entity.hass = SimpleNamespace(async_add_executor_job=_executor)
        with patch.object(
                cached_xp2p_module,
                "probe_stream_health_and_route",
            return_value=_Health(),
        ):
            source = await entity._async_start_stream()
        session = entity._session
        await entity._async_stop_active_session()
        return (
            source,
            runtime.starts,
            entity._last_video_transport,
            bool(session and session.camera_toggle_managed),
        )

    assert asyncio.run(_run()) == (
        "http://127.0.0.1/cached.flv",
        1,
        "cached_xp2p",
        False,
    )


def test_video_camera_stop_does_not_call_cloud_cleanup_for_lan_session() -> None:
    async def _run() -> tuple[int, int]:
        entity = _uninitialized_entity()
        runtime = object()
        session = SimpleNamespace(transport=VIDEO_TRANSPORT_LAN)
        entity._runtime = runtime
        entity._session = session
        entity._attr_is_streaming = True
        entity.stream = None
        runtime_stops = 0
        cloud_disables = 0

        async def _stop(actual_runtime: object, actual_session: object) -> None:
            nonlocal runtime_stops
            assert actual_runtime is runtime
            assert actual_session is session
            runtime_stops += 1

        async def _disable() -> None:
            nonlocal cloud_disables
            cloud_disables += 1

        entity._async_stop_session = _stop
        entity._async_disable_camera_stream = _disable
        entity.async_write_ha_state = lambda: None
        await entity._async_stop_active_session()
        return runtime_stops, cloud_disables

    assert asyncio.run(_run()) == (1, 0)
