"""Coordinator availability regression checks."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.dreame_lawn_mower.coordinator import (
    DreameLawnMowerCoordinator,
    _runtime_tracking_active,
)
from custom_components.dreame_lawn_mower.coordinator_connectivity import (
    CONNECTIVITY_STALE_GRACE_SECONDS,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    FEATURE_LIVE_VIDEO,
)
from custom_components.dreame_lawn_mower.runtime_cache import (
    DreameLawnMowerRuntimeTelemetryCache,
)


def test_feature_capability_evidence_survives_sparse_snapshots() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.video_lan_cache = SimpleNamespace(inputs=None, endpoint=None)
    coordinator.video_provisioning_cache = SimpleNamespace(
        inputs=None,
        device_config=None,
    )
    advertised_snapshot = SimpleNamespace(
        capabilities=(),
        raw_info={"deviceInfo": {"permit": "pincode,video"}},
    )
    sparse_snapshot = SimpleNamespace(capabilities=(), raw_info={})

    coordinator._retain_feature_capability_evidence(advertised_snapshot)
    coordinator.data = sparse_snapshot

    observed, advertised = coordinator.feature_capability_evidence()
    assert observed == frozenset()
    assert advertised == frozenset({FEATURE_LIVE_VIDEO})


def test_persisted_video_route_is_observed_capability_evidence() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.video_lan_cache = SimpleNamespace(
        inputs=object(),
        endpoint=object(),
    )
    coordinator.video_provisioning_cache = SimpleNamespace(
        inputs=None,
        device_config=None,
    )
    coordinator.data = SimpleNamespace(capabilities=(), raw_info={})

    observed, advertised = coordinator.feature_capability_evidence()

    assert observed == frozenset({FEATURE_LIVE_VIDEO})
    assert advertised == frozenset()


def test_offline_snapshot_returns_normally_so_entities_remain_loaded() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.data = SimpleNamespace(state="stale")
    coordinator.runtime_status_blob = {"status": "stale"}
    offline_snapshot = SimpleNamespace(available=False)
    tracking_updates: list[tuple[object, bool]] = []
    coordinator.client = SimpleNamespace(
        async_refresh=lambda: _offline_snapshot(offline_snapshot),
        update_runtime_live_tracking=lambda value, *, active: tracking_updates.append(
            (value, active)
        ),
    )

    result = asyncio.run(coordinator._async_update_data())

    assert result is offline_snapshot
    assert coordinator.runtime_status_blob is None
    assert tracking_updates == [(None, False)]


def test_connectivity_shutdown_cancels_delayed_retry() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        coordinator._shutting_down = False
        coordinator.hass = SimpleNamespace(
            async_create_task=lambda coroutine, _name: asyncio.create_task(coroutine)
        )
        coordinator._initialize_connectivity_recovery()
        coordinator._schedule_connectivity_retry(60)
        retry_task = coordinator._connectivity_retry_task

        assert retry_task is not None
        assert not retry_task.done()

        await coordinator._async_shutdown_connectivity_recovery()

        assert retry_task.cancelled()
        assert coordinator._connectivity_retry_task is None
        assert coordinator._connectivity_retry_inflight_task is None

    asyncio.run(scenario())


def test_home_assistant_stop_does_not_start_metadata_drain() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        created_tasks: list[asyncio.Task[None]] = []

        def create_task(coroutine, _name):
            task = asyncio.create_task(coroutine)
            created_tasks.append(task)
            return task

        metadata_release = asyncio.Event()
        metadata_task = asyncio.create_task(metadata_release.wait())
        coordinator._shutting_down = False
        coordinator.hass = SimpleNamespace(async_create_task=create_task)
        coordinator._initialize_connectivity_recovery()
        coordinator._schedule_connectivity_retry(60)
        retry_task = coordinator._connectivity_retry_task
        coordinator._client_update_pending = False
        coordinator._client_update_task = None
        coordinator._metadata_refresh_task = metadata_task
        coordinator._metadata_shutdown_close_task = None
        coordinator.client = SimpleNamespace(
            set_update_callback=Mock(),
            async_close=AsyncMock(),
        )

        assert retry_task is not None
        await coordinator.async_shutdown_for_home_assistant_stop()

        assert retry_task.cancelled()
        assert not metadata_task.done()
        assert created_tasks == [retry_task]
        coordinator.client.async_close.assert_not_awaited()

        metadata_release.set()
        await metadata_task

    asyncio.run(scenario())


def test_home_assistant_stop_wins_concurrent_config_entry_unload() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        retry_cancelled = asyncio.Event()
        retry_release = asyncio.Event()
        metadata_release = asyncio.Event()

        async def cancellation_resistant_retry() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                retry_cancelled.set()
                await retry_release.wait()
                raise

        retry_task = asyncio.create_task(cancellation_resistant_retry())
        metadata_task = asyncio.create_task(metadata_release.wait())
        coordinator._shutting_down = False
        coordinator._home_assistant_stopping = False
        coordinator._owned_tasks_shutdown_lock = asyncio.Lock()
        coordinator._initialize_connectivity_recovery()
        coordinator._connectivity_retry_task = retry_task
        coordinator._client_update_pending = False
        coordinator._client_update_task = None
        coordinator._metadata_refresh_task = metadata_task
        coordinator._metadata_shutdown_close_task = None
        coordinator._batch_schedule_read_task = None
        coordinator._batch_schedule_read_tasks = set()
        coordinator.hass = SimpleNamespace(async_create_task=Mock())
        coordinator.client = SimpleNamespace(
            set_update_callback=Mock(),
            async_close=AsyncMock(),
        )

        unload = asyncio.create_task(coordinator.async_shutdown())
        await retry_cancelled.wait()
        stop = asyncio.create_task(
            coordinator.async_shutdown_for_home_assistant_stop()
        )
        await asyncio.sleep(0)
        assert not stop.done()

        retry_release.set()
        await asyncio.gather(unload, stop)

        assert retry_task.cancelled()
        assert not metadata_task.done()
        coordinator.hass.async_create_task.assert_not_called()
        coordinator.client.async_close.assert_not_awaited()

        metadata_release.set()
        await metadata_task

    asyncio.run(scenario())


def test_home_assistant_stop_cancels_existing_metadata_shutdown_cleanup() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        metadata_cancelled = asyncio.Event()
        metadata_release = asyncio.Event()

        async def cancellation_resistant_metadata() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                metadata_cancelled.set()
                await metadata_release.wait()
                raise

        metadata_task = asyncio.create_task(cancellation_resistant_metadata())
        await asyncio.sleep(0)
        metadata_task.cancel()
        await metadata_cancelled.wait()

        coordinator._shutting_down = False
        coordinator._home_assistant_stopping = False
        coordinator._owned_tasks_shutdown_lock = asyncio.Lock()
        coordinator._initialize_connectivity_recovery()
        coordinator._client_update_pending = False
        coordinator._client_update_task = None
        coordinator._metadata_refresh_task = metadata_task
        coordinator._batch_schedule_read_task = None
        coordinator._batch_schedule_read_tasks = set()
        coordinator.client = SimpleNamespace(
            set_update_callback=Mock(),
            async_close=AsyncMock(),
        )
        cleanup = asyncio.create_task(
            coordinator._async_close_after_metadata(metadata_task)
        )
        coordinator._metadata_shutdown_close_task = cleanup
        await asyncio.sleep(0)

        await asyncio.wait_for(
            coordinator.async_shutdown_for_home_assistant_stop(),
            timeout=1,
        )

        assert cleanup.cancelled()
        assert coordinator._metadata_shutdown_close_task is None
        assert not metadata_task.done()
        coordinator.client.async_close.assert_not_awaited()

        metadata_release.set()
        with suppress(asyncio.CancelledError):
            await metadata_task

    asyncio.run(scenario())


def test_short_offline_snapshot_retains_last_good_state() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    good_snapshot = SimpleNamespace(available=True, state="mowing")
    offline_snapshot = SimpleNamespace(available=False, state="offline")
    coordinator._record_connectivity_success(good_snapshot)
    coordinator.runtime_status_blob = {"status": "current"}
    coordinator.client = SimpleNamespace(
        async_refresh=AsyncMock(return_value=offline_snapshot),
        update_runtime_live_tracking=Mock(),
    )

    result = asyncio.run(coordinator._async_update_data())

    assert result is good_snapshot
    assert coordinator.runtime_status_blob == {"status": "current"}
    coordinator.client.update_runtime_live_tracking.assert_not_called()
    assert coordinator.connection_degraded is True
    assert coordinator.connection_failure_count == 1
    assert coordinator.connection_retry_after_seconds == 1.0


def test_offline_snapshot_expires_retained_state_after_grace() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    good_snapshot = SimpleNamespace(available=True, state="mowing")
    offline_snapshot = SimpleNamespace(available=False, state="offline")
    coordinator._record_connectivity_success(good_snapshot)
    coordinator._connectivity_last_success_monotonic -= (
        CONNECTIVITY_STALE_GRACE_SECONDS + 1
    )
    coordinator.runtime_status_blob = {"status": "stale"}
    coordinator.client = SimpleNamespace(
        async_refresh=AsyncMock(return_value=offline_snapshot),
        update_runtime_live_tracking=Mock(),
    )

    result = asyncio.run(coordinator._async_update_data())

    assert result is offline_snapshot
    assert coordinator.runtime_status_blob is None
    coordinator.client.update_runtime_live_tracking.assert_called_once_with(
        None,
        active=False,
    )


async def _offline_snapshot(snapshot: SimpleNamespace) -> SimpleNamespace:
    return snapshot


def test_runtime_tracking_respects_explicit_inactive_heartbeat() -> None:
    snapshot = SimpleNamespace(
        mowing_session_active=False,
        activity="mowing",
    )

    assert _runtime_tracking_active(snapshot) is False


def test_runtime_tracking_falls_back_when_heartbeat_state_is_unknown() -> None:
    snapshot = SimpleNamespace(
        mowing_session_active=None,
        activity="paused",
    )

    assert _runtime_tracking_active(snapshot) is True


def test_runtime_tracking_rejects_stale_paused_heartbeat_while_docked() -> None:
    snapshot = SimpleNamespace(
        mowing_session_active=True,
        task_status="paused",
        activity="docked",
        state="charging_completed",
        docked=True,
    )

    assert _runtime_tracking_active(snapshot) is False


def test_active_runtime_tracking_uses_fresh_app_map_identity() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    snapshot = SimpleNamespace(
        available=True,
        mowing_session_active=True,
        activity="mowing",
    )
    status_blob = SimpleNamespace()
    events: list[str] = []
    tracking_updates: list[tuple[object, bool, int | None]] = []
    coordinator.app_maps = {"current_map_index": 0}
    coordinator.app_maps_refreshed_at = datetime(2026, 7, 24, 10, tzinfo=UTC)
    coordinator.selected_map_index = 0
    coordinator.runtime_status_blob = None
    coordinator.runtime_telemetry_cache = SimpleNamespace(update=Mock())
    coordinator.client = SimpleNamespace(
        async_refresh=AsyncMock(return_value=snapshot),
        async_get_runtime_status_blob=AsyncMock(
            side_effect=lambda **_: events.append("runtime") or status_blob
        ),
        async_get_bluetooth_connected=AsyncMock(return_value=False),
        update_runtime_live_tracking=lambda value, *, active, map_index=None: (
            events.append("tracking"),
            tracking_updates.append((value, active, map_index)),
        ),
    )

    async def refresh_app_maps(*, force: bool) -> dict[str, object]:
        events.append("maps")
        assert force is True
        coordinator.app_maps = {"current_map_index": 2}
        coordinator.app_maps_refreshed_at = datetime.now(UTC)
        coordinator.app_maps_refresh_succeeded = True
        return coordinator.app_maps

    coordinator.async_refresh_app_maps = refresh_app_maps
    for name in (
        "async_refresh_batch_device_data",
        "async_refresh_firmware_update_support",
        "async_refresh_app_map_objects",
        "async_refresh_vector_map_details",
        "async_refresh_weather_protection",
        "async_refresh_maintenance_status",
        "async_refresh_voice_settings",
        "async_refresh_schedules",
    ):
        setattr(coordinator, name, AsyncMock())

    result = asyncio.run(coordinator._async_update_data())

    assert result is snapshot
    assert events[:3] == ["maps", "runtime", "tracking"]
    assert tracking_updates == [(status_blob, True, 2)]


def test_active_runtime_tracking_survives_status_blob_failure() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    snapshot = SimpleNamespace(
        available=True,
        mowing_session_active=True,
        activity="mowing",
    )
    tracking_updates: list[tuple[object, bool, int | None]] = []
    coordinator.app_maps = {"current_map_index": 2}
    coordinator.app_maps_refreshed_at = datetime(2026, 7, 24, 10, tzinfo=UTC)
    coordinator.selected_map_index = 2
    coordinator.runtime_status_blob = SimpleNamespace(status="old")
    coordinator.runtime_telemetry_cache = DreameLawnMowerRuntimeTelemetryCache(
        completion_confirmed=True,
    )
    coordinator.client = SimpleNamespace(
        async_refresh=AsyncMock(return_value=snapshot),
        async_get_runtime_status_blob=AsyncMock(
            side_effect=RuntimeError("telemetry unavailable")
        ),
        async_get_bluetooth_connected=AsyncMock(return_value=False),
        update_runtime_live_tracking=lambda value, *, active, map_index=None: (
            tracking_updates.append((value, active, map_index))
        ),
    )

    async def refresh_app_maps(*, force: bool) -> dict[str, object]:
        assert force is True
        coordinator.app_maps_refreshed_at = datetime.now(UTC)
        coordinator.app_maps_refresh_succeeded = True
        return coordinator.app_maps

    coordinator.async_refresh_app_maps = refresh_app_maps
    for name in (
        "async_refresh_batch_device_data",
        "async_refresh_firmware_update_support",
        "async_refresh_app_map_objects",
        "async_refresh_vector_map_details",
        "async_refresh_weather_protection",
        "async_refresh_maintenance_status",
        "async_refresh_voice_settings",
        "async_refresh_schedules",
    ):
        setattr(coordinator, name, AsyncMock())

    result = asyncio.run(coordinator._async_update_data())

    assert result is snapshot
    assert coordinator.runtime_status_blob is None
    assert coordinator.runtime_telemetry_cache.completion_confirmed is False
    assert tracking_updates == [(None, True, 2)]


def test_cached_device_update_publishes_realtime_runtime_position() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    snapshot = SimpleNamespace(
        available=True,
        mowing_session_active=True,
        activity="mowing",
    )
    status_blob = SimpleNamespace()
    tracking_updates: list[tuple[object, bool, int | None]] = []
    coordinator._client_update_task = Mock()
    coordinator._runtime_map_identity_verified = True
    coordinator.app_maps = {"current_map_index": 2}
    coordinator.selected_map_index = 2
    coordinator.runtime_status_blob = None
    coordinator.runtime_telemetry_cache = SimpleNamespace(update=Mock())
    coordinator.bluetooth_connected = None
    coordinator.client = SimpleNamespace(
        async_get_cached_snapshot=AsyncMock(return_value=snapshot),
        async_get_runtime_status_blob=AsyncMock(return_value=status_blob),
        async_get_bluetooth_connected=AsyncMock(return_value=True),
        update_runtime_live_tracking=lambda value, *, active, map_index=None: (
            tracking_updates.append((value, active, map_index))
        ),
    )
    coordinator.async_set_updated_data = Mock()

    asyncio.run(coordinator._async_process_client_update())

    coordinator.client.async_get_runtime_status_blob.assert_awaited_once_with(
        refresh=False,
        include_cloud=False,
    )
    coordinator.client.async_get_bluetooth_connected.assert_awaited_once_with(
        refresh=False,
        include_cloud=False,
    )
    coordinator.runtime_telemetry_cache.update.assert_called_once_with(
        status_blob,
        allow_zero=True,
        active_session=True,
        completion_confirmed=False,
        completion_rejected=False,
        new_session=False,
        new_session_event_at=None,
        new_session_evidence=None,
        session_identity=None,
    )
    assert tracking_updates == [(status_blob, True, 2)]
    assert coordinator.runtime_status_blob is status_blob
    assert coordinator.bluetooth_connected is True
    coordinator.async_set_updated_data.assert_called_once_with(snapshot)
    assert coordinator._client_update_task is None


def test_cached_settings_event_refreshes_cfg_once_per_event() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    snapshot = SimpleNamespace(
        available=True,
        mowing_session_active=False,
        activity="docked",
        device_settings_event_at=123.0,
    )
    coordinator._client_update_task = Mock()
    coordinator._runtime_map_identity_verified = True
    coordinator._last_device_settings_event_at = None
    coordinator._device_settings_write_lock = asyncio.Lock()
    coordinator.app_maps = {"current_map_index": 0}
    coordinator.selected_map_index = 0
    coordinator.runtime_status_blob = None
    coordinator.runtime_telemetry_cache = SimpleNamespace(update=Mock())
    coordinator.bluetooth_connected = None
    coordinator.client = SimpleNamespace(
        async_get_cached_snapshot=AsyncMock(return_value=snapshot),
        async_get_runtime_status_blob=AsyncMock(return_value=None),
        async_get_bluetooth_connected=AsyncMock(return_value=False),
        update_runtime_live_tracking=Mock(),
    )
    coordinator.async_set_updated_data = Mock()
    coordinator.async_refresh_device_settings = AsyncMock(
        return_value={"present_config_keys": ["BAT", "WRP"], "errors": []}
    )
    coordinator.async_update_listeners = Mock()

    with patch(
        "custom_components.dreame_lawn_mower.coordinator.asyncio.sleep",
        new=AsyncMock(),
    ):
        asyncio.run(coordinator._async_process_client_update())
        asyncio.run(coordinator._async_process_client_update())

    coordinator.async_refresh_device_settings.assert_awaited_once_with(
        force=True,
        source="device_settings_realtime",
    )
    coordinator.async_update_listeners.assert_called_once_with()
    assert coordinator._last_device_settings_event_at == 123.0


def test_cached_settings_event_retries_after_failed_cfg_refresh() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    snapshot = SimpleNamespace(
        available=True,
        mowing_session_active=False,
        activity="docked",
        device_settings_event_at=123.0,
    )
    coordinator._client_update_task = Mock()
    coordinator._runtime_map_identity_verified = True
    coordinator._last_device_settings_event_at = None
    coordinator.app_maps = {"current_map_index": 0}
    coordinator.selected_map_index = 0
    coordinator.runtime_status_blob = None
    coordinator.runtime_telemetry_cache = SimpleNamespace(update=Mock())
    coordinator.bluetooth_connected = None
    coordinator.client = SimpleNamespace(
        async_get_cached_snapshot=AsyncMock(return_value=snapshot),
        async_get_runtime_status_blob=AsyncMock(return_value=None),
        async_get_bluetooth_connected=AsyncMock(return_value=False),
        update_runtime_live_tracking=Mock(),
    )
    coordinator.async_set_updated_data = Mock()
    coordinator.async_refresh_device_settings = AsyncMock(
        side_effect=(
            None,
            {"present_config_keys": ["BAT", "WRP"], "errors": []},
        )
    )
    coordinator.async_update_listeners = Mock()

    with patch(
        "custom_components.dreame_lawn_mower.coordinator.asyncio.sleep",
        new=AsyncMock(),
    ):
        asyncio.run(coordinator._async_process_client_update())
        assert coordinator._last_device_settings_event_at is None
        asyncio.run(coordinator._async_process_client_update())

    assert coordinator.async_refresh_device_settings.await_count == 2
    coordinator.async_update_listeners.assert_called_once_with()
    assert coordinator._last_device_settings_event_at == 123.0


def test_cached_preference_event_refreshes_only_preferences_once() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    snapshot = SimpleNamespace(
        available=True,
        mowing_session_active=False,
        activity="docked",
        device_settings_event_at=None,
        mowing_preferences_event_at=456.0,
    )
    preferences = {
        "available": True,
        "maps": [{"idx": 0, "preferences": []}],
        "errors": [],
    }
    coordinator._client_update_task = Mock()
    coordinator._runtime_map_identity_verified = True
    coordinator._last_device_settings_event_at = None
    coordinator._last_mowing_preferences_event_at = None
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator.app_maps = {"current_map_index": 0, "maps": [{"idx": 0}]}
    coordinator.batch_device_data = {
        "batch_schedule": {"available": True},
        "batch_ota_info": {"available": True},
    }
    coordinator.selected_map_index = 0
    coordinator.runtime_status_blob = None
    coordinator.runtime_telemetry_cache = SimpleNamespace(update=Mock())
    coordinator.bluetooth_connected = None
    coordinator.client = SimpleNamespace(
        async_get_cached_snapshot=AsyncMock(return_value=snapshot),
        async_get_runtime_status_blob=AsyncMock(return_value=None),
        async_get_bluetooth_connected=AsyncMock(return_value=False),
        async_get_batch_mowing_preferences=AsyncMock(return_value=preferences),
        update_runtime_live_tracking=Mock(),
    )
    coordinator.async_set_updated_data = Mock()
    coordinator.async_update_listeners = Mock()

    with patch(
        "custom_components.dreame_lawn_mower.coordinator.asyncio.sleep",
        new=AsyncMock(),
    ):
        asyncio.run(coordinator._async_process_client_update())
        asyncio.run(coordinator._async_process_client_update())

    coordinator.client.async_get_batch_mowing_preferences.assert_awaited_once_with(
        include_raw=False,
        map_index_hints=[0],
    )
    assert coordinator.batch_device_data["batch_schedule"] == {"available": True}
    assert coordinator.batch_device_data["batch_ota_info"] == {"available": True}
    assert coordinator.batch_device_data["batch_mowing_preferences"] is preferences
    assert coordinator._last_mowing_preferences_event_at == 456.0


def test_cached_preference_event_retries_after_failed_decode() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    snapshot = SimpleNamespace(
        available=True,
        mowing_session_active=False,
        activity="docked",
        device_settings_event_at=None,
        mowing_preferences_event_at=456.0,
    )
    coordinator._client_update_task = Mock()
    coordinator._runtime_map_identity_verified = True
    coordinator._last_device_settings_event_at = None
    coordinator._last_mowing_preferences_event_at = None
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator.app_maps = {"current_map_index": 0}
    coordinator.batch_device_data = None
    coordinator.selected_map_index = 0
    coordinator.runtime_status_blob = None
    coordinator.runtime_telemetry_cache = SimpleNamespace(update=Mock())
    coordinator.bluetooth_connected = None
    coordinator.client = SimpleNamespace(
        async_get_cached_snapshot=AsyncMock(return_value=snapshot),
        async_get_runtime_status_blob=AsyncMock(return_value=None),
        async_get_bluetooth_connected=AsyncMock(return_value=False),
        async_get_batch_mowing_preferences=AsyncMock(
            side_effect=(
                {"available": False, "maps": [], "errors": ["not ready"]},
                {"available": True, "maps": [{"idx": 0}], "errors": []},
            )
        ),
        update_runtime_live_tracking=Mock(),
    )
    coordinator.async_set_updated_data = Mock()
    coordinator.async_update_listeners = Mock()

    with patch(
        "custom_components.dreame_lawn_mower.coordinator.asyncio.sleep",
        new=AsyncMock(),
    ):
        asyncio.run(coordinator._async_process_client_update())
        assert coordinator._last_mowing_preferences_event_at is None
        asyncio.run(coordinator._async_process_client_update())

    assert coordinator.client.async_get_batch_mowing_preferences.await_count == 2
    assert coordinator._last_mowing_preferences_event_at == 456.0


def test_cached_completion_survives_runtime_failure_and_later_idle_refresh() -> None:
    """A transient completion event is cached before optional telemetry reads."""
    completed = SimpleNamespace(
        available=True,
        mowing_session_active=False,
        activity="docked",
        docked=True,
        task_status="idle",
        status_notice_name="mowing_task_completed",
    )
    idle = SimpleNamespace(
        available=True,
        mowing_session_active=False,
        activity="docked",
        docked=True,
        task_status="idle",
        status_notice_name=None,
    )
    blob = SimpleNamespace(candidate_runtime_area_progress_percent=99.7)
    cache = DreameLawnMowerRuntimeTelemetryCache()
    assert cache.update(blob) is True
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._client_update_task = Mock()
    coordinator._runtime_map_identity_verified = True
    coordinator.app_maps = {"current_map_index": 2}
    coordinator.selected_map_index = 2
    coordinator.runtime_status_blob = None
    coordinator.runtime_telemetry_cache = cache
    coordinator.bluetooth_connected = None
    coordinator.client = SimpleNamespace(
        async_get_cached_snapshot=AsyncMock(side_effect=(completed, idle)),
        async_get_runtime_status_blob=AsyncMock(
            side_effect=RuntimeError("telemetry unavailable")
        ),
        async_get_bluetooth_connected=AsyncMock(return_value=False),
        update_runtime_live_tracking=Mock(),
    )
    coordinator.async_set_updated_data = Mock()

    asyncio.run(coordinator._async_process_client_update())
    assert cache.completion_confirmed is True

    asyncio.run(coordinator._async_process_client_update())
    assert cache.completion_confirmed is True
    assert cache.blob is blob


def test_new_session_discards_prior_telemetry_before_completion() -> None:
    """A later success event cannot relabel a prior mission's measurements."""
    active = SimpleNamespace(
        available=True,
        mowing_session_active=None,
        activity="mowing",
        docked=False,
        task_status="starting",
        task_resumable=False,
        status_notice_name=None,
    )
    completed = SimpleNamespace(
        available=True,
        mowing_session_active=False,
        activity="docked",
        docked=True,
        task_status="idle",
        status_notice_name="mowing_task_completed",
    )
    previous_blob = SimpleNamespace(candidate_runtime_area_progress_percent=42.0)
    cache = DreameLawnMowerRuntimeTelemetryCache()
    assert cache.update(previous_blob, active_session=True) is True
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._client_update_task = Mock()
    coordinator._runtime_map_identity_verified = True
    coordinator.app_maps = {"current_map_index": 2}
    coordinator.selected_map_index = 2
    coordinator.runtime_status_blob = None
    coordinator.runtime_telemetry_cache = cache
    coordinator.bluetooth_connected = None
    coordinator.client = SimpleNamespace(
        async_get_cached_snapshot=AsyncMock(side_effect=(active, completed)),
        async_get_runtime_status_blob=AsyncMock(
            side_effect=RuntimeError("telemetry unavailable")
        ),
        async_get_bluetooth_connected=AsyncMock(return_value=False),
        update_runtime_live_tracking=Mock(),
    )
    coordinator.async_set_updated_data = Mock()

    asyncio.run(coordinator._async_process_client_update())
    assert cache.blob is None
    assert cache.completion_confirmed is False

    asyncio.run(coordinator._async_process_client_update())
    assert cache.blob is None
    assert cache.completion_confirmed is True


