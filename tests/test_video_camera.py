"""Contract tests for the Home Assistant Dreame live-video entity."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import suppress
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

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
import custom_components.dreame_lawn_mower.video_stream_helpers as video_helpers_module
from custom_components.dreame_lawn_mower import (
    video_camera_startup as video_camera_startup_module,
)
from custom_components.dreame_lawn_mower import (
    video_camera_state as video_camera_state_module,
)
from custom_components.dreame_lawn_mower import (
    video_camera_types as video_camera_types_module,
)
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
from custom_components.dreame_lawn_mower.diagnostic_events import (
    DreameLawnMowerDiagnosticEventStore,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    DreameLawnMowerXp2pHostAssets,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    xp2p_host_runtime as xp2p_host_runtime_module,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.stream_health import (
    DreameLawnMowerStreamUrlProbeResult,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerVideoRuntimeError,
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

    async def _async_refresh() -> None:
        return None

    async def _async_refresh_video_safety_state() -> object:
        return entity.coordinator.data

    entity = object.__new__(DreameLawnMowerVideoCamera)
    entity._descriptor = SimpleNamespace(
        did=f"test-device-{id(entity)}",
        unique_id=f"test-device-{id(entity)}",
        model="dreame.mower.test",
    )
    entity._attr_unique_id = f"{entity._descriptor.unique_id}_live_video"
    entity._entry = SimpleNamespace(
        options={
            CONF_XP2P_RUNNER_COMMAND: "xp2p-runner",
            CONF_VIDEO_TRANSPORT: VIDEO_TRANSPORT_CLOUD,
        }
    )
    entity.coordinator = SimpleNamespace(
        data=snapshot,
        last_update_success=True,
        async_refresh=_async_refresh,
        async_refresh_video_safety_state=_async_refresh_video_safety_state,
    )
    entity._session = None
    entity._pending_provisioning_inputs = None
    entity._runtime = None
    entity._prepared_runtime = None
    entity._runtime_prepare_task = None
    entity._state_gate_cleanup_task = None
    entity._stream_idle_monitor = SimpleNamespace(
        schedule=lambda _stream, _session: None,
        async_cancel=lambda: asyncio.sleep(0),
    )
    entity._attr_is_on = True
    entity._stream_lock = asyncio.Lock()
    entity._snapshot_lock = asyncio.Lock()
    entity._snapshot_requests = 0
    entity._snapshot_owned_stream = None
    entity._lan_cache = SimpleNamespace(inputs=None, endpoint=None)
    entity._provisioning_cache = _ProvisioningCache()
    entity._provisioning_cache_error = None
    entity._last_cached_xp2p_error = None
    entity._runtime_input_config = None
    entity._last_error = None
    entity._last_error_at = None
    entity._last_error_code = None
    entity._last_error_stage = None
    entity._last_runtime_inputs_ready = None
    entity._last_runtime_inputs_source = None
    entity._last_runtime_inputs_missing = ()
    entity._last_runtime_inputs_provisioning_issue = None
    entity._last_runtime_input_diagnostics = None
    entity._last_stream_health = None
    entity._last_stream_enable_result = None
    entity._last_stream_disable_error = None
    entity._last_native_runtime_diagnostics = None
    entity._runtime_preparation_error = None
    entity._lan_cache_error = None
    entity._last_lan_error = None
    entity._bypass_lan = False
    entity._last_video_transport = None
    entity._last_video_transport_attempted = None
    entity._video_capability_observed = video_camera_module.snapshot_advertises_video(
        snapshot
    )
    entity._video_recovery_failure_count = 0
    entity._video_recovery_success_count = 0
    entity._video_recovery_consecutive_failures = 0
    entity._video_recovery_pending = False
    entity._video_retry_not_before = 0.0
    entity._last_stream_recovered_at = None
    entity._video_first_media_at = None
    entity._last_stream_cleanup_reason = None
    entity._last_stream_cleanup_error = None
    entity._last_stream_cleanup_error_stage = None
    entity._last_stream_cleanup_at = None
    return entity


def test_video_camera_advertises_stop_control() -> None:
    features = _uninitialized_entity().supported_features

    assert features & CameraEntityFeature.STREAM
    assert features & CameraEntityFeature.ON_OFF
    assert "async_turn_on" in DreameLawnMowerVideoCamera.__dict__
    assert "async_turn_off" in DreameLawnMowerVideoCamera.__dict__


def test_managed_runtime_environment_is_privacy_safe(
    monkeypatch,
) -> None:
    monkeypatch.setattr(video_helpers_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(video_helpers_module.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(video_helpers_module.platform, "release", lambda: "6.12-test")
    monkeypatch.setattr(
        video_helpers_module.platform,
        "libc_ver",
        lambda: ("glibc", "2.39"),
    )
    monkeypatch.setattr(
        video_helpers_module.os,
        "sysconf",
        lambda _name: 4096,
        raising=False,
    )

    assert video_helpers_module.managed_runtime_environment() == {
        "system": "linux",
        "machine": "x86_64",
        "execution_mode": "qemu_aarch64",
        "supported": True,
        "kernel_release": "6.12-test",
        "page_size": 4096,
        "libc": "glibc",
        "libc_version": "2.39",
    }


def test_managed_runtime_environment_reports_large_page_compatibility(
    monkeypatch,
) -> None:
    monkeypatch.setattr(video_helpers_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        video_helpers_module.platform,
        "machine",
        lambda: "aarch64",
    )
    monkeypatch.setattr(
        video_helpers_module.platform,
        "release",
        lambda: "6.12-test",
    )
    monkeypatch.setattr(
        video_helpers_module.platform,
        "libc_ver",
        lambda: ("musl", "1"),
    )
    monkeypatch.setattr(
        video_helpers_module.os,
        "sysconf",
        lambda _name: 16384,
        raising=False,
    )

    environment = video_helpers_module.managed_runtime_environment()

    assert environment["execution_mode"] == "native_aarch64_large_page"
    assert environment["page_size"] == 16384
    assert environment["supported"] is True


def test_video_camera_facade_preserves_split_method_surface() -> None:
    """Keep reflection and direct module imports stable after decomposition."""
    split_methods = {
        video_camera_state_module.DreameLawnMowerVideoStateMixin: (
            "_handle_coordinator_update",
            "_async_cleanup_for_state_gate",
            "available",
            "device_info",
            "extra_state_attributes",
        ),
        video_camera_startup_module.DreameLawnMowerVideoStartupMixin: (
            "_async_start_stream",
            "_async_refresh_video_start_state",
            "_video_start_is_blocked",
            "_async_try_lan_stream",
            "_async_try_cached_xp2p_stream",
            "_async_get_runtime_inputs",
            "_async_cache_healthy_provisioning",
            "_async_start_lan_runtime_session",
            "_adopt_stream_session",
            "_async_adopt_stream_session",
            "_async_cleanup_rejected_session",
            "_with_lan_failure",
            "_async_start_runtime_session",
            "_schedule_late_start_cleanup",
            "_async_cleanup_late_start",
        ),
    }

    for mixin, method_names in split_methods.items():
        for method_name in method_names:
            assert (
                DreameLawnMowerVideoCamera.__dict__[method_name]
                is mixin.__dict__[method_name]
            )

    assert (
        video_camera_module._runtime_inputs_not_ready_message
        is video_camera_startup_module._runtime_inputs_not_ready_message
    )
    assert (
        video_camera_module._DreameVideoRuntime
        is video_camera_types_module._DreameVideoRuntime
    )


def test_split_startup_observes_historical_facade_monkeypatch() -> None:
    """Keep dependency injection through the original module path working."""

    async def _run() -> tuple[
        str | None,
        list[object],
        object,
        object,
        DreameLawnMowerVideoCamera,
    ]:
        entity = _uninitialized_entity()
        observed: list[object] = []
        runtime = object()
        inputs = object()

        async def _start_cached(
            patched_runtime: object,
            patched_inputs: object,
            *,
            start_session: object,
        ) -> SimpleNamespace:
            observed.extend((patched_runtime, patched_inputs, start_session))
            return SimpleNamespace(session=None, error=None)

        with patch.object(
            video_camera_module,
            "async_start_cached_xp2p",
            _start_cached,
        ):
            result = await entity._async_try_cached_xp2p_stream(runtime, inputs)
        return result, observed, runtime, inputs, entity

    result, observed, runtime, inputs, entity = asyncio.run(_run())

    assert result is None
    assert observed[0] is runtime
    assert observed[1] is inputs
    assert observed[2].__self__ is entity


def test_video_failure_is_sanitized_logged_once_and_preserved(caplog) -> None:
    entity = _uninitialized_entity(
        snapshot=SimpleNamespace(firmware_version="4.3.6_0625")
    )
    entity._descriptor = SimpleNamespace(model="dreame.mower.g2568a")
    entity.coordinator.diagnostic_events = DreameLawnMowerDiagnosticEventStore()
    entity.async_write_ha_state = lambda: None

    entity._set_stream_error("accessToken=secret failed", stage="cloud_start")
    entity._set_stream_error("accessToken=secret failed", stage="cloud_start")

    assert entity._last_error == "accessToken=**REDACTED** failed"
    assert entity._last_error_code == "video_cloud_start_failed"
    assert entity._last_error_stage == "cloud_start"
    assert entity._last_error_at is not None
    events = entity.coordinator.diagnostic_events.as_list()
    assert len(events) == 1
    assert events[0]["count"] == 2
    assert events[0]["context"]["model"] == "dreame.mower.g2568a"
    assert caplog.text.count("video_cloud_start_failed") == 1
    assert "secret" not in caplog.text


def test_video_camera_preserves_runtime_input_telemetry_on_cloud_failure() -> None:
    async def _raise_cloud_error():
        raise RuntimeError("cloud request failed")

    entity = _uninitialized_entity()
    entity.coordinator.client = SimpleNamespace(
        async_get_camera_stream_runtime_inputs=_raise_cloud_error,
        last_camera_stream_diagnostics={
            "operation": "camera_stream_inputs",
            "stages": [
                {
                    "stage": "cloud_access_token",
                    "error": {
                        "message": "accessToken=secret-token failed",
                    },
                }
            ],
        },
    )

    with pytest.raises(RuntimeError, match="cloud request failed"):
        asyncio.run(entity._async_get_runtime_inputs())

    diagnostics = entity._last_runtime_input_diagnostics
    assert diagnostics["stages"][0]["stage"] == "cloud_access_token"
    assert diagnostics["stages"][0]["error"]["message"] == (
        "accessToken=**REDACTED** failed"
    )


def test_video_camera_preserves_runtime_input_telemetry_on_incomplete_result() -> None:
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="device-1",
        diagnostics={
            "operation": "camera_stream_inputs",
            "ready": False,
            "missing_required": ("product_id", "device_name", "p2p_info"),
        },
    )

    async def _runtime_inputs():
        return inputs

    entity = _uninitialized_entity()
    entity.coordinator.client = SimpleNamespace(
        async_get_camera_stream_runtime_inputs=_runtime_inputs,
    )

    result = asyncio.run(entity._async_get_runtime_inputs())

    assert result is inputs
    assert entity._last_runtime_input_diagnostics == {
        "operation": "camera_stream_inputs",
        "ready": False,
        "missing_required": ["product_id", "device_name", "p2p_info"],
    }
    assert entity._last_runtime_inputs_ready is False
    assert entity._last_runtime_inputs_missing == (
        "product_id",
        "device_name",
        "p2p_info",
    )
    assert entity._last_runtime_inputs_provisioning_issue is None


def test_video_camera_explains_unprovisioned_device_triple() -> None:
    diagnostics = load_json_fixture("q2501a_ru_xp2p_unprovisioned.json")
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="sanitized-device",
        diagnostics=diagnostics,
    )

    message = video_camera_module._runtime_inputs_not_ready_message(inputs)

    assert "has not provisioned an XP2P video identity" in message
    assert "current account/region" in message
    assert "Dreamehome or MOVAhome" in message
    assert "product_id" not in message


def test_video_camera_cloud_start_surfaces_unprovisioned_device_triple() -> None:
    async def _run() -> tuple[str | None, str | None, str | None]:
        diagnostics = load_json_fixture("q2501a_ru_xp2p_unprovisioned.json")
        inputs = DreameLawnMowerCameraStreamRuntimeInputs(
            source="dreame_third_video_tx",
            did="sanitized-device",
            diagnostics=diagnostics,
        )

        class _Client:
            async def async_get_camera_stream_runtime_inputs(self) -> object:
                return inputs

            async def async_set_camera_stream_enabled(
                self,
                _enabled: bool,
            ) -> None:
                raise AssertionError("Unprovisioned video must not be enabled")

        entity = _uninitialized_entity()
        entity.coordinator.client = _Client()
        entity.coordinator.data = object()
        entity._prepared_runtime = object()
        entity._async_stop_active_session = lambda: asyncio.sleep(0)
        entity.async_write_ha_state = lambda: None

        source = await entity._async_start_stream()
        return (
            source,
            entity._last_error,
            entity._last_runtime_inputs_provisioning_issue,
        )

    source, error, issue = asyncio.run(_run())

    assert source is None
    assert error is not None
    assert "has not provisioned an XP2P video identity" in error
    assert issue == "device_triple_missing"


def test_video_camera_first_relay_start_does_not_stop_its_own_stream() -> None:
    async def _run() -> tuple[str | None, int]:
        entity = _uninitialized_entity()
        entity.stream = object()
        stops = 0

        async def _stop() -> None:
            nonlocal stops
            stops += 1

        async def _runtime() -> object:
            raise RuntimeError("runtime unavailable")

        entity._async_stop_active_session = _stop
        entity._async_get_runtime = _runtime
        entity.async_write_ha_state = lambda: None

        return await entity._async_start_stream(), stops

    source, stops = asyncio.run(_run())

    assert source is None
    assert stops == 0


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


def test_video_camera_normalizes_legacy_lan_only_policy_to_auto() -> None:
    entity = _uninitialized_entity()
    entity._entry.options[CONF_VIDEO_TRANSPORT] = VIDEO_TRANSPORT_LAN

    assert entity._video_transport == VIDEO_TRANSPORT_AUTO


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


def test_cached_cloud_video_remains_available_without_capability_metadata() -> None:
    entity = _uninitialized_entity(
        snapshot=SimpleNamespace(
            available=True,
            capabilities=(),
            raw_info={"deviceInfo": {}, "videoStatus": None},
        )
    )
    entity._entry.options[CONF_VIDEO_TRANSPORT] = VIDEO_TRANSPORT_CLOUD
    entity._provisioning_cache.inputs = object()
    entity._provisioning_cache.device_config = object()

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


def test_video_camera_remembers_support_when_metadata_disappears() -> None:
    snapshot = SimpleNamespace(
        available=True,
        state="mowing",
        activity="mowing",
        docked=False,
        raw_docked=False,
        returning=False,
        capabilities=("video",),
        raw_info={},
        raw_attributes={},
    )
    entity = _uninitialized_entity(snapshot=snapshot)
    entity.coordinator.data = SimpleNamespace(
        available=True,
        state="mowing",
        activity="mowing",
        docked=False,
        raw_docked=False,
        returning=False,
        capabilities=(),
        raw_info={},
        raw_attributes={},
    )

    with patch.object(
        video_camera_module.CoordinatorEntity,
        "_handle_coordinator_update",
        lambda _entity: None,
    ):
        entity._handle_coordinator_update()

    assert entity.available is True
    assert entity.extra_state_attributes["video_capability_advertised"] is False
    assert entity.extra_state_attributes["video_capability_observed"] is True


def test_video_camera_restores_support_from_healthy_cache_after_docked_reload() -> None:
    async def _run() -> tuple[bool, bool]:
        snapshot = SimpleNamespace(
            available=True,
            state="charging",
            activity="charging",
            docked=True,
            raw_docked=True,
            returning=False,
            capabilities=(),
            raw_info={},
            raw_attributes={},
        )
        entity = _uninitialized_entity(snapshot=snapshot)

        class _Cache:
            loaded = False
            inputs = None
            device_config = None

            async def async_load(self) -> None:
                self.loaded = True
                self.inputs = object()
                self.device_config = object()

        async def _executor(function, *args):
            return function(*args)

        entity._provisioning_cache = _Cache()
        entity._lan_cache = SimpleNamespace(
            loaded=True,
            inputs=None,
            endpoint=None,
        )
        entity.hass = SimpleNamespace(
            async_add_executor_job=_executor,
            async_create_task=asyncio.create_task,
        )

        async def _base_added(_entity: object) -> None:
            return None

        with patch.object(
            video_camera_module.CoordinatorEntity,
            "async_added_to_hass",
            new=_base_added,
        ):
            await entity.async_added_to_hass()

        observed_after_reload = entity._video_capability_observed
        entity.coordinator.data = SimpleNamespace(
            available=True,
            state="mowing",
            activity="mowing",
            docked=False,
            raw_docked=False,
            returning=False,
            capabilities=(),
            raw_info={},
            raw_attributes={},
        )
        available_after_undock = entity.available
        task = entity._runtime_prepare_task
        if task is not None:
            await task
        return observed_after_reload, available_after_undock

    assert asyncio.run(_run()) == (True, True)


def test_video_camera_dock_transition_recovers_from_stale_raw_returning() -> None:
    async def _run() -> tuple[
        int,
        int,
        bool,
        str | None,
        bool,
        list[str],
        str | None,
    ]:
        snapshot = SimpleNamespace(
            available=True,
            state="mowing",
            activity="mowing",
            docked=False,
            raw_docked=False,
            returning=False,
            capabilities=("video",),
            raw_info={},
            raw_attributes={},
        )
        entity = _uninitialized_entity(snapshot=snapshot)
        runtime_stops = 0
        camera_disables = 0

        class _Runtime:
            def stop_live_stream(self, _session: object) -> None:
                nonlocal runtime_stops
                runtime_stops += 1

        class _Client:
            async def async_set_camera_stream_enabled(self, _enabled: bool) -> None:
                nonlocal camera_disables
                camera_disables += 1
                raise RuntimeError(
                    "Dreame app video toggle returned an invalid response"
                )

        async def _executor(function, *args):
            return function(*args)

        entity.coordinator.client = _Client()
        entity.coordinator.diagnostic_events = DreameLawnMowerDiagnosticEventStore()
        entity.hass = SimpleNamespace(
            async_add_executor_job=_executor,
            async_create_task=asyncio.create_task,
            data={},
        )
        entity.async_write_ha_state = lambda: None
        entity.stream = None
        entity._runtime = _Runtime()
        entity._session = SimpleNamespace(
            transport=VIDEO_TRANSPORT_CLOUD,
            camera_toggle_managed=True,
        )
        entity._attr_is_streaming = True
        entity.coordinator.data = SimpleNamespace(
            available=True,
            state="charging",
            activity="charging",
            docked=True,
            raw_docked=True,
            returning=False,
            capabilities=(),
            raw_info={},
            raw_attributes={},
        )

        with patch.object(
            video_camera_module.CoordinatorEntity,
            "_handle_coordinator_update",
            lambda _entity: None,
        ):
            entity._handle_coordinator_update()
            cleanup_task = entity._state_gate_cleanup_task
            assert cleanup_task is not None
            await cleanup_task

            available_while_docked = entity.available
            docked_block_reason = entity.extra_state_attributes[
                "video_block_reason"
            ]
            entity.coordinator.data = SimpleNamespace(
                available=True,
                state="mowing",
                activity="mowing",
                docked=False,
                raw_docked=False,
                returning=False,
                capabilities=(),
                raw_info={},
                raw_attributes={
                    "running": True,
                    "returning": True,
                    "status": "Returning",
                },
            )
            entity._handle_coordinator_update()

        events = entity.coordinator.diagnostic_events.as_list()
        return (
            runtime_stops,
            camera_disables,
            available_while_docked,
            docked_block_reason,
            entity.available,
            [event["code"] for event in events],
            entity._last_stream_cleanup_error_stage,
        )

    assert asyncio.run(_run()) == (
        1,
        1,
        True,
        (
            "Camera stream handshake probe is blocked while the mower is "
            "docked. The Dreame app requires moving the mower out of the "
            "station before remote video monitoring can start."
        ),
        True,
        [
            "video_state_gate_cleanup",
            "video_camera_stream_disable_failed",
        ],
        "camera_stream_disable",
    )


def test_video_camera_state_gate_cleanup_does_not_stop_replacement_session() -> None:
    async def _run() -> int:
        snapshot = SimpleNamespace(
            state="charging",
            activity="charging",
            docked=True,
            raw_docked=True,
            returning=False,
            capabilities=(),
            raw_info={},
            raw_attributes={},
        )
        entity = _uninitialized_entity(snapshot=snapshot)
        old_session = object()
        old_stream = object()
        entity._session = old_session
        entity.stream = old_stream
        entity.hass = SimpleNamespace(async_create_task=asyncio.create_task)
        stops = 0

        async def _stop(**_kwargs) -> None:
            nonlocal stops
            stops += 1

        entity._async_stop_active_session = _stop
        await entity._stream_lock.acquire()
        try:
            with patch.object(
                video_camera_module.CoordinatorEntity,
                "_handle_coordinator_update",
                lambda _entity: None,
            ):
                entity._handle_coordinator_update()
            cleanup_task = entity._state_gate_cleanup_task
            assert cleanup_task is not None
            entity._session = object()
            entity.stream = object()
        finally:
            entity._stream_lock.release()
        await cleanup_task
        return stops

    assert asyncio.run(_run()) == 0


def test_video_camera_rechecks_state_after_cloud_inputs_before_enable() -> None:
    async def _run() -> tuple[str | None, list[bool], int, str | None]:
        snapshot = SimpleNamespace(
            available=True,
            state="mowing",
            activity="mowing",
            docked=False,
            raw_docked=False,
            returning=False,
            capabilities=("video",),
            raw_info={},
            raw_attributes={},
        )
        entity = _uninitialized_entity(snapshot=snapshot)
        runtime = object()
        camera_toggles: list[bool] = []
        runtime_starts = 0

        class _Client:
            last_camera_stream_diagnostics = {}

            async def async_get_camera_stream_runtime_inputs(
                self,
            ) -> DreameLawnMowerCameraStreamRuntimeInputs:
                entity.coordinator.data = SimpleNamespace(
                    available=True,
                    state="charging",
                    activity="charging",
                    docked=True,
                    raw_docked=True,
                    returning=False,
                    capabilities=(),
                    raw_info={},
                    raw_attributes={},
                )
                return DreameLawnMowerCameraStreamRuntimeInputs(
                    source="test",
                    did="device-1",
                    product_id="product-1",
                    device_name="mower-1",
                    p2p_info="p2p-info",
                )

            async def async_set_camera_stream_enabled(self, enabled: bool) -> object:
                camera_toggles.append(enabled)
                return {"enabled": enabled}

        async def _save_lan_identity(_inputs: object) -> None:
            return None

        async def _executor(function, *args):
            return function(*args)

        async def _start_session(*_args, **_kwargs) -> object:
            nonlocal runtime_starts
            runtime_starts += 1
            return object()

        entity.coordinator.client = _Client()
        entity.coordinator.diagnostic_events = DreameLawnMowerDiagnosticEventStore()
        entity.hass = SimpleNamespace(async_add_executor_job=_executor)
        entity._lan_cache = SimpleNamespace(
            inputs=None,
            endpoint=None,
            async_save_identity=_save_lan_identity,
        )
        entity._prepared_runtime = runtime
        entity._async_start_runtime_session = _start_session
        entity.async_write_ha_state = lambda: None

        source = await entity._async_start_stream()
        return source, camera_toggles, runtime_starts, entity._last_error_stage

    assert asyncio.run(_run()) == (None, [], 0, "mower_state_gate")


def test_video_camera_rejects_cloud_session_when_mower_docks_during_start() -> None:
    async def _run() -> tuple[
        str | None,
        int,
        list[bool],
        str | None,
        str | None,
        str | None,
    ]:
        snapshot = SimpleNamespace(
            available=True,
            state="mowing",
            activity="mowing",
            docked=False,
            raw_docked=False,
            returning=False,
            capabilities=("video",),
            raw_info={},
            raw_attributes={},
        )
        entity = _uninitialized_entity(snapshot=snapshot)
        runtime = object()
        session = SimpleNamespace(
            stream_url="http://127.0.0.1/late.flv",
            transport=VIDEO_TRANSPORT_CLOUD,
            camera_toggle_managed=True,
        )
        runtime_stops = 0
        camera_toggles: list[bool] = []

        class _Client:
            last_camera_stream_diagnostics = {}

            async def async_get_camera_stream_runtime_inputs(
                self,
            ) -> DreameLawnMowerCameraStreamRuntimeInputs:
                return DreameLawnMowerCameraStreamRuntimeInputs(
                    source="test",
                    did="device-1",
                    product_id="product-1",
                    device_name="mower-1",
                    p2p_info="p2p-info",
                )

            async def async_set_camera_stream_enabled(self, enabled: bool) -> object:
                camera_toggles.append(enabled)
                return {"enabled": enabled}

        async def _start_session(
            _runtime: object,
            _inputs: DreameLawnMowerCameraStreamRuntimeInputs,
            *,
            camera_toggle_managed: bool = True,
        ) -> object:
            assert camera_toggle_managed is True
            entity.coordinator.data = SimpleNamespace(
                available=True,
                state="charging",
                activity="charging",
                docked=True,
                raw_docked=True,
                returning=False,
                capabilities=(),
                raw_info={},
                raw_attributes={},
            )
            return session

        async def _stop_session(
            actual_runtime: object,
            actual_session: object,
        ) -> bool:
            nonlocal runtime_stops
            assert actual_runtime is runtime
            assert actual_session is session
            runtime_stops += 1
            return True

        async def _save_lan_identity(_inputs: object) -> None:
            return None

        async def _executor(function, *args):
            return function(*args)

        entity.coordinator.client = _Client()
        entity.coordinator.diagnostic_events = DreameLawnMowerDiagnosticEventStore()
        entity.hass = SimpleNamespace(async_add_executor_job=_executor)
        entity._lan_cache = SimpleNamespace(
            inputs=None,
            endpoint=None,
            async_save_identity=_save_lan_identity,
        )
        entity._prepared_runtime = runtime
        entity._async_start_runtime_session = _start_session
        entity._async_stop_session = _stop_session
        entity.async_write_ha_state = lambda: None

        source = await entity._async_start_stream()
        events = entity.coordinator.diagnostic_events.as_list()
        return (
            source,
            runtime_stops,
            camera_toggles,
            entity._last_error_stage,
            events[0]["code"] if events else None,
            entity._last_stream_cleanup_reason,
        )

    assert asyncio.run(_run()) == (
        None,
        1,
        [True, False],
        "mower_state_gate",
        "video_start_state_changed",
        "state_gate",
    )


def test_video_camera_rejected_session_cleanup_survives_cancellation() -> None:
    async def _run() -> tuple[bool, int]:
        snapshot = SimpleNamespace(
            state="charging",
            activity="charging",
            docked=True,
            raw_docked=True,
            returning=False,
            capabilities=(),
            raw_info={},
            raw_attributes={},
        )
        entity = _uninitialized_entity(snapshot=snapshot)
        entity.coordinator.diagnostic_events = DreameLawnMowerDiagnosticEventStore()
        entity.async_write_ha_state = lambda: None
        stop_started = asyncio.Event()
        release_stop = asyncio.Event()
        camera_disables = 0

        async def _stop_session(_runtime: object, _session: object) -> bool:
            stop_started.set()
            await release_stop.wait()
            return True

        async def _disable() -> None:
            nonlocal camera_disables
            camera_disables += 1

        entity._async_stop_session = _stop_session
        entity._async_disable_camera_stream = _disable
        task = asyncio.create_task(
            entity._async_adopt_stream_session(
                object(),
                SimpleNamespace(
                    transport=VIDEO_TRANSPORT_CLOUD,
                    camera_toggle_managed=True,
                ),
                None,
                transport=VIDEO_TRANSPORT_CLOUD,
            )
        )
        await stop_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        release_stop.set()
        cancelled = False
        try:
            await task
        except asyncio.CancelledError:
            cancelled = True
        return cancelled, camera_disables

    assert asyncio.run(_run()) == (True, 1)


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


def test_managed_runtime_retries_after_transient_probe_failure(
    monkeypatch,
    tmp_path,
) -> None:
    entity = _uninitialized_entity()
    entity._entry.options.pop(CONF_XP2P_RUNNER_COMMAND)
    entity.hass = SimpleNamespace(
        config=SimpleNamespace(path=lambda *parts: str(tmp_path.joinpath(*parts)))
    )
    failed_assets = DreameLawnMowerXp2pHostAssets(
        worker_path=tmp_path / "worker",
        linker_path=tmp_path / "linker64",
        library_path=tmp_path / "libiot_video_demo.so",
        library_search_paths=(tmp_path,),
        startup_probe={
            "ready": False,
            "stage": "response_wait",
            "exception": "TimeoutExpired",
        },
    )
    ready_assets = DreameLawnMowerXp2pHostAssets(
        worker_path=tmp_path / "worker",
        linker_path=tmp_path / "linker64",
        library_path=tmp_path / "libiot_video_demo.so",
        library_search_paths=(tmp_path,),
        startup_probe={
            "ready": True,
            "returncode": 1,
            "exit": "exit_code=1",
            "response_status": 1,
        },
    )
    prepared = iter((failed_assets, ready_assets))
    monkeypatch.setattr(
        video_helpers_module,
        "managed_runtime_supported",
        lambda: True,
    )
    monkeypatch.setattr(
        video_camera_module,
        "ensure_xp2p_host_runtime",
        lambda _root: next(prepared),
    )

    with pytest.raises(
        DreameLawnMowerVideoRuntimeError,
        match="compatibility probe failed",
    ):
        entity._create_runtime()

    assert entity._prepared_runtime is None
    assert entity._last_managed_runtime_diagnostics == {
        "stage": "runtime_probe",
        "startup_probe": {
            "ready": False,
            "stage": "response_wait",
            "exception": "TimeoutExpired",
        },
    }

    runtime = entity._create_runtime()

    assert runtime is entity._prepared_runtime
    assert runtime.assets is ready_assets
    assert entity._last_managed_runtime_diagnostics is None


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
            entity._async_start_raw_source(),
            entity._async_start_raw_source(),
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
        entity._set_stream_error = lambda _error, **_kwargs: None
        return await entity._async_start_raw_source(), starts

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
        return await entity._async_start_raw_source(), stops, starts

    source, stops, starts = asyncio.run(_run())

    assert source == "http://127.0.0.1/fresh.flv"
    assert stops == 1
    assert starts == 1


def test_video_camera_direct_stream_source_returns_dormant_local_relay() -> None:
    async def _run() -> tuple[str | None, int]:
        entity = _uninitialized_entity()
        entity.stream = None
        starts = 0

        class _Relay:
            async def async_start(self) -> str:
                nonlocal starts
                starts += 1
                return "http://127.0.0.1:12345/private.flv"

        entity._flv_relay = _Relay()
        source = await entity.stream_source()
        return source, starts

    assert asyncio.run(_run()) == (
        "http://127.0.0.1:12345/private.flv",
        1,
    )


def test_video_camera_direct_stream_source_failure_does_not_touch_ha_stream() -> None:
    async def _run() -> tuple[str | None, int, bool]:
        entity = _uninitialized_entity()
        entity.async_write_ha_state = lambda: None
        stops = 0
        stream = object()
        entity.stream = stream

        async def _stop() -> None:
            nonlocal stops
            stops += 1
            entity.stream = None

        entity._async_stop_active_session = _stop

        class _Relay:
            @staticmethod
            async def async_start() -> str:
                raise RuntimeError("loopback bind failed")

        entity._flv_relay = _Relay()
        source = await entity.stream_source()
        return source, stops, entity.stream is stream

    assert asyncio.run(_run()) == (None, 0, True)


def test_video_camera_creates_home_assistant_stream_from_live_source() -> None:
    async def _run() -> tuple[object, object]:
        entity = _uninitialized_entity()
        entity._create_stream_lock = None
        entity.stream = None
        entity.stream_options = {"use_wallclock_as_timestamps": True}
        entity.entity_id = "camera.dreame_live_video"
        entity.async_write_ha_state = lambda: None
        class _Relay:
            @staticmethod
            async def async_start_ha_stream() -> str:
                return "http://127.0.0.1:12345/ha-owned.flv"

        entity._flv_relay = _Relay()

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
    assert call.args[1] == "http://127.0.0.1:12345/ha-owned.flv"
    assert call.kwargs["stream_label"] == "camera.dreame_live_video"


def test_video_camera_marks_relay_media_ready_without_waiting_for_hls() -> None:
    async def _run() -> tuple[dict[str, object], bool, bool]:
        entity = _uninitialized_entity()
        entity.async_write_ha_state = lambda: None
        provisioning = object()
        entity._pending_provisioning_inputs = provisioning
        entity._unverified_playback_session = object()
        entity._last_video_transport = VIDEO_TRANSPORT_LAN
        entity._bypass_lan = True
        cached: list[object] = []

        async def _cache(inputs: object) -> None:
            cached.append(inputs)

        entity._async_cache_healthy_provisioning = _cache
        await entity._async_relay_media_ready(
            {
                "flv_header_present": True,
                "video_width": 640,
                "video_height": 360,
                "video_observed_fps": 15.0,
            }
        )
        return (
            entity._last_stream_health,
            cached == [provisioning],
            entity._bypass_lan,
        )

    health, cached, bypass_lan = asyncio.run(_run())

    assert health["playback_session_verified"] is True
    assert health["verification_source"] == "local_flv_relay"
    assert health["video_width"] == 640
    assert cached is True
    assert bypass_lan is False


def test_video_camera_relay_failure_retires_active_session() -> None:
    async def _run() -> tuple[list[str], str | None]:
        entity = _uninitialized_entity()
        entity.async_write_ha_state = lambda: None
        entity._session = object()
        reasons: list[str] = []

        async def _stop(*, reason: str = "session_stop", **_kwargs: object) -> None:
            reasons.append(reason)
            entity._session = None

        entity._async_stop_active_session = _stop
        await entity._async_relay_failed("invalid FLV")
        return reasons, entity._last_error_stage

    assert asyncio.run(_run()) == (["relay_failure"], "relay_playback")


def test_video_camera_cancelled_relay_callback_finishes_runtime_cleanup() -> None:
    async def _run() -> tuple[int, int, bool, bool]:
        entity = _uninitialized_entity()
        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        runtime_stops = 0
        video_disables = 0

        class _Stream:
            @staticmethod
            def outputs() -> dict[str, object]:
                return {"hls": object()}

        runtime = object()

        class _Client:
            async def async_set_camera_stream_enabled(self, enabled: bool) -> None:
                nonlocal video_disables
                assert enabled is False
                video_disables += 1

        async def _stop_session(
            actual_runtime: object,
            _session: object,
        ) -> bool:
            nonlocal runtime_stops
            assert actual_runtime is runtime
            cleanup_started.set()
            await release_cleanup.wait()
            runtime_stops += 1
            return True

        stream = _Stream()
        entity.stream = stream
        entity._runtime = runtime
        entity._session = SimpleNamespace(
            transport=VIDEO_TRANSPORT_CLOUD,
            camera_toggle_managed=True,
        )
        entity._attr_is_streaming = True
        entity.coordinator.client = _Client()
        entity._async_stop_session = _stop_session
        entity.async_write_ha_state = lambda: None

        callback = asyncio.create_task(entity._async_relay_failed("source failed"))
        await asyncio.wait_for(cleanup_started.wait(), timeout=1)
        callback.cancel()
        await asyncio.sleep(0)
        release_cleanup.set()
        with suppress(asyncio.CancelledError):
            await asyncio.wait_for(callback, timeout=1)
        return (
            runtime_stops,
            video_disables,
            callback.cancelled(),
            entity._runtime is None
            and entity._session is None
            and entity.stream is stream,
        )

    assert asyncio.run(_run()) == (1, 1, True, True)


def test_video_camera_relay_failure_preserves_ha_stream_without_session() -> None:
    async def _run() -> tuple[list[str], bool, bool]:
        entity = _uninitialized_entity()
        entity.async_write_ha_state = lambda: None
        entity._session = None
        stream = object()
        entity.stream = stream
        reasons: list[str] = []

        async def _stop(*, reason: str = "session_stop", **_kwargs: object) -> None:
            reasons.append(reason)
            entity.stream = None

        entity._async_stop_active_session = _stop
        await entity._async_relay_failed("source unavailable")
        return reasons, entity.stream is stream, entity._video_recovery_pending

    assert asyncio.run(_run()) == ([], True, True)


def test_video_camera_safety_block_does_not_accumulate_recovery_backoff() -> None:
    async def _run() -> tuple[bool, int, float, str | None]:
        entity = _uninitialized_entity(
            snapshot=SimpleNamespace(
                available=True,
                state="charging",
                activity="charging",
                docked=True,
                raw_docked=True,
                returning=False,
                raw_attributes={},
            )
        )
        entity._video_recovery_pending = True
        entity._video_recovery_failure_count = 4
        entity._video_retry_not_before = 999.0
        entity.async_write_ha_state = lambda: None

        await entity._async_relay_failed("The mower video source did not start.")
        return (
            entity._video_recovery_pending,
            entity._video_recovery_failure_count,
            entity._video_retry_not_before,
            entity._last_error_stage,
        )

    assert asyncio.run(_run()) == (False, 4, 0.0, "mower_state_gate")


def test_video_camera_recovery_backoff_is_bounded_and_resets_after_stability() -> None:
    async def _run() -> tuple[list[float], int, int, bool, str | None]:
        entity = _uninitialized_entity()
        entity.async_write_ha_state = lambda: None
        retry_deadlines: list[float] = []

        for now in (100.0, 101.0, 103.0, 107.0, 115.0, 131.0, 161.0, 191.0):
            with patch.object(video_camera_module, "monotonic", return_value=now):
                await entity._async_relay_failed("network dropped")
            retry_deadlines.append(entity._video_retry_not_before - now)

        with patch.object(video_camera_module, "monotonic", return_value=200.0):
            await entity._async_relay_media_ready({})
        entity._video_first_media_at = 200.0
        with patch.object(video_camera_module, "monotonic", return_value=261.0):
            await entity._async_relay_failed("network dropped after stable media")
        retry_deadlines.append(entity._video_retry_not_before - 261.0)
        return (
            retry_deadlines,
            entity._video_recovery_failure_count,
            entity._video_recovery_success_count,
            entity._video_recovery_pending,
            entity._last_error,
        )

    deadlines, failures, successes, pending, last_error = asyncio.run(_run())

    assert deadlines == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0, 1.0]
    assert failures == 9
    assert successes == 1
    assert pending is True
    assert last_error == (
        "The mower video connection was interrupted: "
        "network dropped after stable media"
    )


def test_video_camera_stays_available_during_active_stream_connectivity_loss() -> None:
    entity = _uninitialized_entity(snapshot=SimpleNamespace(available=False))
    entity.stream = object()

    assert entity.available is True


def test_video_camera_relay_failure_bypasses_stale_cached_xp2p() -> None:
    async def _run() -> tuple[int, list[bool], bool]:
        entity = _uninitialized_entity()
        entity.async_write_ha_state = lambda: None
        entity._last_video_transport = "cached_xp2p"
        entity._unverified_playback_session = object()
        entity._session = entity._unverified_playback_session
        entity.stream = None
        clears = 0
        skip_values: list[bool] = []

        class _ProvisioningCache:
            async def async_clear(self) -> None:
                nonlocal clears
                clears += 1

        async def _stop(*, reason: str = "session_stop", **_kwargs: object) -> None:
            assert reason == "relay_failure"
            entity._session = None
            entity._unverified_playback_session = None

        async def _start(*, skip_cached_xp2p: bool = False) -> str:
            skip_values.append(skip_cached_xp2p)
            return "http://127.0.0.1/fresh.flv"

        entity._provisioning_cache = _ProvisioningCache()
        entity._async_stop_active_session = _stop
        entity._async_start_raw_source = _start

        await entity._async_relay_failed("cached media was invalid")
        await entity._async_start_relay_upstream()
        return clears, skip_values, entity._bypass_cached_xp2p

    assert asyncio.run(_run()) == (1, [True], True)


def test_video_camera_relay_failure_clears_cache_before_reconnect_start() -> None:
    async def _run() -> tuple[list[str], bool, str | None]:
        entity = _uninitialized_entity()
        entity.async_write_ha_state = lambda: None
        entity._last_video_transport = "cached_xp2p"
        entity._unverified_playback_session = object()
        entity._session = entity._unverified_playback_session
        entity.stream = None
        clear_started = asyncio.Event()
        release_clear = asyncio.Event()
        order: list[str] = []

        class _ProvisioningCache:
            async def async_clear(self) -> None:
                order.append("clear_started")
                clear_started.set()
                await release_clear.wait()
                order.append("clear_finished")

        async def _stop(*, reason: str = "session_stop", **_kwargs: object) -> None:
            assert reason == "relay_failure"
            entity._session = None
            entity._unverified_playback_session = None

        async def _start(*, skip_cached_xp2p: bool = False) -> str:
            assert skip_cached_xp2p is True
            order.append("replacement_started")
            return "http://127.0.0.1/fresh.flv"

        entity._provisioning_cache = _ProvisioningCache()
        entity._async_stop_active_session = _stop
        entity._async_start_stream = _start

        failure = asyncio.create_task(
            entity._async_relay_failed("cached media was invalid")
        )
        await asyncio.wait_for(clear_started.wait(), timeout=1)
        replacement = asyncio.create_task(
            entity._async_start_raw_source(skip_cached_xp2p=True)
        )
        await asyncio.sleep(0)
        replacement_was_blocked = "replacement_started" not in order

        release_clear.set()
        await failure
        source = await replacement
        return order, replacement_was_blocked, source

    assert asyncio.run(_run()) == (
        ["clear_started", "clear_finished", "replacement_started"],
        True,
        "http://127.0.0.1/fresh.flv",
    )


def test_video_camera_relay_failure_bypasses_stale_lan_for_auto() -> None:
    async def _run() -> tuple[int, int, bool, str | None, str | None]:
        entity = _uninitialized_entity()
        entity._entry.options[CONF_VIDEO_TRANSPORT] = VIDEO_TRANSPORT_AUTO
        entity.async_write_ha_state = lambda: None
        entity._last_video_transport = VIDEO_TRANSPORT_LAN
        entity._unverified_playback_session = object()
        entity._session = entity._unverified_playback_session
        entity.stream = None
        lan_attempts = 0

        class _LanCache:
            inputs = object()
            endpoint = object()

            def __init__(self) -> None:
                self.clears = 0

            async def async_clear_endpoint(self) -> None:
                self.clears += 1
                self.endpoint = None

        async def _stop(*, reason: str = "session_stop", **_kwargs: object) -> None:
            assert reason == "relay_failure"
            entity._session = None
            entity._unverified_playback_session = None

        async def _try_lan(_runtime: object, _inputs: object) -> str | None:
            nonlocal lan_attempts
            lan_attempts += 1
            raise AssertionError("Auto must bypass the failed LAN route")

        runtime = object()
        cloud_inputs = DreameLawnMowerCameraStreamRuntimeInputs(
            source="dreame_third_video_tx",
            did="did-1",
            product_id="product-1",
            device_name="device-1",
            p2p_info="p2p-info-1",
        )
        cloud_session = object()

        class _Client:
            async def async_set_camera_stream_enabled(self, enabled: bool) -> object:
                assert enabled is True
                return {"enabled": True}

        async def _get_runtime() -> object:
            return runtime

        async def _get_inputs() -> DreameLawnMowerCameraStreamRuntimeInputs:
            return cloud_inputs

        async def _start_cloud(
            actual_runtime: object,
            actual_inputs: object,
        ) -> object:
            assert actual_runtime is runtime
            assert actual_inputs is cloud_inputs
            return cloud_session

        async def _adopt(
            actual_runtime: object,
            actual_session: object,
            _health: object,
            *,
            transport: str,
            provisioning_inputs: object,
        ) -> str:
            assert actual_runtime is runtime
            assert actual_session is cloud_session
            assert transport == VIDEO_TRANSPORT_CLOUD
            assert provisioning_inputs is cloud_inputs
            return "http://127.0.0.1/cloud.flv"

        lan_cache = _LanCache()
        entity._lan_cache = lan_cache
        entity._async_stop_active_session = _stop
        entity._async_try_lan_stream = _try_lan
        entity._async_get_runtime = _get_runtime
        entity._async_get_runtime_inputs = _get_inputs
        entity._async_start_runtime_session = _start_cloud
        entity._async_adopt_stream_session = _adopt
        entity._async_refresh_video_start_state = lambda: asyncio.sleep(
            0,
            result=True,
        )
        entity.coordinator.client = _Client()

        await entity._async_relay_failed("LAN media never reached a keyframe")
        source = await entity._async_start_stream()
        return (
            lan_cache.clears,
            lan_attempts,
            entity._bypass_lan,
            source,
            entity._last_video_transport_attempted,
        )

    assert asyncio.run(_run()) == (
        1,
        0,
        True,
        "http://127.0.0.1/cloud.flv",
        VIDEO_TRANSPORT_CLOUD,
    )


def test_video_camera_preserves_ha_stream_across_upstream_worker_exit() -> None:
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

        class _Preferences:
            async def get_dynamic_stream_settings(self, _entity_id: str) -> object:
                return object()

        fresh_stream = SimpleNamespace(set_update_callback=lambda _callback: None)
        entity._async_stop_active_session = _stop
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
    assert stops == 0


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

        async def _stop(**_kwargs) -> None:
            nonlocal stops
            stops += 1
            entity.stream = None
            entity._session = None

        class _Preferences:
            async def get_dynamic_stream_settings(self, _entity_id: str) -> object:
                preferences_started.set()
                await release_preferences.wait()
                return object()

        entity._flv_relay = SimpleNamespace(async_start_ha_stream=_source)
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


def test_video_camera_cancellation_before_consumer_never_starts_session() -> None:
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

        entity._flv_relay = SimpleNamespace(async_start_ha_stream=_source)
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

    assert stops == 0
    assert session is None


def test_video_camera_returns_jpeg_from_home_assistant_stream() -> None:
    async def _run() -> tuple[bytes | None, dict[str, object]]:
        entity = _uninitialized_entity()
        entity._last_image = None
        call: dict[str, object] = {}

        class _Stream:
            async def async_get_image(self, **kwargs) -> bytes:
                call.update(kwargs)
                return b"\xff\xd8real-jpeg\xff\xd9"

        stream = _Stream()
        entity.stream = stream

        async def _create_stream() -> _Stream:
            return stream

        entity._async_create_stream_locked = _create_stream
        image = await entity.async_camera_image(width=640, height=360)
        return image, call

    image, call = asyncio.run(_run())

    assert image == b"\xff\xd8real-jpeg\xff\xd9"
    assert call == {
        "width": 640,
        "height": 360,
        "wait_for_next_keyframe": True,
    }


def test_video_camera_stops_stream_created_only_for_snapshot() -> None:
    async def _run() -> tuple[bytes | None, int]:
        entity = _uninitialized_entity()
        entity._last_image = None
        stops = 0

        class _Stream:
            @staticmethod
            def outputs() -> dict[str, object]:
                return {"hls": object()}

            async def async_get_image(self, **_kwargs) -> bytes:
                return b"\xff\xd8snapshot-jpeg\xff\xd9"

            async def stop(self) -> None:
                nonlocal stops
                stops += 1

        stream = _Stream()

        async def _create_stream() -> _Stream:
            entity.stream = stream
            return stream

        entity._async_create_stream_locked = _create_stream
        image = await entity.async_camera_image()
        return image, stops

    assert asyncio.run(_run()) == (b"\xff\xd8snapshot-jpeg\xff\xd9", 1)


def test_video_camera_viewer_adopts_stream_while_snapshot_waits() -> None:
    async def _run() -> tuple[bytes | None, bool, int, int]:
        entity = _uninitialized_entity()
        entity._last_image = None
        entity._create_stream_lock = None
        stops = 0
        creates = 0
        image_started = asyncio.Event()
        release_image = asyncio.Event()

        class _SnapshotStream:
            async def async_get_image(self, **_kwargs: object) -> bytes:
                image_started.set()
                await release_image.wait()
                return b"\xff\xd8snapshot-jpeg\xff\xd9"

            async def stop(self) -> None:
                nonlocal stops
                stops += 1

        snapshot_stream = _SnapshotStream()

        async def _create_stream() -> object:
            nonlocal creates
            existing = getattr(entity, "stream", None)
            if existing is not None:
                return existing
            creates += 1
            entity.stream = snapshot_stream
            return snapshot_stream

        entity._async_create_stream_locked = _create_stream
        snapshot_task = asyncio.create_task(entity.async_camera_image())
        await image_started.wait()
        viewer_task = asyncio.create_task(entity.async_create_stream())
        viewer = await asyncio.wait_for(viewer_task, timeout=1)
        release_image.set()
        image = await asyncio.wait_for(snapshot_task, timeout=1)
        return image, viewer is snapshot_stream, stops, creates

    assert asyncio.run(_run()) == (
        b"\xff\xd8snapshot-jpeg\xff\xd9",
        True,
        0,
        1,
    )


def test_video_camera_snapshot_start_timeout_returns_last_image() -> None:
    async def _run() -> tuple[bytes | None, bool]:
        entity = _uninitialized_entity()
        entity._last_image = b"\xff\xd8cached-jpeg\xff\xd9"
        cancelled = False

        async def _create_stream() -> object:
            nonlocal cancelled
            try:
                await asyncio.Future()
            finally:
                cancelled = True
            raise AssertionError("unreachable")

        entity._async_create_stream_locked = _create_stream
        with patch.object(
            video_camera_module,
            "_SNAPSHOT_STREAM_START_TIMEOUT",
            0.01,
        ):
            image = await entity.async_camera_image()
        return image, cancelled

    assert asyncio.run(_run()) == (b"\xff\xd8cached-jpeg\xff\xd9", True)


def test_video_camera_snapshot_image_timeout_returns_last_image() -> None:
    async def _run() -> tuple[bytes | None, bool, int]:
        entity = _uninitialized_entity()
        entity._last_image = b"\xff\xd8cached-jpeg\xff\xd9"
        entity._flv_relay = SimpleNamespace(
            diagnostics={"relay_first_media_ready": True}
        )
        cancelled = False
        stops = 0

        class _Stream:
            async def async_get_image(self, **_kwargs) -> bytes:
                nonlocal cancelled
                try:
                    await asyncio.Future()
                finally:
                    cancelled = True
                raise AssertionError("unreachable")

            async def stop(self) -> None:
                nonlocal stops
                stops += 1

        stream = _Stream()

        async def _create_stream() -> _Stream:
            entity.stream = stream
            return stream

        entity._async_create_stream_locked = _create_stream
        with patch.object(video_camera_module, "_SNAPSHOT_IMAGE_TIMEOUT", 0.01):
            image = await entity.async_camera_image()
        return image, cancelled, stops

    assert asyncio.run(_run()) == (b"\xff\xd8cached-jpeg\xff\xd9", True, 1)


def test_video_camera_cold_snapshot_includes_upstream_startup_budget() -> None:
    async def _run() -> bytes | None:
        entity = _uninitialized_entity()
        entity._last_image = None
        entity._flv_relay = SimpleNamespace(
            diagnostics={"relay_first_media_ready": False}
        )

        class _Stream:
            async def async_get_image(self, **_kwargs) -> bytes:
                await asyncio.sleep(0.02)
                return b"\xff\xd8cold-snapshot\xff\xd9"

            async def stop(self) -> None:
                return None

        stream = _Stream()

        async def _create_stream() -> _Stream:
            entity.stream = stream
            return stream

        entity._async_create_stream_locked = _create_stream
        with (
            patch.object(video_camera_module, "_SNAPSHOT_IMAGE_TIMEOUT", 0.01),
            patch.object(video_camera_module, "_VIDEO_UPSTREAM_START_TIMEOUT", 0.03),
        ):
            return await entity.async_camera_image()

    assert asyncio.run(_run()) == b"\xff\xd8cold-snapshot\xff\xd9"


def test_failed_relay_cleanup_preserves_replacement_during_snapshot_stop() -> None:
    async def _run() -> tuple[bool, bool, bool]:
        entity = _uninitialized_entity()
        stop_entered = asyncio.Event()
        release_stop = asyncio.Event()
        replacement_release = asyncio.Event()

        class _Stream:
            async def stop(self) -> None:
                stop_entered.set()
                await release_stop.wait()

        class _Relay:
            def __init__(self) -> None:
                self._pump_task: asyncio.Task[None] | None = None
                self.preserved_replacement = False

            async def async_stop_upstream(
                self,
                *,
                expected_task: asyncio.Task[None] | None = None,
            ) -> None:
                if (
                    expected_task is not None
                    and self._pump_task is not None
                    and self._pump_task is not expected_task
                ):
                    self.preserved_replacement = True
                    return
                raise AssertionError("Old cleanup must not stop the replacement pump")

        stream = _Stream()
        relay = _Relay()
        entity.stream = stream
        entity._snapshot_owned_stream = stream
        entity._flv_relay = relay
        entity._runtime = object()
        entity._session = SimpleNamespace(transport=VIDEO_TRANSPORT_LAN)
        entity._last_stream_health = {}
        entity.hass = SimpleNamespace(
            data={
                video_camera_module.STREAM_DOMAIN: {
                    video_camera_module.ATTR_STREAMS: [stream],
                }
            }
        )
        entity.async_write_ha_state = lambda: None
        entity._async_stop_session = lambda *_args: asyncio.sleep(0)
        entity._async_clear_failed_playback_caches = (
            lambda **_kwargs: asyncio.sleep(0)
        )

        snapshot_stop = asyncio.create_task(entity._async_stop_owned_stream(stream))
        await asyncio.wait_for(stop_entered.wait(), timeout=1)
        failed_cleanup = asyncio.create_task(entity._async_relay_failed("link lost"))
        await asyncio.sleep(0)
        replacement = asyncio.create_task(replacement_release.wait())
        relay._pump_task = replacement
        release_stop.set()
        await snapshot_stop
        await failed_cleanup
        preserved = relay.preserved_replacement and not replacement.done()
        replacement_release.set()
        await replacement
        return preserved, entity._runtime is None, entity._session is None

    assert asyncio.run(_run()) == (True, True, True)


def test_video_camera_stop_unregisters_cached_home_assistant_stream() -> None:
    async def _run() -> tuple[object | None, int, list[object]]:
        entity = _uninitialized_entity()
        entity._attr_is_streaming = True
        stopped = 0

        class _Stream:
            async def stop(self) -> None:
                nonlocal stopped
                stopped += 1

        stream = _Stream()
        registry = [stream]
        entity.stream = stream
        entity.hass = SimpleNamespace(
            data={
                video_camera_module.STREAM_DOMAIN: {
                    video_camera_module.ATTR_STREAMS: registry,
                }
            }
        )
        entity.async_write_ha_state = lambda: None
        await entity._async_stop_active_session()
        return entity.stream, stopped, registry

    stream, stopped, registry = asyncio.run(_run())

    assert stream is None
    assert stopped == 1
    assert registry == []


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

        stream = _Stream()
        registry = [stream]
        entity.stream = stream
        entity.hass = SimpleNamespace(
            data={
                video_camera_module.STREAM_DOMAIN: {
                    video_camera_module.ATTR_STREAMS: registry,
                }
            }
        )
        entity._async_stop_session = _stop_session
        entity._async_disable_camera_stream = _disable
        entity.async_write_ha_state = lambda: None
        await entity._async_stop_active_session()
        assert registry == []
        return runtime_stops, video_disables

    assert asyncio.run(_run()) == (1, 1)


def test_video_camera_unload_is_bounded_when_every_cleanup_stage_hangs() -> None:
    async def _run() -> tuple[bool, list[str], str | None, bool]:
        entity = _uninitialized_entity()
        entity._descriptor = SimpleNamespace(model="dreame.mower.q2501a")
        entity.coordinator.diagnostic_events = DreameLawnMowerDiagnosticEventStore()
        base_remove_called = False

        class _Stream:
            async def stop(self) -> None:
                await asyncio.get_running_loop().create_future()

        class _Runtime:
            def stop_live_stream(self, _session: object) -> None:
                raise AssertionError("The synthetic executor must stay pending")

        class _Client:
            async def async_set_camera_stream_enabled(self, _enabled: bool) -> None:
                await asyncio.get_running_loop().create_future()

        async def _base_remove(_entity: object) -> None:
            nonlocal base_remove_called
            base_remove_called = True

        def _pending_executor(*_args):
            return asyncio.get_running_loop().create_future()

        stream = _Stream()
        entity.stream = stream
        entity._runtime = _Runtime()
        entity._session = SimpleNamespace(
            transport=VIDEO_TRANSPORT_CLOUD,
            camera_toggle_managed=True,
        )
        entity._attr_is_streaming = True
        entity.coordinator.client = _Client()
        entity.hass = SimpleNamespace(
            async_add_executor_job=_pending_executor,
            data={
                video_camera_module.STREAM_DOMAIN: {
                    video_camera_module.ATTR_STREAMS: [stream],
                }
            },
        )
        entity.async_write_ha_state = lambda: None

        with (
            patch.object(video_camera_module, "_HA_STREAM_STOP_TIMEOUT", 0.001),
            patch.object(video_camera_module, "_RUNTIME_SESSION_STOP_TIMEOUT", 0.001),
            patch.object(video_camera_module, "_CAMERA_STREAM_DISABLE_TIMEOUT", 0.001),
            patch.object(
                video_camera_module.CoordinatorEntity,
                "async_will_remove_from_hass",
                new=_base_remove,
            ),
        ):
            await entity.async_will_remove_from_hass()

        events = entity.coordinator.diagnostic_events.as_list()
        return (
            base_remove_called,
            [event["code"] for event in events],
            entity._last_stream_cleanup_error_stage,
            entity._session is None and entity.stream is None,
        )

    assert asyncio.run(_run()) == (
        True,
        [
            "video_home_assistant_stream_stop_failed",
            "video_runtime_session_stop_failed",
            "video_camera_stream_disable_failed",
        ],
        "camera_stream_disable",
        True,
    )


def test_video_camera_unload_closes_relay_before_waiting_for_startup_lock() -> None:
    async def _run() -> tuple[bool, bool, bool]:
        entity = _uninitialized_entity()
        startup_entered = asyncio.Event()
        state_gate_started = asyncio.Event()
        state_gate_finished = asyncio.Event()
        relay_closed = False
        base_remove_called = False

        async def _cold_start() -> None:
            async with entity._stream_lock:
                startup_entered.set()
                await asyncio.get_running_loop().create_future()

        startup_task = asyncio.create_task(_cold_start())
        await asyncio.wait_for(startup_entered.wait(), timeout=1)

        async def _state_gate_cleanup() -> None:
            state_gate_started.set()
            async with entity._stream_lock:
                state_gate_finished.set()

        state_gate_task = asyncio.create_task(_state_gate_cleanup())
        entity._state_gate_cleanup_task = state_gate_task
        await asyncio.wait_for(state_gate_started.wait(), timeout=1)

        class _Relay:
            async def async_close(self) -> None:
                nonlocal relay_closed
                relay_closed = True
                startup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await startup_task

        async def _base_remove(_entity: object) -> None:
            nonlocal base_remove_called
            base_remove_called = True

        entity._flv_relay = _Relay()
        try:
            with patch.object(
                video_camera_module.CoordinatorEntity,
                "async_will_remove_from_hass",
                new=_base_remove,
            ):
                await asyncio.wait_for(entity.async_will_remove_from_hass(), timeout=1)
        finally:
            if not startup_task.done():
                startup_task.cancel()
                with suppress(asyncio.CancelledError):
                    await startup_task
            if not state_gate_task.done():
                state_gate_task.cancel()
                with suppress(asyncio.CancelledError):
                    await state_gate_task
        return relay_closed, state_gate_finished.is_set(), base_remove_called

    assert asyncio.run(_run()) == (True, True, True)


def test_video_camera_fences_restart_until_timed_out_runtime_stop_finishes() -> None:
    async def _run() -> tuple[bool, str | None, bool]:
        shared_did = "shared-timeout-device"
        pending_stop = asyncio.get_running_loop().create_future()
        first = _uninitialized_entity()
        first._descriptor = SimpleNamespace(
            did=shared_did,
            unique_id=shared_did,
            model="dreame.mower.q2501a",
        )
        first.coordinator.diagnostic_events = DreameLawnMowerDiagnosticEventStore()
        first.hass = SimpleNamespace(
            async_add_executor_job=lambda *_args: pending_stop,
        )

        class _Runtime:
            @staticmethod
            def stop_live_stream(_session: object) -> None:
                return None

        with patch.object(
            video_camera_module,
            "_RUNTIME_SESSION_STOP_TIMEOUT",
            0.001,
        ):
            stopped = await first._async_stop_session(_Runtime(), object())

        second = _uninitialized_entity()
        second._descriptor = first._descriptor
        second.coordinator.diagnostic_events = DreameLawnMowerDiagnosticEventStore()
        second.async_write_ha_state = lambda: None
        source = await second._async_start_stream()
        assert source is None
        error_stage = second._last_error_stage

        pending_stop.set_result(None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return stopped, error_stage, second._runtime_cleanup_pending

    assert asyncio.run(_run()) == (False, "runtime_cleanup", False)


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


def test_video_camera_exposes_managed_runtime_failure_context() -> None:
    async def _run() -> dict[str, object] | None:
        entity = _uninitialized_entity()
        start_job = asyncio.get_running_loop().create_future()
        start_job.set_exception(
            DreameLawnMowerVideoRuntimeError("managed worker failed")
        )
        entity.hass = SimpleNamespace(
            async_add_executor_job=lambda *_args: start_job,
        )
        runtime = SimpleNamespace(
            start_live_stream=lambda _inputs: None,
            last_failure={
                "stage": "response_wait",
                "exception": "DreameLawnMowerVideoRuntimeError",
                "returncode": -11,
                "exit": "signal=11",
                "native_trace": "xp2p-worker: runtime loaded",
            }
        )

        with pytest.raises(
            DreameLawnMowerVideoRuntimeError,
            match="managed worker failed",
        ):
            await entity._async_start_runtime_session(runtime, object())
        return entity._last_managed_runtime_diagnostics

    assert asyncio.run(_run()) == {
        "stage": "response_wait",
        "exception": "DreameLawnMowerVideoRuntimeError",
        "returncode": -11,
        "exit": "signal=11",
        "native_trace": "xp2p-worker: runtime loaded",
    }


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


def test_video_camera_idle_monitor_ignores_ha_owned_relay_subscriber() -> None:
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
        relay = SimpleNamespace(
            subscriber_count=1,
            direct_subscriber_count=0,
        )
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
            has_external_consumers=lambda: relay.direct_subscriber_count > 0,
            idle_grace=0,
            poll_interval=0,
        )
        entity._stream_idle_monitor = monitor
        monitor.schedule(stream, session)
        while stops == 0:
            await asyncio.sleep(0)
        return stops

    assert asyncio.run(_run()) == 1


def test_video_camera_idle_monitor_keeps_direct_relay_viewer_alive() -> None:
    async def _run() -> int:
        entity = _uninitialized_entity()
        session = SimpleNamespace(service_id="product-1/device-1")
        direct_viewer = True
        stops = 0

        class _Stream:
            @staticmethod
            def outputs() -> dict[str, object]:
                return {}

        stream = _Stream()
        entity.stream = stream
        entity._session = session

        async def _stop() -> None:
            nonlocal stops
            stops += 1
            entity.stream = None
            entity._session = None

        monitor = DreameLawnMowerHaStreamIdleMonitor(
            SimpleNamespace(async_create_task=asyncio.create_task),
            stream_lock=entity._stream_lock,
            is_current=lambda actual_stream, actual_session: (
                entity.stream is actual_stream and entity._session is actual_session
            ),
            stop_active=_stop,
            has_external_consumers=lambda: direct_viewer,
            provider_grace=0,
            idle_grace=0,
            poll_interval=0,
        )
        monitor.schedule(stream, session)
        for _attempt in range(10):
            await asyncio.sleep(0)
        assert stops == 0

        direct_viewer = False
        while stops == 0:
            await asyncio.sleep(0)
        await monitor.async_cancel()
        return stops

    assert asyncio.run(_run()) == 1


def test_video_camera_idle_monitor_keeps_snapshot_request_alive() -> None:
    async def _run() -> int:
        entity = _uninitialized_entity()
        session = SimpleNamespace(service_id="product-1/device-1")
        entity._snapshot_requests = 1
        stops = 0

        class _Stream:
            @staticmethod
            def outputs() -> dict[str, object]:
                return {}

        stream = _Stream()
        entity.stream = stream
        entity._session = session

        async def _stop() -> None:
            nonlocal stops
            stops += 1
            entity.stream = None
            entity._session = None

        monitor = DreameLawnMowerHaStreamIdleMonitor(
            SimpleNamespace(async_create_task=asyncio.create_task),
            stream_lock=entity._stream_lock,
            is_current=lambda actual_stream, actual_session: (
                entity.stream is actual_stream and entity._session is actual_session
            ),
            stop_active=_stop,
            has_external_consumers=lambda: entity._snapshot_requests > 0,
            provider_grace=0,
            idle_grace=0,
            poll_interval=0,
        )
        monitor.schedule(stream, session)
        for _attempt in range(10):
            await asyncio.sleep(0)
        assert stops == 0

        entity._snapshot_requests = 0
        while stops == 0:
            await asyncio.sleep(0)
        await monitor.async_cancel()
        return stops

    assert asyncio.run(_run()) == 1


def test_video_camera_tracks_snapshot_request_for_entire_image_wait() -> None:
    async def _run() -> tuple[int, bytes | None, int]:
        entity = _uninitialized_entity()
        image_started = asyncio.Event()
        release_image = asyncio.Event()

        async def _camera_image(
            _width: int | None,
            _height: int | None,
        ) -> bytes:
            image_started.set()
            await release_image.wait()
            return b"snapshot"

        entity._async_camera_image_locked = _camera_image
        image_task = asyncio.create_task(entity.async_camera_image())
        await asyncio.wait_for(image_started.wait(), timeout=1)
        active_requests = entity._snapshot_requests
        release_image.set()
        image = await image_task
        return active_requests, image, entity._snapshot_requests

    assert asyncio.run(_run()) == (1, b"snapshot", 0)


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

        async def _async_refresh() -> object:
            return entity.coordinator.data

        entity.coordinator = SimpleNamespace(
            client=client,
            data=object(),
            last_update_success=True,
            async_refresh=_async_refresh,
            async_refresh_video_safety_state=_async_refresh,
        )
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
    async def _run() -> tuple[str | None, list[bool], str | None, str | None]:
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

        entity.coordinator.client = _Client()
        entity.coordinator.data = object()
        entity.stream = None
        entity._attr_is_streaming = False
        entity._runtime_preparation_error = None
        entity._last_stream_disable_error = None
        entity._async_stop_active_session = lambda: asyncio.sleep(0)
        entity._create_runtime = lambda: object()
        entity._set_stream_error = lambda _error, **_kwargs: None
        entity.hass = SimpleNamespace(
            async_add_executor_job=lambda function, *args: asyncio.sleep(
                0,
                result=function(*args),
            )
        )

        result = await entity._async_start_stream()
        return (
            result,
            calls,
            entity._last_video_transport,
            entity._last_video_transport_attempted,
        )

    assert asyncio.run(_run()) == (None, [True, False], None, VIDEO_TRANSPORT_CLOUD)


def test_video_camera_caches_provisioning_only_after_relay_media_ready() -> None:
    async def _run() -> tuple[str | None, int, int, int, int, dict[str, object]]:
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
            def __init__(self) -> None:
                self.starts = 0
                self.stops = 0

            def start_live_stream(
                self,
                actual_inputs: object,
            ) -> DreameLawnMowerXp2pLiveStreamSession:
                assert actual_inputs is inputs
                self.starts += 1
                return DreameLawnMowerXp2pLiveStreamSession(
                    service_id="product-1/device-1",
                    stream_url=f"http://127.0.0.1/cloud-{self.starts}.flv",
                )

            def stop_live_stream(self, _session: object) -> None:
                self.stops += 1

        cache = _ProvisioningCache()
        entity._provisioning_cache = cache
        entity._lan_cache = _LanCache()
        entity.coordinator.client = _Client()
        entity.coordinator.data = object()
        runtime = _Runtime()
        entity._prepared_runtime = runtime
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
        source = await entity._async_start_stream()
        saves_before_playback = cache.saves
        session = entity._session

        assert session is not None
        await entity._async_relay_media_ready(
            {
                "flv_header_present": True,
                "video_width": 640,
                "video_height": 360,
            }
        )
        return (
            source,
            saves_before_playback,
            cache.saves,
            runtime.starts,
            runtime.stops,
            entity._last_stream_health,
        )

    source, before, after, starts, stops, health = asyncio.run(_run())

    assert (source, before, after, starts, stops) == (
        "http://127.0.0.1/cloud-1.flv",
        0,
        1,
        1,
        0,
    )
    assert health["verification_source"] == "local_flv_relay"
    assert health["playback_session_verified"] is True
    assert health["available"] is True
    assert health["flv_header_present"] is True
    assert health["video_width"] == 640


def test_video_camera_cloud_start_cancellation_cleans_late_session() -> None:
    async def _run() -> tuple[list[bool], int, int]:
        entity = _uninitialized_entity()
        inputs = DreameLawnMowerCameraStreamRuntimeInputs(
            source="dreame_third_video_tx",
            did="did-1",
            product_id="product-1",
            device_name="device-1",
            p2p_info="p2p-info-1",
        )
        stream_enabled_calls: list[bool] = []
        start_started = asyncio.Event()
        release_start = asyncio.Event()
        stop_completed = asyncio.Event()

        class _Client:
            async def async_set_camera_stream_enabled(self, enabled: bool) -> None:
                stream_enabled_calls.append(enabled)

        class _Runtime:
            def __init__(self) -> None:
                self.starts = 0
                self.stops = 0

            def start_live_stream(
                self,
                _inputs: object,
            ) -> DreameLawnMowerXp2pLiveStreamSession:
                self.starts += 1
                return DreameLawnMowerXp2pLiveStreamSession(
                    service_id="product-1/device-1",
                    stream_url="http://127.0.0.1/cloud.flv",
                )

            def stop_live_stream(self, _session: object) -> None:
                self.stops += 1
                stop_completed.set()

        runtime = _Runtime()
        entity.coordinator.client = _Client()
        entity.coordinator.data = object()
        entity._prepared_runtime = runtime
        entity._runtime_preparation_error = None
        entity._last_stream_disable_error = None
        entity._last_error = None
        entity.async_write_ha_state = lambda: None

        async def _runtime_inputs() -> object:
            return inputs

        def _executor(function, *args):
            async def _run_executor_job():
                if function == runtime.start_live_stream:
                    start_started.set()
                    await release_start.wait()
                return function(*args)

            return asyncio.create_task(_run_executor_job())

        entity._async_get_runtime_inputs = _runtime_inputs
        entity.hass = SimpleNamespace(
            async_add_executor_job=_executor,
            async_create_task=asyncio.create_task,
        )

        task = asyncio.create_task(entity._async_start_stream())
        await start_started.wait()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        release_start.set()
        await asyncio.wait_for(stop_completed.wait(), timeout=1)
        return stream_enabled_calls, runtime.starts, runtime.stops

    assert asyncio.run(_run()) == (
        [True, False],
        1,
        1,
    )


def test_video_camera_lan_handoff_raises_when_probe_stop_fails() -> None:
    async def _run() -> tuple[int, int, str | None]:
        entity = _uninitialized_entity()
        inputs = DreameLawnMowerCameraStreamRuntimeInputs(
            source="lan_video_cache",
            did="did-1",
            product_id="product-1",
            device_name="device-1",
        )

        class _LanCache:
            endpoint = None

            @staticmethod
            async def async_save_session(_session: object) -> None:
                return None

        class _Runtime:
            def __init__(self) -> None:
                self.starts = 0
                self.stops = 0

            def start_lan_stream(
                self,
                _inputs: object,
            ) -> DreameLawnMowerXp2pLiveStreamSession:
                self.starts += 1
                return DreameLawnMowerXp2pLiveStreamSession(
                    service_id="product-1/device-1",
                    stream_url="http://127.0.0.1/lan.flv",
                )

            def stop_live_stream(self, _session: object) -> None:
                self.stops += 1
                raise RuntimeError("runner did not stop")

        runtime = _Runtime()
        entity._lan_cache = _LanCache()

        async def _executor(function, *args):
            return function(*args)

        entity.hass = SimpleNamespace(async_add_executor_job=_executor)
        health = DreameLawnMowerStreamUrlProbeResult(
            available=True,
            flv_header_present=True,
        )
        with patch.object(
            video_camera_module.video_helpers,
            "probe_stream_health_and_route",
            return_value=health,
        ):
            with pytest.raises(DreameLawnMowerVideoRuntimeError):
                await entity._async_try_lan_stream(runtime, inputs)
        return runtime.starts, runtime.stops, entity._last_lan_error

    starts, stops, error = asyncio.run(_run())

    assert starts == 1
    assert stops == 1
    assert error == (
        "Qualified same-LAN probe session could not stop before playback handoff."
    )


def test_video_camera_lan_probe_stop_failure_aborts_auto_fallback() -> None:
    async def _run() -> tuple[str | None, str | None]:
        entity = _uninitialized_entity(
            snapshot=SimpleNamespace(available=True, raw_attributes={})
        )
        entity._entry.options[CONF_VIDEO_TRANSPORT] = VIDEO_TRANSPORT_AUTO
        entity._prepared_runtime = object()
        entity._runtime_preparation_error = None
        entity._last_error = None
        entity._attr_is_streaming = False
        entity.async_write_ha_state = lambda: None
        entity._lan_cache = SimpleNamespace(
            inputs=DreameLawnMowerCameraStreamRuntimeInputs(
                source="lan_video_cache",
                did="did-1",
                product_id="product-1",
                device_name="device-1",
            ),
            endpoint=object(),
        )

        class _Client:
            async def async_get_camera_stream_runtime_inputs(self) -> object:
                raise AssertionError("Stop failure must not refresh LAN or use cloud")

            async def async_set_camera_stream_enabled(self, _enabled: bool) -> None:
                raise AssertionError("Stop failure must not enable cloud video")

        entity.coordinator.client = _Client()

        async def _failed_lan_start(
            _runtime: object,
            _inputs: object,
        ) -> str | None:
            raise DreameLawnMowerVideoRuntimeError(
                "Qualified same-LAN probe session could not stop before "
                "playback handoff."
            )

        entity._async_try_lan_stream = _failed_lan_start
        source = await entity._async_start_stream()
        return source, entity._last_error

    source, error = asyncio.run(_run())

    assert source is None
    assert error == (
        "Qualified same-LAN probe session could not stop before playback handoff."
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

        runtime = _Runtime()
        snapshot = SimpleNamespace(available=True, raw_attributes={})

        async def _async_refresh() -> object:
            return entity.coordinator.data

        entity.coordinator = SimpleNamespace(
            client=_Client(),
            data=snapshot,
            last_update_success=True,
            async_refresh=_async_refresh,
            async_refresh_video_safety_state=_async_refresh,
        )
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
        source = await entity._async_start_stream()
        session = entity._session
        assert entity._unverified_playback_session is session
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


def test_video_camera_managed_host_uses_bounded_cached_start() -> None:
    async def _run() -> tuple[int, int, bool]:
        entity = _uninitialized_entity()
        inputs = DreameLawnMowerCameraStreamRuntimeInputs(
            source="video_provisioning_cache",
            did="did-1",
            product_id="product-1",
            device_name="device-1",
            p2p_info="p2p-info-1",
        )

        class _Runtime:
            def __init__(self) -> None:
                self.cached_starts = 0
                self.regular_starts = 0

            def start_cached_live_stream(
                self,
                actual_inputs: object,
            ) -> DreameLawnMowerXp2pLiveStreamSession:
                assert actual_inputs is inputs
                self.cached_starts += 1
                return DreameLawnMowerXp2pLiveStreamSession(
                    service_id="product-1/device-1",
                    stream_url="http://127.0.0.1/cached.flv",
                )

            def start_live_stream(self, _inputs: object) -> object:
                self.regular_starts += 1
                raise AssertionError("Cached startup must use the bounded entrypoint")

        runtime = _Runtime()

        async def _executor(function, *args):
            return function(*args)

        entity.hass = SimpleNamespace(async_add_executor_job=_executor)
        session = await entity._async_start_cached_runtime_session(runtime, inputs)
        return (
            runtime.cached_starts,
            runtime.regular_starts,
            session.camera_toggle_managed,
        )

    assert asyncio.run(_run()) == (1, 0, False)


def test_video_camera_relay_upstream_start_has_bounded_deadline() -> None:
    async def _run() -> tuple[str | None, str | None]:
        entity = _uninitialized_entity()
        entity.async_write_ha_state = lambda: None

        async def _start() -> str:
            await asyncio.Future()
            return "unreachable"

        entity._async_start_raw_source = _start
        with patch.object(
            video_camera_module,
            "_VIDEO_UPSTREAM_START_TIMEOUT",
            0.01,
        ):
            source = await entity._async_start_relay_upstream()
        return source, entity._last_error_stage

    assert asyncio.run(_run()) == (None, "upstream_start_timeout")


def test_video_camera_relay_deadline_covers_managed_runtime_budget() -> None:
    assert (
        video_camera_module._VIDEO_UPSTREAM_START_TIMEOUT
        == xp2p_host_runtime_module.DEFAULT_XP2P_HOST_STARTUP_TIMEOUT
    )


@pytest.mark.parametrize(
    "transport",
    [VIDEO_TRANSPORT_AUTO, VIDEO_TRANSPORT_CLOUD, VIDEO_TRANSPORT_LAN],
)
def test_video_camera_safety_refresh_blocks_start_after_mower_docks(
    transport: str,
) -> None:
    async def _run() -> tuple[str | None, int, list[str]]:
        entity = _uninitialized_entity(
            snapshot=SimpleNamespace(
                available=True,
                state="paused",
                activity="paused",
                docked=False,
                raw_docked=False,
                returning=False,
                raw_attributes={},
            )
        )
        entity._entry.options[CONF_VIDEO_TRANSPORT] = transport
        entity._provisioning_cache.inputs = DreameLawnMowerCameraStreamRuntimeInputs(
            source="video_provisioning_cache",
            did="did-1",
            product_id="product-1",
            device_name="device-1",
            p2p_info="p2p-info-1",
        )
        entity._provisioning_cache.device_config = object()
        refreshes = 0
        errors: list[str] = []

        async def _async_refresh() -> object:
            nonlocal refreshes
            refreshes += 1
            entity.coordinator.data = SimpleNamespace(
                available=True,
                state="charging",
                activity="charging",
                docked=True,
                raw_docked=True,
                returning=False,
                raw_attributes={},
            )
            return entity.coordinator.data

        entity.coordinator.async_refresh_video_safety_state = _async_refresh
        entity._set_stream_error = lambda error, **_kwargs: errors.append(error)
        entity._async_stop_active_session = lambda: asyncio.sleep(0)
        entity.async_write_ha_state = lambda: None
        entity._create_runtime = lambda: (_ for _ in ()).throw(
            AssertionError("Unsafe cached startup must stop before runtime creation")
        )

        return await entity._async_start_stream(), refreshes, errors

    source, refreshes, errors = asyncio.run(_run())

    assert source is None
    assert refreshes == 1
    assert len(errors) == 1
    assert "blocked while the mower is docked" in errors[0]


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