def test_resumed_charging_session_preserves_current_telemetry() -> None:
    """Missing and paused heartbeats cannot split one charging mission."""
    missing_heartbeat = SimpleNamespace(
        available=True,
        mowing_session_active=None,
        activity="docked",
        state="charging",
        docked=True,
        task_status=None,
        task_resumable=None,
        status_notice_name=None,
    )
    paused_heartbeat = SimpleNamespace(
        available=True,
        mowing_session_active=None,
        activity="docked",
        state="charging",
        docked=True,
        task_status="paused",
        task_resumable=True,
        status_notice_name=None,
    )
    resumed = SimpleNamespace(
        available=True,
        mowing_session_active=None,
        activity="mowing",
        state="mowing",
        docked=False,
        task_status="mowing",
        task_resumable=False,
        status_notice_name=None,
    )
    completed = SimpleNamespace(
        available=True,
        mowing_session_active=False,
        activity="docked",
        state="charging",
        docked=True,
        task_status="finished",
        task_resumable=False,
        status_notice_name="mowing_task_completed",
    )
    current_blob = SimpleNamespace(candidate_runtime_area_progress_percent=42.0)
    cache = DreameLawnMowerRuntimeTelemetryCache()
    assert cache.update(current_blob, active_session=True) is True
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._client_update_task = Mock()
    coordinator._runtime_map_identity_verified = True
    coordinator.app_maps = {"current_map_index": 2}
    coordinator.selected_map_index = 2
    coordinator.runtime_status_blob = None
    coordinator.runtime_telemetry_cache = cache
    coordinator.bluetooth_connected = None
    coordinator.client = SimpleNamespace(
        async_get_cached_snapshot=AsyncMock(
            side_effect=(
                missing_heartbeat,
                paused_heartbeat,
                resumed,
                completed,
            )
        ),
        async_get_runtime_status_blob=AsyncMock(
            side_effect=RuntimeError("telemetry unavailable")
        ),
        async_get_bluetooth_connected=AsyncMock(return_value=False),
        update_runtime_live_tracking=Mock(),
    )
    coordinator.async_set_updated_data = Mock()

    asyncio.run(coordinator._async_process_client_update())
    assert cache.blob is current_blob
    assert cache.completion_confirmed is False

    asyncio.run(coordinator._async_process_client_update())
    assert cache.blob is current_blob
    assert cache.completion_confirmed is False

    asyncio.run(coordinator._async_process_client_update())
    assert cache.blob is current_blob
    assert cache.completion_confirmed is False

    asyncio.run(coordinator._async_process_client_update())
    assert cache.blob is current_blob
    assert cache.completion_confirmed is True


def test_foreground_charging_snapshot_preserves_current_session_cache() -> None:
    """Foreground refresh preserves cache when heartbeat enrichment fails."""
    charging = SimpleNamespace(
        available=True,
        mowing_session_active=None,
        activity="docked",
        state="charging",
        docked=True,
        task_status=None,
        task_resumable=None,
        status_notice_name="mowing_task_completed",
    )
    current_blob = SimpleNamespace(candidate_runtime_area_progress_percent=42.0)
    cache = DreameLawnMowerRuntimeTelemetryCache()
    assert cache.update(current_blob, active_session=True) is True
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.runtime_status_blob = None
    coordinator.runtime_telemetry_cache = cache
    coordinator._runtime_map_identity_verified = True
    coordinator._schedule_metadata_refresh = Mock()
    coordinator.client = SimpleNamespace(
        async_refresh=AsyncMock(return_value=charging),
        update_runtime_live_tracking=Mock(),
    )

    result = asyncio.run(coordinator._async_update_data())

    assert result is charging
    assert cache.blob is current_blob
    assert cache.completion_confirmed is False
    coordinator.client.update_runtime_live_tracking.assert_called_once_with(
        None,
        active=False,
    )


def test_cached_device_update_does_not_confirm_connectivity() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    confirmed = SimpleNamespace(
        available=True,
        mowing_session_active=False,
        activity="idle",
    )
    optimistic = SimpleNamespace(
        available=True,
        mowing_session_active=True,
        activity="mowing",
    )
    coordinator._record_connectivity_success(confirmed)
    coordinator._record_connectivity_failure("action acknowledgement was lost")
    coordinator._client_update_task = Mock()
    coordinator._runtime_map_identity_verified = True
    coordinator.app_maps = {"current_map_index": 2}
    coordinator.selected_map_index = 2
    coordinator.runtime_status_blob = None
    coordinator.runtime_telemetry_cache = SimpleNamespace(update=Mock())
    coordinator.bluetooth_connected = None
    coordinator.client = SimpleNamespace(
        async_get_cached_snapshot=AsyncMock(return_value=optimistic),
        async_get_runtime_status_blob=AsyncMock(return_value=SimpleNamespace()),
        async_get_bluetooth_connected=AsyncMock(return_value=True),
        update_runtime_live_tracking=Mock(),
    )
    coordinator.async_set_updated_data = Mock()

    asyncio.run(coordinator._async_process_client_update())

    assert coordinator._connectivity_last_good_snapshot is confirmed
    assert coordinator.connection_degraded is True
    assert coordinator.connection_failure_count == 1
    coordinator.async_set_updated_data.assert_called_once_with(optimistic)


def test_newer_video_safety_state_wins_over_delayed_cached_mqtt_update() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        cached_snapshot = SimpleNamespace(
            available=True,
            mowing_session_active=True,
            activity="mowing",
        )
        video_snapshot = SimpleNamespace(
            available=True,
            mowing_session_active=False,
            activity="idle",
        )
        bluetooth_started = asyncio.Event()
        release_bluetooth = asyncio.Event()

        async def runtime_status(*, refresh: bool, include_cloud: bool):
            assert refresh is False
            assert include_cloud is False
            return SimpleNamespace()

        async def bluetooth_status(*, refresh: bool, include_cloud: bool):
            assert refresh is False
            assert include_cloud is False
            bluetooth_started.set()
            await release_bluetooth.wait()
            return True

        coordinator._client_update_task = Mock()
        coordinator._client_update_pending = False
        coordinator._shutting_down = False
        coordinator._runtime_map_identity_verified = True
        coordinator._device_refresh_lock = asyncio.Lock()
        coordinator._device_snapshot_generation = 0
        coordinator._published_device_snapshot_generation = 0
        coordinator._device_snapshot_generations = {}
        coordinator.app_maps = {"current_map_index": 2}
        coordinator.selected_map_index = 2
        coordinator.runtime_status_blob = None
        completed_blob = SimpleNamespace(candidate_runtime_area_progress_percent=99.7)
        coordinator.runtime_telemetry_cache = DreameLawnMowerRuntimeTelemetryCache()
        assert (
            coordinator.runtime_telemetry_cache.update(
                completed_blob,
                completion_confirmed=True,
            )
            is True
        )
        coordinator.bluetooth_connected = None
        coordinator.client = SimpleNamespace(
            async_get_cached_snapshot=AsyncMock(return_value=cached_snapshot),
            async_refresh=AsyncMock(return_value=video_snapshot),
            async_refresh_authoritative_snapshot=AsyncMock(return_value=video_snapshot),
            async_get_runtime_status_blob=runtime_status,
            async_get_bluetooth_connected=bluetooth_status,
            update_runtime_live_tracking=Mock(),
        )

        with patch.object(
            DataUpdateCoordinator,
            "async_set_updated_data",
        ) as publish:
            cached_task = asyncio.create_task(
                coordinator._async_process_client_update()
            )
            await asyncio.wait_for(bluetooth_started.wait(), timeout=1)
            result = await coordinator.async_refresh_video_safety_state()
            release_bluetooth.set()
            await cached_task

        assert result is video_snapshot
        publish.assert_called_once_with(video_snapshot)
        assert coordinator.runtime_status_blob is None
        assert coordinator.runtime_telemetry_cache.blob is completed_blob
        assert coordinator.runtime_telemetry_cache.completion_confirmed is True
        coordinator.client.update_runtime_live_tracking.assert_not_called()
        assert coordinator.bluetooth_connected is None

    asyncio.run(scenario())


def test_command_boundary_blocks_delayed_cached_mqtt_runtime_side_effects() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        snapshot = SimpleNamespace(
            available=True,
            mowing_session_active=False,
            activity="idle",
        )
        runtime_started = asyncio.Event()
        release_runtime = asyncio.Event()
        stale_runtime = SimpleNamespace(candidate_runtime_area_progress_percent=100.0)
        cache = DreameLawnMowerRuntimeTelemetryCache()
        assert cache.update(stale_runtime, completion_confirmed=True) is True

        async def runtime_status(*, refresh: bool, include_cloud: bool) -> object:
            assert refresh is False
            assert include_cloud is False
            runtime_started.set()
            await release_runtime.wait()
            return stale_runtime

        coordinator._client_update_task = Mock()
        coordinator._client_update_pending = False
        coordinator._shutting_down = False
        coordinator._runtime_map_identity_verified = True
        coordinator._device_refresh_lock = asyncio.Lock()
        coordinator._device_snapshot_generation = 0
        coordinator._published_device_snapshot_generation = 0
        coordinator._device_snapshot_generations = {}
        coordinator.app_maps = {"current_map_index": 2}
        coordinator.selected_map_index = 2
        coordinator.runtime_status_blob = None
        coordinator.runtime_telemetry_cache = cache
        coordinator.bluetooth_connected = None
        coordinator.client = SimpleNamespace(
            async_get_cached_snapshot=AsyncMock(return_value=snapshot),
            async_get_runtime_status_blob=runtime_status,
            async_get_bluetooth_connected=AsyncMock(return_value=True),
            update_runtime_live_tracking=Mock(),
        )
        coordinator.async_set_updated_data = Mock()

        update_task = asyncio.create_task(coordinator._async_process_client_update())
        await asyncio.wait_for(runtime_started.wait(), timeout=1)
        cache.begin_new_session(session_started_at=20.0)
        release_runtime.set()
        await update_task

        assert cache.blob is None
        assert coordinator.runtime_status_blob is None
        assert coordinator.bluetooth_connected is None
        coordinator.client.update_runtime_live_tracking.assert_not_called()
        coordinator.async_set_updated_data.assert_not_called()

    asyncio.run(scenario())


def test_cached_device_update_waits_for_verified_active_map_identity() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    snapshot = SimpleNamespace(
        available=True,
        mowing_session_active=True,
        activity="mowing",
    )
    status_blob = SimpleNamespace()
    coordinator._client_update_task = Mock()
    coordinator._runtime_map_identity_verified = False
    coordinator.app_maps = {"current_map_index": 2}
    coordinator.selected_map_index = 2
    coordinator.runtime_status_blob = None
    coordinator.runtime_telemetry_cache = SimpleNamespace(update=Mock())
    coordinator.bluetooth_connected = None
    coordinator.client = SimpleNamespace(
        async_get_cached_snapshot=AsyncMock(return_value=snapshot),
        async_get_runtime_status_blob=AsyncMock(return_value=status_blob),
        async_get_bluetooth_connected=AsyncMock(return_value=True),
        update_runtime_live_tracking=Mock(),
    )
    coordinator.async_set_updated_data = Mock()

    asyncio.run(coordinator._async_process_client_update())

    coordinator.client.update_runtime_live_tracking.assert_called_once_with(
        status_blob,
        active=True,
        map_index=None,
    )


def test_cached_device_update_queues_callback_received_while_processing() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._client_update_task = Mock()
    coordinator._client_update_pending = False
    coordinator._shutting_down = False

    coordinator._schedule_client_update()

    assert coordinator._client_update_pending is True


def test_cached_device_update_reschedules_pending_callback() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._client_update_task = Mock()
    coordinator._client_update_pending = True
    coordinator._shutting_down = False
    coordinator.runtime_status_blob = None
    coordinator.client = SimpleNamespace(
        async_get_cached_snapshot=AsyncMock(
            return_value=SimpleNamespace(available=False)
        ),
        update_runtime_live_tracking=Mock(),
    )
    coordinator.async_set_updated_data = Mock()
    coordinator._schedule_client_update = Mock()

    asyncio.run(coordinator._async_process_client_update())

    assert coordinator._client_update_task is None
    assert coordinator._client_update_pending is False
    coordinator._schedule_client_update.assert_called_once_with()


def test_preference_updates_are_serialized_around_full_payload_operation() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator.last_preference_write_result = None
    coordinator.async_update_listeners = Mock()
    active = 0
    maximum_active = 0

    async def plan_update(**kwargs):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1
        return {"area_id": kwargs["area_id"]}

    coordinator.client = SimpleNamespace(
        async_plan_app_mowing_preference_update=plan_update
    )

    async def run_updates() -> None:
        await asyncio.gather(
            coordinator.async_plan_mowing_preference_update(
                map_index=1,
                area_id=1,
                changes={"mowing_height_cm": 4.0},
                execute=False,
                confirm_write=False,
            ),
            coordinator.async_plan_mowing_preference_update(
                map_index=1,
                area_id=2,
                changes={"mowing_height_cm": 5.0},
                execute=False,
                confirm_write=False,
            ),
        )

    asyncio.run(run_updates())

    assert maximum_active == 1
    assert coordinator.async_update_listeners.call_count == 2


def test_runtime_map_identity_does_not_fall_back_after_fresh_unknown_map() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.app_maps = {"current_map_index": None}
    coordinator.selected_map_index = 1

    assert coordinator._runtime_map_index() is None


def test_app_map_refresh_synchronizes_selected_map_identity() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.client = SimpleNamespace(
        async_get_app_maps=AsyncMock(return_value={"current_map_index": 2, "maps": []})
    )
    coordinator.app_maps = {"current_map_index": 0}
    coordinator.app_maps_refreshed_at = None
    coordinator.selected_map_index = 0
    coordinator.selected_contour_id = (3, 0)
    coordinator.selected_zone_id = 3
    coordinator.selected_spot_id = 2

    asyncio.run(coordinator.async_refresh_app_maps(force=True))

    assert coordinator.selected_map_index == 2
    assert coordinator.selected_contour_id is None
    assert coordinator.selected_zone_id is None
    assert coordinator.selected_spot_id is None


def test_app_map_refresh_clears_map_scoped_selection_for_deleted_map() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.client = SimpleNamespace(
        async_get_app_maps=AsyncMock(
            return_value={
                "current_map_index": None,
                "map_list_valid": True,
                "maps": [{"idx": 0, "created": True}],
            }
        )
    )
    coordinator.app_maps = {
        "current_map_index": 1,
        "map_list_valid": True,
        "maps": [
            {"idx": 0, "created": True},
            {"idx": 1, "created": True},
        ],
    }
    coordinator.app_maps_refreshed_at = None
    coordinator.app_maps_refresh_succeeded = True
    coordinator.selected_map_index = 1
    coordinator.selected_contour_id = (3, 0)
    coordinator.selected_zone_id = 3
    coordinator.selected_spot_id = 2
    coordinator.selected_maintenance_point_id = 302

    asyncio.run(coordinator.async_refresh_app_maps(force=True))

    assert coordinator.selected_map_index is None
    assert coordinator.selected_contour_id is None
    assert coordinator.selected_zone_id is None
    assert coordinator.selected_spot_id is None
    assert coordinator.selected_maintenance_point_id is None


def test_app_map_cache_hit_preserves_failed_refresh_status() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.client = SimpleNamespace(async_get_app_maps=AsyncMock())
    coordinator.app_maps = {"current_map_index": 0}
    coordinator.app_maps_refreshed_at = datetime.now(UTC)
    coordinator.app_maps_refresh_succeeded = False

    result = asyncio.run(coordinator.async_refresh_app_maps())

    assert result is coordinator.app_maps
    assert coordinator.app_maps_refresh_succeeded is False
    coordinator.client.async_get_app_maps.assert_not_awaited()


def test_app_map_refresh_marks_invalid_inventory_for_retry() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.client = SimpleNamespace(
        async_get_app_maps=AsyncMock(
            return_value={
                "map_list_valid": False,
                "current_map_index": None,
                "maps": [{"idx": 0, "created": True}],
            }
        )
    )
    coordinator.app_maps = None
    coordinator.app_maps_refreshed_at = None
    coordinator.app_maps_refresh_succeeded = False
    coordinator.selected_map_index = None

    result = asyncio.run(coordinator.async_refresh_app_maps(force=True))

    assert result["map_list_valid"] is False
    assert coordinator.app_maps_refresh_succeeded is False
    assert coordinator._metadata_phase_needs_retry("app_maps", result)
