"""Coordinator availability regression checks."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.dreame_lawn_mower import (
    coordinator as coordinator_module,
)
from custom_components.dreame_lawn_mower import (
    coordinator_connectivity as connectivity_module,
)
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
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    mark_write_attempted,
)
from custom_components.dreame_lawn_mower.preference_cache import (
    merge_mowing_preference_readbacks,
    reconcile_pending_preference_readbacks,
    retain_confirmed_preference_write,
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
        coordinator.entry = SimpleNamespace(
            async_create_background_task=lambda _hass, coroutine, _name: (
                asyncio.create_task(coroutine)
            )
        )
        coordinator._shutting_down = False
        coordinator.hass = SimpleNamespace()
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


def test_home_assistant_stop_skips_metadata_drain_and_closes_client() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        created_tasks: list[asyncio.Task[None]] = []

        def create_task(_hass, coroutine, _name):
            task = asyncio.create_task(coroutine)
            created_tasks.append(task)
            return task

        metadata_release = asyncio.Event()
        metadata_task = asyncio.create_task(metadata_release.wait())
        coordinator.entry = SimpleNamespace(
            async_create_background_task=create_task,
        )
        coordinator._shutting_down = False
        coordinator._base_shutdown_complete = True
        coordinator.hass = SimpleNamespace()
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
        coordinator.client.async_close.assert_awaited_once_with()

        metadata_release.set()
        await metadata_task

    asyncio.run(scenario())


def test_home_assistant_stop_bounds_concurrent_config_entry_unload() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        retry_cancelled = asyncio.Event()
        retry_release = asyncio.Event()
        metadata_release = asyncio.Event()

        async def cancellation_resistant_refresh() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                retry_cancelled.set()
                while not retry_release.is_set():
                    try:
                        await retry_release.wait()
                    except asyncio.CancelledError:
                        pass
                raise

        metadata_task = asyncio.create_task(metadata_release.wait())
        coordinator.entry = SimpleNamespace(
            async_create_background_task=lambda _hass, coroutine, _name: (
                asyncio.create_task(coroutine)
            )
        )
        coordinator.hass = SimpleNamespace(async_create_task=Mock())
        coordinator._shutting_down = False
        coordinator._home_assistant_stopping = False
        coordinator._base_shutdown_complete = True
        coordinator._owned_tasks_shutdown_lock = asyncio.Lock()
        coordinator._initialize_connectivity_recovery()
        coordinator.async_request_refresh = cancellation_resistant_refresh
        coordinator._schedule_connectivity_retry(0)
        while coordinator._connectivity_retry_inflight_task is None:
            await asyncio.sleep(0)
        retry_task = coordinator._connectivity_retry_inflight_task
        assert retry_task is not None
        coordinator._client_update_pending = False
        coordinator._client_update_task = None
        coordinator._metadata_refresh_task = metadata_task
        coordinator._metadata_shutdown_close_task = None
        coordinator._batch_schedule_read_task = None
        coordinator._batch_schedule_read_tasks = set()
        coordinator.client = SimpleNamespace(
            set_update_callback=Mock(),
            async_close=AsyncMock(),
        )

        with patch.object(
            connectivity_module,
            "CONNECTIVITY_SHUTDOWN_GRACE_SECONDS",
            0.01,
        ):
            unload = asyncio.create_task(coordinator.async_shutdown())
            await retry_cancelled.wait()
            stop = asyncio.create_task(
                coordinator.async_shutdown_for_home_assistant_stop()
            )
            await asyncio.wait_for(asyncio.gather(unload, stop), timeout=0.5)

        assert not retry_task.done()
        assert not metadata_task.done()
        coordinator.hass.async_create_task.assert_not_called()
        coordinator.client.async_close.assert_awaited_once_with()

        retry_release.set()
        with suppress(asyncio.CancelledError):
            await retry_task
        assert coordinator._connectivity_retry_task is None
        assert coordinator._connectivity_retry_inflight_task is None

        metadata_release.set()
        await metadata_task

    asyncio.run(scenario())


def test_home_assistant_stop_bounds_cancellation_resistant_realtime_update() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        update_started = asyncio.Event()
        update_cancelled = asyncio.Event()
        update_release = asyncio.Event()

        async def cancellation_resistant_snapshot() -> None:
            update_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                update_cancelled.set()
                while not update_release.is_set():
                    try:
                        await update_release.wait()
                    except asyncio.CancelledError:
                        pass
                raise

        coordinator._shutting_down = False
        coordinator._base_shutdown_complete = True
        coordinator._owned_tasks_shutdown_lock = asyncio.Lock()
        coordinator._initialize_connectivity_recovery()
        coordinator._client_update_pending = False
        coordinator._client_update_task = None
        coordinator._device_refresh_lock = asyncio.Lock()
        coordinator._metadata_refresh_task = None
        coordinator._metadata_shutdown_close_task = None
        coordinator.client = SimpleNamespace(
            async_get_cached_snapshot=cancellation_resistant_snapshot,
            set_update_callback=Mock(),
            async_close=AsyncMock(),
        )
        update_task = asyncio.create_task(coordinator._async_process_client_update())
        coordinator._client_update_task = update_task
        await update_started.wait()

        with patch.object(
            coordinator_module,
            "CLIENT_UPDATE_SHUTDOWN_GRACE_SECONDS",
            0.01,
        ):
            await asyncio.wait_for(
                coordinator.async_shutdown_for_home_assistant_stop(),
                timeout=0.5,
            )

        assert update_cancelled.is_set()
        assert not update_task.done()
        assert coordinator._client_update_task is update_task
        coordinator.client.async_close.assert_awaited_once_with()

        update_release.set()
        with suppress(asyncio.CancelledError):
            await update_task
        assert coordinator._client_update_task is None

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
        coordinator._base_shutdown_complete = True
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
        coordinator.client.async_close.assert_awaited_once_with()

        metadata_release.set()
        with suppress(asyncio.CancelledError):
            await metadata_task

    asyncio.run(scenario())


def test_home_assistant_stop_shares_concurrent_unload_client_close() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        close_started = asyncio.Event()
        close_release = asyncio.Event()
        close_finished = asyncio.Event()

        async def close() -> None:
            close_started.set()
            await close_release.wait()
            close_finished.set()

        coordinator._shutting_down = False
        coordinator._home_assistant_stopping = False
        coordinator._base_shutdown_complete = True
        coordinator._owned_tasks_shutdown_lock = asyncio.Lock()
        coordinator._initialize_connectivity_recovery()
        coordinator._client_update_pending = False
        coordinator._client_update_task = None
        coordinator._client_close_task = None
        coordinator._metadata_refresh_task = None
        coordinator._metadata_shutdown_close_task = None
        coordinator._batch_schedule_read_task = None
        coordinator._batch_schedule_read_tasks = set()
        coordinator.client = SimpleNamespace(
            set_update_callback=Mock(),
            async_close=AsyncMock(side_effect=close),
        )

        unload = asyncio.create_task(coordinator.async_shutdown())
        await close_started.wait()
        stop = asyncio.create_task(coordinator.async_shutdown_for_home_assistant_stop())
        await asyncio.sleep(0)

        assert not stop.done()
        assert not close_finished.is_set()

        close_release.set()
        await asyncio.wait_for(stop, timeout=1)
        await asyncio.wait_for(unload, timeout=1)

        assert close_finished.is_set()
        coordinator.client.async_close.assert_awaited_once_with()
        assert coordinator._client_close_task is None

    asyncio.run(scenario())


def test_shutdown_chains_data_update_coordinator_base_once() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        coordinator._shutting_down = False
        coordinator._home_assistant_stopping = False
        coordinator._base_shutdown_complete = False
        coordinator._owned_tasks_shutdown_lock = asyncio.Lock()
        coordinator._initialize_connectivity_recovery()
        coordinator._client_update_pending = False
        coordinator._client_update_task = None
        coordinator._client_close_task = None
        coordinator._metadata_refresh_task = None
        coordinator._metadata_shutdown_close_task = None
        coordinator._batch_schedule_read_task = None
        coordinator._batch_schedule_read_tasks = set()
        coordinator.client = SimpleNamespace(
            set_update_callback=Mock(),
            async_close=AsyncMock(),
        )

        base_shutdown = AsyncMock()
        with patch.object(
            DataUpdateCoordinator,
            "async_shutdown",
            base_shutdown,
        ):
            await coordinator.async_shutdown_for_home_assistant_stop()
            await coordinator.async_shutdown()

        base_shutdown.assert_awaited_once_with()
        coordinator.client.async_close.assert_awaited_once_with()

    asyncio.run(scenario())


def test_cancelled_unload_waiter_does_not_cancel_shared_client_close() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        close_started = asyncio.Event()
        close_release = asyncio.Event()

        async def close() -> None:
            close_started.set()
            await close_release.wait()

        coordinator._home_assistant_stopping = False
        coordinator._client_close_task = None
        coordinator.client = SimpleNamespace(async_close=AsyncMock(side_effect=close))

        first = asyncio.create_task(coordinator._async_close_client_for_unload())
        second = asyncio.create_task(coordinator._async_close_client_for_unload())
        await close_started.wait()

        first.cancel()
        with suppress(asyncio.CancelledError):
            await first

        close_task = coordinator._client_close_task
        assert close_task is not None
        assert not close_task.done()

        close_release.set()
        await asyncio.wait_for(second, timeout=1)

        coordinator.client.async_close.assert_awaited_once_with()
        assert coordinator._client_close_task is None

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

    async def refresh_map_index() -> int:
        events.append("maps")
        coordinator.app_maps = {"current_map_index": 2}
        coordinator.app_maps_refreshed_at = datetime.now(UTC)
        coordinator.app_maps_refresh_succeeded = True
        return 2

    coordinator.client.async_get_current_app_map_index = refresh_map_index
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

    async def refresh_map_index() -> int:
        coordinator.app_maps_refreshed_at = datetime.now(UTC)
        coordinator.app_maps_refresh_succeeded = True
        return 2

    coordinator.client.async_get_current_app_map_index = refresh_map_index
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
    coordinator._runtime_map_index_refreshed_at = datetime.now(UTC)
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
    coordinator._runtime_map_index_refreshed_at = datetime.now(UTC)
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
    coordinator._runtime_map_index_refreshed_at = datetime.now(UTC)
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
        "maps": [
            {
                "idx": 0,
                "area_count": 1,
                "preferences": [{"area_id": 0}],
            }
        ],
        "errors": [],
    }
    coordinator._client_update_task = Mock()
    coordinator._runtime_map_identity_verified = True
    coordinator._runtime_map_index_refreshed_at = datetime.now(UTC)
    coordinator._last_device_settings_event_at = None
    coordinator._last_mowing_preferences_event_at = None
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [{"idx": 0}],
    }
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
        map_slot_index_hints=[0],
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
    coordinator._runtime_map_index_refreshed_at = datetime.now(UTC)
    coordinator._last_device_settings_event_at = None
    coordinator._last_mowing_preferences_event_at = None
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator.app_maps = {"current_map_index": 0}
    cached_preferences = {
        "available": True,
        "maps": [{"idx": 0, "preferences": [{"area_id": 0}]}],
        "errors": [],
    }
    coordinator.batch_device_data = {
        "batch_mowing_preferences": cached_preferences,
    }
    coordinator.batch_device_data_refreshed_at = datetime.now(UTC)
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
                {
                    "available": True,
                    "maps": [
                        {
                            "idx": 0,
                            "area_count": 1,
                            "preferences": [{"area_id": 0}],
                        }
                    ],
                    "errors": [],
                },
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
        assert coordinator.batch_device_data["batch_mowing_preferences"] is (
            cached_preferences
        )
        assert coordinator.batch_device_data_refreshed_at is None
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
    coordinator._runtime_map_index_refreshed_at = datetime.now(UTC)
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
    coordinator._runtime_map_index_refreshed_at = datetime.now(UTC)
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
    coordinator._runtime_map_index_refreshed_at = datetime.now(UTC)
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
    coordinator._runtime_map_index_refreshed_at = datetime.now(UTC)
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
    coordinator._runtime_map_index_refreshed_at = datetime.now(UTC)
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
        coordinator._runtime_map_index_refreshed_at = datetime.now(UTC)
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
        coordinator._runtime_map_index_refreshed_at = datetime.now(UTC)
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


def test_failed_preference_verification_still_reconciles_coordinator_state() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = retain_confirmed_preference_write(
        [],
        {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 1,
            "area_id": None,
            "changed_fields": ["preference_mode"],
            "readback": {
                "map": {"idx": 1, "mode": 0, "mode_name": "global"},
                "preference": None,
            },
        },
        confirmed_at=datetime.now(UTC),
    )
    coordinator.last_preference_write_result = None
    coordinator.batch_device_data_refreshed_at = datetime.now(UTC)
    coordinator.async_update_listeners = Mock()
    coordinator.async_refresh_batch_device_data = AsyncMock(return_value={})
    coordinator.async_request_refresh = AsyncMock()
    attempted_error = RuntimeError("readback did not confirm")
    mark_write_attempted(attempted_error, fields=["preference_mode"])
    coordinator.client = SimpleNamespace(
        async_plan_app_mowing_preference_update=AsyncMock(side_effect=attempted_error)
    )

    with pytest.raises(RuntimeError, match="readback did not confirm"):
        asyncio.run(
            coordinator.async_plan_mowing_preference_update(
                map_index=1,
                area_id=None,
                changes={"preference_mode": "custom"},
                execute=True,
                confirm_write=True,
            )
        )

    assert coordinator.batch_device_data_refreshed_at is None
    coordinator.async_refresh_batch_device_data.assert_awaited_once_with(
        force=True,
        source="mowing_preference_write",
    )
    coordinator.async_request_refresh.assert_awaited_once_with()
    coordinator.async_update_listeners.assert_called_once_with()
    assert coordinator._pending_preference_confirmations == []


def test_failed_preference_plan_preserves_confirmation_without_write_attempt() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = retain_confirmed_preference_write(
        [],
        {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 1,
            "area_id": None,
            "changed_fields": ["preference_mode"],
            "readback": {
                "map": {"idx": 1, "mode": 0, "mode_name": "global"},
                "preference": None,
            },
        },
        confirmed_at=datetime.now(UTC),
    )
    original_confirmations = coordinator._pending_preference_confirmations
    coordinator.last_preference_write_result = None
    coordinator.async_update_listeners = Mock()
    coordinator.async_refresh_batch_device_data = AsyncMock(return_value={})
    coordinator.async_request_refresh = AsyncMock()
    coordinator.client = SimpleNamespace(
        async_plan_app_mowing_preference_update=AsyncMock(
            side_effect=ValueError("invalid preference value")
        )
    )

    with pytest.raises(ValueError, match="invalid preference value"):
        asyncio.run(
            coordinator.async_plan_mowing_preference_update(
                map_index=1,
                area_id=None,
                changes={"preference_mode": "invalid"},
                execute=True,
                confirm_write=True,
            )
        )

    assert coordinator._pending_preference_confirmations is original_confirmations
    coordinator.async_refresh_batch_device_data.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()
    coordinator.async_update_listeners.assert_not_called()


def test_failed_preference_sequence_invalidates_only_attempted_fields() -> None:
    confirmed_at = datetime.now(UTC)
    pending = retain_confirmed_preference_write(
        [],
        {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 0,
            "area_id": None,
            "changed_fields": ["preference_mode"],
            "readback": {
                "map": {"idx": 0, "mode": 0, "mode_name": "global"},
                "preference": None,
            },
        },
        confirmed_at=confirmed_at,
    )
    pending = retain_confirmed_preference_write(
        pending,
        {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 0,
            "area_id": 1,
            "changed_fields": ["mowing_height_cm"],
            "readback": {
                "map": {"idx": 0, "mode": 0, "mode_name": "global"},
                "preference": {"area_id": 1, "mowing_height_cm": 7.0},
            },
        },
        confirmed_at=confirmed_at + timedelta(seconds=1),
    )
    attempted_error = RuntimeError("mode write failed")
    mark_write_attempted(attempted_error, fields=["preference_mode"])
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = pending
    coordinator.last_preference_write_result = None
    coordinator.async_update_listeners = Mock()
    coordinator.async_refresh_batch_device_data = AsyncMock(return_value={})
    coordinator.async_request_refresh = AsyncMock()
    coordinator.client = SimpleNamespace(
        async_plan_app_mowing_preference_update=AsyncMock(side_effect=attempted_error)
    )

    with pytest.raises(RuntimeError, match="mode write failed"):
        asyncio.run(
            coordinator.async_plan_mowing_preference_update(
                map_index=0,
                area_id=1,
                changes={"preference_mode": "custom", "mowing_height_cm": 5.0},
                execute=True,
                confirm_write=True,
            )
        )

    assert [item.field for item in coordinator._pending_preference_confirmations] == [
        "mowing_height_cm"
    ]
    coordinator.async_refresh_batch_device_data.assert_awaited_once_with(
        force=True,
        source="mowing_preference_write",
    )


def _coordinator_for_confirmed_preference_write(
    *,
    batch_device_data,
    confirmed,
):
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = []
    coordinator.last_preference_write_result = None
    coordinator.batch_device_data = batch_device_data
    coordinator.batch_device_data_refreshed_at = datetime.now(UTC)
    coordinator.async_update_listeners = Mock()
    coordinator.async_refresh_batch_device_data = AsyncMock(return_value={})
    coordinator.async_request_refresh = AsyncMock()
    coordinator.client = SimpleNamespace(
        descriptor=SimpleNamespace(model="dreame.mower.g2408"),
        async_plan_app_mowing_preference_update=AsyncMock(return_value=confirmed),
    )
    return coordinator


@pytest.mark.parametrize(
    ("model", "display_model"),
    [
        ("mova.mower.g2583", None),
        ("mova.mower.unknown", "Viax 300"),
    ],
)
def test_coordinator_rejects_manual_height_write_before_client_boundary(
    model: str, display_model: str | None
) -> None:
    coordinator = _coordinator_for_confirmed_preference_write(
        batch_device_data=None,
        confirmed={},
    )
    coordinator.client.descriptor.model = model
    coordinator.client.descriptor.display_model = display_model

    with pytest.raises(HomeAssistantError, match="adjusted manually"):
        asyncio.run(
            coordinator.async_plan_mowing_preference_update(
                map_index=0,
                area_id=1,
                changes={"mowing_height_cm": 4.5},
                execute=True,
                confirm_write=True,
            )
        )

    coordinator.client.async_plan_app_mowing_preference_update.assert_not_awaited()


def test_confirmed_preference_mode_readback_wins_over_stale_batch_cache() -> None:
    stale_batch = {
        "captured_at": "before-write",
        "source": "batch_device_data",
        "batch_mowing_preferences": {
            "available": True,
            "maps": [
                {
                    "idx": 0,
                    "mode": 1,
                    "mode_name": "custom",
                    "preferences": [{"area_id": 0, "mowing_height_cm": 6.0}],
                }
            ],
        },
    }
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 0,
        "area_id": None,
        "changed_fields": ["preference_mode"],
        "readback": {
            "map": {"idx": 0, "mode": 0, "mode_name": "global"},
            "preference": None,
        },
    }
    coordinator = _coordinator_for_confirmed_preference_write(
        batch_device_data=stale_batch,
        confirmed=confirmed,
    )

    result = asyncio.run(
        coordinator.async_plan_mowing_preference_update(
            map_index=0,
            area_id=None,
            changes={"preference_mode": "global"},
            execute=True,
            confirm_write=True,
        )
    )

    assert result is confirmed
    assert coordinator.last_preference_write_result is confirmed
    assert coordinator.batch_device_data is not stale_batch
    assert stale_batch["batch_mowing_preferences"]["maps"][0]["mode"] == 1
    assert coordinator.batch_device_data["source"] == (
        "mowing_preference_write_readback"
    )
    reconciled_map = coordinator.batch_device_data["batch_mowing_preferences"]["maps"][
        0
    ]
    assert reconciled_map["mode"] == 0
    assert reconciled_map["mode_name"] == "global"
    assert coordinator.batch_device_data_refreshed_at is None
    coordinator.async_refresh_batch_device_data.assert_not_awaited()
    coordinator.async_request_refresh.assert_awaited_once_with()
    coordinator.async_update_listeners.assert_called_once_with()


def test_confirmed_preference_setting_readback_updates_only_target_area() -> None:
    batch_device_data = {
        "batch_mowing_preferences": {
            "available": True,
            "maps": [
                {
                    "idx": 0,
                    "available": True,
                    "area_count": 2,
                    "mode": 1,
                    "mode_name": "custom",
                    "preferences": [
                        {
                            "area_id": 1,
                            "mowing_height_cm": 6.0,
                            "obstacle_avoidance_sensitivity": 1,
                        },
                        {"area_id": 2, "mowing_height_cm": 5.0},
                    ],
                }
            ],
        }
    }
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 0,
        "area_id": 1,
        "changed_fields": [
            "mowing_height_cm",
            "obstacle_avoidance_sensitivity",
        ],
        "readback": {
            "map": {"idx": 0, "mode": 1, "mode_name": "custom"},
            "preference": {
                "map_index": 0,
                "area_id": 1,
                "reported_version": 51,
                "mowing_height_cm": 7.0,
                "obstacle_avoidance_sensitivity": 2,
            },
        },
    }
    coordinator = _coordinator_for_confirmed_preference_write(
        batch_device_data=batch_device_data,
        confirmed=confirmed,
    )

    asyncio.run(
        coordinator.async_plan_mowing_preference_update(
            map_index=0,
            area_id=1,
            changes={
                "mowing_height_cm": 7.0,
                "obstacle_avoidance_sensitivity": 2,
            },
            execute=True,
            confirm_write=True,
        )
    )

    preferences = coordinator.batch_device_data["batch_mowing_preferences"]["maps"][0][
        "preferences"
    ]
    assert preferences[0]["mowing_height_cm"] == 7.0
    assert preferences[0]["obstacle_avoidance_sensitivity"] == 2
    assert preferences[0]["reported_version"] == 51
    assert preferences[1] == {"area_id": 2, "mowing_height_cm": 5.0}
    coordinator.async_refresh_batch_device_data.assert_not_awaited()


def test_inflight_batch_read_cannot_replace_confirmed_preference_cache() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        batch_schedule = {"available": True, "schedules": []}
        coordinator.schedules = batch_schedule
        coordinator.batch_device_data = None
        coordinator.batch_device_data_refreshed_at = None
        coordinator._schedule_cache_generation = 0
        coordinator._pending_preference_confirmations = []
        coordinator.async_request_refresh = AsyncMock()
        coordinator.async_update_listeners = Mock()
        read_started = asyncio.Event()
        release_read = asyncio.Event()
        read_count = 0

        async def fetch_batch_device_data(**_kwargs):
            nonlocal read_count
            read_count += 1
            if read_count == 1:
                read_started.set()
                await release_read.wait()
            preferences = {
                "available": True,
                "maps": [
                    {
                        "idx": 0,
                        "mode": 1,
                        "mode_name": "custom",
                        "preferences": [],
                    }
                ],
            }
            return (
                batch_schedule,
                preferences,
                {"available": True},
                0,
                None,
                preferences,
            )

        coordinator._async_fetch_batch_device_data = fetch_batch_device_data
        refresh = asyncio.create_task(
            coordinator.async_refresh_batch_device_data(
                force=True,
                source="background_before_write",
            )
        )
        await read_started.wait()
        confirmed = {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 0,
            "area_id": None,
            "changed_fields": ["preference_mode"],
            "readback": {
                "map": {"idx": 0, "mode": 0, "mode_name": "global"},
                "preference": None,
            },
        }
        await coordinator._async_reconcile_mowing_preference_write(
            confirmed_result=confirmed,
        )
        assert read_count == 2
        release_read.set()

        result = await refresh

        assert result is not None
        assert result["batch_mowing_preferences"]["maps"][0]["mode_name"] == ("global")

    asyncio.run(scenario())


def test_direct_read_started_before_write_cannot_supersede_confirmation() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        coordinator.schedules = {"available": True, "schedules": []}
        old_preferences = {
            "source": "app_action_mowing_preferences",
            "available": True,
            "errors": [],
            "maps": [
                {
                    "idx": 0,
                    "mode": 1,
                    "mode_name": "custom",
                    "preferences": [
                        {
                            "map_index": 0,
                            "area_id": 1,
                            "version": 160,
                            "mowing_height_cm": 5.5,
                        }
                    ],
                }
            ],
        }
        coordinator.batch_device_data = {"batch_mowing_preferences": old_preferences}
        coordinator.batch_device_data_refreshed_at = datetime.now(UTC)
        coordinator._schedule_cache_generation = 0
        coordinator._pending_preference_confirmations = []
        coordinator.async_request_refresh = AsyncMock()
        coordinator.async_update_listeners = Mock()
        read_started = asyncio.Event()
        release_read = asyncio.Event()

        async def fetch_batch_device_data(**_kwargs):
            read_started.set()
            await release_read.wait()
            return (
                coordinator.schedules,
                old_preferences,
                {"available": True},
                0,
                old_preferences,
                None,
            )

        coordinator._async_fetch_batch_device_data = fetch_batch_device_data
        refresh = asyncio.create_task(
            coordinator.async_refresh_batch_device_data(force=True)
        )
        await read_started.wait()
        confirmed = {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 0,
            "area_id": 1,
            "changed_fields": ["mowing_height_cm"],
            "readback": {
                "map": {"idx": 0, "mode": 1, "mode_name": "custom"},
                "preference": {
                    "map_index": 0,
                    "area_id": 1,
                    "version": 164,
                    "mowing_height_cm": 4.5,
                },
            },
        }

        await coordinator._async_reconcile_mowing_preference_write(
            confirmed_result=confirmed
        )
        release_read.set()
        result = await refresh

        preference = result["batch_mowing_preferences"]["maps"][0]["preferences"][0]
        assert preference["mowing_height_cm"] == 4.5
        assert preference["version"] == 164
        assert coordinator._pending_preference_confirmations

    asyncio.run(scenario())


def test_matching_read_does_not_unprotect_older_inflight_batch() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        batch_schedule = {"available": True, "schedules": []}
        coordinator.schedules = batch_schedule
        coordinator.batch_device_data = None
        coordinator.batch_device_data_refreshed_at = None
        coordinator._schedule_cache_generation = 0
        confirmed = {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 0,
            "area_id": None,
            "changed_fields": ["preference_mode"],
            "readback": {
                "map": {"idx": 0, "mode": 0, "mode_name": "global"},
                "preference": None,
            },
        }
        coordinator._pending_preference_confirmations = (
            retain_confirmed_preference_write(
                [],
                confirmed,
                confirmed_at=datetime.now(UTC),
            )
        )
        older_read_started = asyncio.Event()
        release_older_read = asyncio.Event()
        read_count = 0

        async def fetch_batch_device_data(**_kwargs):
            nonlocal read_count
            read_count += 1
            mode = 1
            if read_count == 1:
                older_read_started.set()
                await release_older_read.wait()
            else:
                mode = 0
            preferences = {
                "available": True,
                "maps": [
                    {
                        "idx": 0,
                        "mode": mode,
                        "mode_name": "global" if mode == 0 else "custom",
                        "preferences": [],
                    }
                ],
            }
            return (
                batch_schedule,
                preferences,
                {"available": True},
                0,
                None,
                preferences,
            )

        coordinator._async_fetch_batch_device_data = fetch_batch_device_data
        older = asyncio.create_task(
            coordinator.async_refresh_batch_device_data(force=True)
        )
        await older_read_started.wait()
        matching = await coordinator.async_refresh_batch_device_data(force=True)

        assert matching is not None
        assert matching["batch_mowing_preferences"]["maps"][0]["mode"] == 0
        assert coordinator._pending_preference_confirmations

        release_older_read.set()
        older_result = await older

        assert older_result is not None
        assert older_result["batch_mowing_preferences"]["maps"][0]["mode"] == 0
        assert coordinator._pending_preference_confirmations

    asyncio.run(scenario())


def test_superseded_preference_read_still_publishes_schedule_and_ota() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        old_preferences = {
            "available": True,
            "maps": [
                {
                    "idx": 0,
                    "mode": 0,
                    "mode_name": "global",
                    "area_count": 1,
                    "preferences": [{"area_id": 0, "mowing_height_cm": 5.5}],
                }
            ],
        }
        current_preferences = {
            "available": True,
            "errors": [],
            "maps": [
                {
                    "idx": 0,
                    "mode": 0,
                    "mode_name": "global",
                    "area_count": 1,
                    "preferences": [
                        {
                            "area_id": 0,
                            "version": 1,
                            "reported_version": 1,
                            "mowing_height_cm": 4.5,
                        }
                    ],
                }
            ],
        }
        old_schedule = {"available": True, "version": 1, "schedules": []}
        new_schedule = {"available": True, "version": 2, "schedules": []}
        coordinator.schedules = new_schedule
        coordinator.batch_device_data = {
            "batch_schedule": old_schedule,
            "batch_mowing_preferences": old_preferences,
            "batch_ota_info": {"available": True, "version": "1.0.0"},
        }
        coordinator.batch_device_data_refreshed_at = None
        coordinator._schedule_cache_generation = 0
        coordinator._preference_write_lock = asyncio.Lock()
        coordinator._pending_preference_confirmations = []
        coordinator.app_maps = {
            "map_list_valid": True,
            "current_map_index": 0,
            "maps": [{"idx": 0}],
        }
        coordinator.client = SimpleNamespace(
            async_get_mowing_preferences=AsyncMock(return_value=current_preferences),
            async_get_batch_mowing_preferences=AsyncMock(),
        )
        batch_started = asyncio.Event()
        release_batch = asyncio.Event()

        async def fetch_batch_device_data(**_kwargs):
            batch_started.set()
            await release_batch.wait()
            return (
                new_schedule,
                old_preferences,
                {"available": True, "version": "2.0.0"},
                0,
                None,
                old_preferences,
            )

        coordinator._async_fetch_batch_device_data = fetch_batch_device_data
        batch_refresh = asyncio.create_task(
            coordinator.async_refresh_batch_device_data(force=True)
        )
        await batch_started.wait()

        realtime_result = await coordinator.async_refresh_mowing_preferences(
            source="preference_event"
        )
        assert realtime_result is current_preferences

        release_batch.set()
        result = await batch_refresh

        assert result is not None
        assert result["batch_schedule"] is new_schedule
        assert result["batch_ota_info"] == {
            "available": True,
            "version": "2.0.0",
        }
        assert result["batch_mowing_preferences"] == current_preferences
        assert coordinator.batch_device_data is result

    asyncio.run(scenario())


def test_superseded_complete_batch_preserves_partial_retry_invalidation() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        complete_preferences = {
            "available": True,
            "errors": [],
            "maps": [
                {
                    "idx": 0,
                    "mode": 1,
                    "mode_name": "custom",
                    "area_count": 1,
                    "preferences": [{"area_id": 1, "mowing_height_cm": 5.0}],
                },
                {
                    "idx": 1,
                    "mode": 0,
                    "mode_name": "global",
                    "area_count": 1,
                    "preferences": [{"area_id": 0, "mowing_height_cm": 6.0}],
                },
            ],
        }
        partial_direct = {
            "available": True,
            "errors": [{"idx": 1, "stage": "preferences", "error": "unavailable"}],
            "maps": [
                {
                    "idx": 0,
                    "mode": 1,
                    "mode_name": "custom",
                    "area_count": 1,
                    "preferences": [{"area_id": 1, "mowing_height_cm": 4.5}],
                },
                {"idx": 1, "error": "unavailable", "preferences": []},
            ],
        }
        partial_batch = {
            "available": True,
            "errors": [],
            "maps": [
                {
                    "idx": 0,
                    "mode": 1,
                    "mode_name": "custom",
                    "area_count": 1,
                    "preferences": [{"area_id": 1, "mowing_height_cm": 4.75}],
                }
            ],
        }
        schedule = {"available": True, "schedules": []}
        coordinator.schedules = schedule
        coordinator.batch_device_data = {
            "batch_schedule": schedule,
            "batch_mowing_preferences": complete_preferences,
            "batch_ota_info": {"available": True},
        }
        coordinator.batch_device_data_refreshed_at = None
        coordinator._schedule_cache_generation = 0
        coordinator._preference_write_lock = asyncio.Lock()
        coordinator._pending_preference_confirmations = []
        coordinator.async_update_listeners = Mock()
        coordinator.app_maps = {
            "map_list_valid": True,
            "current_map_index": 0,
            "maps": [{"idx": 0}, {"idx": 1}],
        }
        coordinator.client = SimpleNamespace(
            async_get_mowing_preferences=AsyncMock(return_value=partial_direct),
            async_get_batch_mowing_preferences=AsyncMock(return_value=partial_batch),
        )
        batch_started = asyncio.Event()
        release_batch = asyncio.Event()

        async def fetch_batch_device_data(**_kwargs):
            batch_started.set()
            await release_batch.wait()
            return (
                schedule,
                complete_preferences,
                {"available": True},
                0,
                None,
                complete_preferences,
            )

        coordinator._async_fetch_batch_device_data = fetch_batch_device_data
        older_refresh = asyncio.create_task(
            coordinator.async_refresh_batch_device_data(force=True)
        )
        await batch_started.wait()

        event_result = await coordinator.async_refresh_mowing_preferences(
            source="preference_event"
        )
        assert event_result is None
        assert coordinator.batch_device_data_refreshed_at is None
        coordinator.async_update_listeners.assert_called_once_with()

        release_batch.set()
        result = await older_refresh

        assert result is not None
        assert coordinator.batch_device_data_refreshed_at is None
        maps = result["batch_mowing_preferences"]["maps"]
        assert maps[0]["preferences"][0]["mowing_height_cm"] == 4.5
        assert maps[1]["preferences"][0]["mowing_height_cm"] == 6.0

    asyncio.run(scenario())


def test_superseded_event_stays_retryable_after_newer_partial_batch() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        complete = {
            "available": True,
            "errors": [],
            "maps": [
                {
                    "idx": index,
                    "mode": 1,
                    "mode_name": "custom",
                    "area_count": 1,
                    "preferences": [
                        {
                            "area_id": index + 1,
                            "version": 10,
                            "reported_version": 10,
                        }
                    ],
                }
                for index in (0, 1)
            ],
        }
        partial = {
            "available": True,
            "errors": [{"idx": 1, "stage": "preferences", "error": "unavailable"}],
            "maps": [complete["maps"][0], {"idx": 1, "error": "unavailable"}],
        }
        schedule = {"available": True, "schedules": []}
        coordinator.schedules = schedule
        coordinator.batch_device_data = {"batch_mowing_preferences": complete}
        coordinator.batch_device_data_refreshed_at = datetime.now(UTC)
        coordinator._schedule_cache_generation = 0
        coordinator._preference_write_lock = asyncio.Lock()
        coordinator._pending_preference_confirmations = []
        coordinator.app_maps = {
            "map_list_valid": True,
            "current_map_index": 0,
            "maps": [{"idx": 0}, {"idx": 1}],
        }
        event_started = asyncio.Event()
        release_event = asyncio.Event()

        async def read_direct(**_kwargs):
            event_started.set()
            await release_event.wait()
            return complete

        coordinator.client = SimpleNamespace(
            async_get_mowing_preferences=read_direct,
            async_get_batch_mowing_preferences=AsyncMock(),
        )
        coordinator._async_fetch_batch_device_data = AsyncMock(
            return_value=(schedule, partial, {"available": True}, 0, partial, partial)
        )

        event_refresh = asyncio.create_task(
            coordinator.async_refresh_mowing_preferences(source="preference_event")
        )
        await event_started.wait()
        batch_result = await coordinator.async_refresh_batch_device_data(force=True)
        assert batch_result is not None
        assert coordinator._published_preference_read_complete is False

        release_event.set()
        event_result = await event_refresh

        assert event_result is None
        assert coordinator.batch_device_data_refreshed_at is None

    asyncio.run(scenario())


def test_older_successful_preference_read_publishes_when_newer_read_fails() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        old_preferences = {
            "available": True,
            "maps": [
                {
                    "idx": 0,
                    "mode": 0,
                    "mode_name": "global",
                    "preferences": [{"area_id": 0, "mowing_height_cm": 5.5}],
                }
            ],
        }
        fresh_preferences = {
            "available": True,
            "maps": [
                {
                    "idx": 0,
                    "mode": 0,
                    "mode_name": "global",
                    "preferences": [{"area_id": 0, "mowing_height_cm": 4.5}],
                }
            ],
        }
        schedule = {"available": True, "schedules": []}
        unavailable = {"available": False, "maps": [], "errors": []}
        coordinator.schedules = schedule
        coordinator.batch_device_data = {
            "batch_schedule": schedule,
            "batch_mowing_preferences": old_preferences,
            "batch_ota_info": {"available": True},
        }
        coordinator.batch_device_data_refreshed_at = None
        coordinator._schedule_cache_generation = 0
        coordinator._preference_write_lock = asyncio.Lock()
        coordinator._pending_preference_confirmations = []
        coordinator.app_maps = {
            "map_list_valid": True,
            "current_map_index": 0,
            "maps": [{"idx": 0}],
        }
        coordinator.client = SimpleNamespace(
            async_get_mowing_preferences=AsyncMock(return_value=unavailable),
            async_get_batch_mowing_preferences=AsyncMock(return_value=unavailable),
        )
        batch_started = asyncio.Event()
        release_batch = asyncio.Event()

        async def fetch_batch_device_data(**_kwargs):
            batch_started.set()
            await release_batch.wait()
            return (
                schedule,
                fresh_preferences,
                {"available": True},
                0,
                None,
                fresh_preferences,
            )

        coordinator._async_fetch_batch_device_data = fetch_batch_device_data
        older_refresh = asyncio.create_task(
            coordinator.async_refresh_batch_device_data(force=True)
        )
        await batch_started.wait()

        failed_result = await coordinator.async_refresh_mowing_preferences(
            source="preference_event"
        )
        assert failed_result is None
        assert coordinator.batch_device_data["batch_mowing_preferences"] is (
            old_preferences
        )
        assert coordinator.batch_device_data_refreshed_at is None

        release_batch.set()
        result = await older_refresh

        assert result is not None
        assert result["batch_mowing_preferences"] == fresh_preferences
        assert coordinator.batch_device_data is result

    asyncio.run(scenario())


def test_failed_direct_and_batch_reads_retain_cache_without_marking_it_fresh() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        schedule = {"available": True, "schedules": []}
        cached_preferences = {
            "available": True,
            "maps": [
                {
                    "idx": 0,
                    "mode": 0,
                    "mode_name": "global",
                    "preferences": [{"area_id": 0, "mowing_height_cm": 4.5}],
                }
            ],
        }
        refreshed_at = datetime.now(UTC) - timedelta(minutes=16)
        coordinator.schedules = schedule
        coordinator._fresh_batch_schedule = lambda: schedule
        coordinator._published_schedule_read_generation = 0
        coordinator.batch_device_data = {
            "batch_schedule": schedule,
            "batch_mowing_preferences": cached_preferences,
            "batch_ota_info": {"available": True, "version": "1.0.0"},
        }
        coordinator.batch_device_data_refreshed_at = refreshed_at
        coordinator._schedule_cache_generation = 0
        coordinator._pending_preference_confirmations = []
        coordinator.app_maps = {
            "map_list_valid": True,
            "current_map_index": 0,
            "maps": [{"idx": 0}],
        }
        coordinator.client = SimpleNamespace(
            async_get_mowing_preferences=AsyncMock(
                side_effect=RuntimeError("direct unavailable")
            ),
            async_get_batch_mowing_preferences=AsyncMock(
                side_effect=RuntimeError("batch unavailable")
            ),
            async_get_batch_ota_info=AsyncMock(
                return_value={"available": True, "version": "2.0.0"}
            ),
        )

        result = await coordinator.async_refresh_batch_device_data(source="batch_retry")

        assert result is not None
        assert result["batch_schedule"] is schedule
        assert result["batch_ota_info"] == {
            "available": True,
            "version": "2.0.0",
        }
        assert result["batch_mowing_preferences"] == cached_preferences
        assert coordinator.batch_device_data_refreshed_at is None
        coordinator.client.async_get_mowing_preferences.assert_awaited_once_with(
            include_raw=False,
            map_indices=[0],
        )
        coordinator.client.async_get_batch_mowing_preferences.assert_awaited_once_with(
            include_raw=False,
            map_index_hints=[0],
            map_slot_index_hints=[0],
        )

    asyncio.run(scenario())


def test_pending_confirmation_with_failed_batch_read_retries() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        schedule = {"available": True, "schedules": []}
        preferences = {
            "source": "app_action_mowing_preferences",
            "available": True,
            "errors": [],
            "maps": [
                {
                    "idx": 0,
                    "mode": 1,
                    "mode_name": "custom",
                    "area_count": 1,
                    "preferences": [
                        {
                            "map_index": 0,
                            "area_id": 1,
                            "version": 164,
                            "mowing_height_cm": 4.5,
                        }
                    ],
                }
            ],
        }
        confirmed = {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 0,
            "area_id": 1,
            "changed_fields": ["mowing_height_cm"],
            "readback": {
                "map": {"idx": 0, "mode": 1, "mode_name": "custom"},
                "preference": {
                    "map_index": 0,
                    "area_id": 1,
                    "version": 164,
                    "mowing_height_cm": 4.5,
                },
            },
        }
        refreshed_at = datetime.now(UTC) - timedelta(minutes=16)
        coordinator.schedules = schedule
        coordinator._fresh_batch_schedule = lambda: schedule
        coordinator._published_schedule_read_generation = 0
        coordinator.batch_device_data = {
            "batch_schedule": schedule,
            "batch_mowing_preferences": preferences,
            "batch_ota_info": {"available": True},
        }
        coordinator.batch_device_data_refreshed_at = refreshed_at
        coordinator._schedule_cache_generation = 0
        coordinator._pending_preference_confirmations = (
            retain_confirmed_preference_write(
                [],
                confirmed,
                confirmed_at=datetime.now(UTC),
            )
        )
        coordinator.app_maps = {
            "map_list_valid": True,
            "current_map_index": 0,
            "maps": [{"idx": 0}],
        }
        coordinator.client = SimpleNamespace(
            async_get_mowing_preferences=AsyncMock(return_value=preferences),
            async_get_batch_mowing_preferences=AsyncMock(
                side_effect=RuntimeError("batch unavailable")
            ),
            async_get_batch_ota_info=AsyncMock(return_value={"available": True}),
        )

        result = await coordinator.async_refresh_batch_device_data(
            source="pending_confirmation_retry"
        )

        assert result is not None
        assert result["batch_mowing_preferences"]["maps"] == preferences["maps"]
        assert coordinator._pending_preference_confirmations
        assert coordinator.batch_device_data_refreshed_at is None
        coordinator.client.async_get_batch_mowing_preferences.assert_awaited_once_with(
            include_raw=False,
            map_index_hints=[0],
            map_slot_index_hints=[0],
        )

    asyncio.run(scenario())


def test_pending_confirmation_with_complete_stale_batch_read_retries() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        schedule = {"available": True, "schedules": []}
        direct = {
            "source": "app_action_mowing_preferences",
            "available": True,
            "errors": [],
            "maps": [
                {
                    "idx": 0,
                    "mode": 1,
                    "mode_name": "custom",
                    "area_count": 1,
                    "preferences": [
                        {
                            "map_index": 0,
                            "area_id": 1,
                            "version": 164,
                            "mowing_height_cm": 4.5,
                        }
                    ],
                }
            ],
        }
        stale_batch = {
            "source": "batch_device_data_mowing_preferences",
            "available": True,
            "errors": [],
            "maps": [
                {
                    "idx": 0,
                    "mode": 1,
                    "mode_name": "custom",
                    "area_count": 1,
                    "preferences": [
                        {
                            "map_index": 0,
                            "area_id": 1,
                            "version": 160,
                            "mowing_height_cm": 5.0,
                        }
                    ],
                }
            ],
        }
        confirmed = {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 0,
            "area_id": 1,
            "changed_fields": ["mowing_height_cm"],
            "readback": {
                "map": {"idx": 0, "mode": 1, "mode_name": "custom"},
                "preference": {
                    "map_index": 0,
                    "area_id": 1,
                    "version": 164,
                    "mowing_height_cm": 4.5,
                },
            },
        }
        refreshed_at = datetime.now(UTC) - timedelta(minutes=16)
        coordinator.schedules = schedule
        coordinator._fresh_batch_schedule = lambda: schedule
        coordinator._published_schedule_read_generation = 0
        coordinator.batch_device_data = {
            "batch_schedule": schedule,
            "batch_mowing_preferences": direct,
            "batch_ota_info": {"available": True},
        }
        coordinator.batch_device_data_refreshed_at = refreshed_at
        coordinator._schedule_cache_generation = 0
        coordinator._pending_preference_confirmations = (
            retain_confirmed_preference_write(
                [],
                confirmed,
                confirmed_at=datetime.now(UTC),
            )
        )
        coordinator.app_maps = {
            "map_list_valid": True,
            "current_map_index": 0,
            "maps": [{"idx": 0}],
        }
        coordinator.client = SimpleNamespace(
            async_get_mowing_preferences=AsyncMock(return_value=direct),
            async_get_batch_mowing_preferences=AsyncMock(return_value=stale_batch),
            async_get_batch_ota_info=AsyncMock(return_value={"available": True}),
        )

        result = await coordinator.async_refresh_batch_device_data(
            source="pending_confirmation_retry"
        )

        assert result is not None
        assert result["batch_mowing_preferences"]["maps"] == direct["maps"]
        assert coordinator._pending_preference_confirmations
        assert coordinator.batch_device_data_refreshed_at is None

    asyncio.run(scenario())


def test_confirmed_preference_readback_without_cache_uses_batch_fallback() -> None:
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 0,
        "area_id": None,
        "changed_fields": ["preference_mode"],
        "readback": {
            "map": {"idx": 0, "mode": 0, "mode_name": "global"},
            "preference": None,
        },
    }
    coordinator = _coordinator_for_confirmed_preference_write(
        batch_device_data=None,
        confirmed=confirmed,
    )

    asyncio.run(
        coordinator.async_plan_mowing_preference_update(
            map_index=0,
            area_id=None,
            changes={"preference_mode": "global"},
            execute=True,
            confirm_write=True,
        )
    )

    coordinator.async_refresh_batch_device_data.assert_awaited_once_with(
        force=True,
        source="mowing_preference_write",
    )
    coordinator.async_request_refresh.assert_awaited_once_with()
    coordinator.async_update_listeners.assert_called_once_with()


def test_confirmed_preference_readback_with_invalid_cache_uses_batch_fallback() -> None:
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 0,
        "area_id": None,
        "changed_fields": ["preference_mode"],
        "readback": {
            "map": {"idx": 0, "mode": 0, "mode_name": "global"},
            "preference": None,
        },
    }
    coordinator = _coordinator_for_confirmed_preference_write(
        batch_device_data={
            "batch_mowing_preferences": {
                "available": False,
                "errors": ["partial read"],
                "maps": [
                    {
                        "idx": 0,
                        "mode": 1,
                        "mode_name": "custom",
                        "preferences": [],
                    }
                ],
            }
        },
        confirmed=confirmed,
    )

    asyncio.run(
        coordinator.async_plan_mowing_preference_update(
            map_index=0,
            area_id=None,
            changes={"preference_mode": "global"},
            execute=True,
            confirm_write=True,
        )
    )

    coordinator.async_refresh_batch_device_data.assert_awaited_once_with(
        force=True,
        source="mowing_preference_write",
    )
    assert coordinator._pending_preference_confirmations


def test_confirmed_preference_readback_with_missing_map_uses_batch_fallback() -> None:
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 0,
        "area_id": None,
        "changed_fields": ["preference_mode"],
        "readback": {
            "map": {"idx": 0, "mode": 0, "mode_name": "global"},
            "preference": None,
        },
    }
    coordinator = _coordinator_for_confirmed_preference_write(
        batch_device_data={
            "batch_mowing_preferences": {
                "available": True,
                "errors": [],
                "maps": [
                    {
                        "idx": 1,
                        "mode": 1,
                        "mode_name": "custom",
                        "preferences": [],
                    }
                ],
            }
        },
        confirmed=confirmed,
    )

    asyncio.run(
        coordinator.async_plan_mowing_preference_update(
            map_index=0,
            area_id=None,
            changes={"preference_mode": "global"},
            execute=True,
            confirm_write=True,
        )
    )

    coordinator.async_refresh_batch_device_data.assert_awaited_once_with(
        force=True,
        source="mowing_preference_write",
    )


def test_stale_batch_missing_area_retains_only_current_confirmed_target() -> None:
    confirmed_at = datetime.now(UTC)
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 0,
        "area_id": 1,
        "changed_fields": ["mowing_height_cm"],
        "readback": {
            "map": {"idx": 0, "mode": 1, "mode_name": "custom"},
            "preference": {
                "map_index": 0,
                "area_id": 1,
                "mowing_height_cm": 7.0,
            },
        },
    }
    pending = retain_confirmed_preference_write(
        [],
        confirmed,
        confirmed_at=confirmed_at,
    )
    incoming = {
        "available": True,
        "maps": [
            {
                "idx": 0,
                "available": True,
                "area_count": 1,
                "mode": 1,
                "mode_name": "custom",
                "preferences": [{"area_id": 2, "mowing_height_cm": 6.0}],
            }
        ],
    }

    result, remaining = reconcile_pending_preference_readbacks(
        incoming,
        pending,
    )

    areas = result["maps"][0]["preferences"]
    assert areas == [
        {"area_id": 2, "mowing_height_cm": 6.0},
        {"map_index": 0, "area_id": 1, "mowing_height_cm": 7.0},
    ]
    assert result["maps"][0]["area_count"] == 2
    assert result["maps"][0]["available"] is True
    assert remaining == pending


def test_failed_preference_read_preserves_error_and_unrelated_map_evidence() -> None:
    confirmed_at = datetime.now(UTC)
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 0,
        "area_id": None,
        "changed_fields": ["preference_mode"],
        "readback": {
            "map": {"idx": 0, "mode": 0, "mode_name": "global"},
            "preference": None,
        },
    }
    pending = retain_confirmed_preference_write(
        [],
        confirmed,
        confirmed_at=confirmed_at,
    )
    incoming = {
        "available": False,
        "errors": [{"stage": "settings", "error": "partial read"}],
        "maps": [
            {"idx": 0, "mode": 1, "mode_name": "custom", "preferences": []},
            {
                "idx": 1,
                "mode": 1,
                "mode_name": "custom",
                "preferences": [{"area_id": 4, "mowing_height_cm": 5.0}],
            },
        ],
    }

    result, remaining = reconcile_pending_preference_readbacks(
        incoming,
        pending,
    )

    assert result["available"] is False
    assert result["errors"] == [{"stage": "settings", "error": "partial read"}]
    assert result["maps"][0]["mode_name"] == "global"
    assert result["maps"][1] is incoming["maps"][1]
    assert remaining == pending


def test_authoritative_map_error_does_not_retire_mode_confirmation() -> None:
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 1,
        "area_id": None,
        "changed_fields": ["preference_mode"],
        "readback": {
            "map": {"idx": 1, "mode": 0, "mode_name": "global"},
            "preference": None,
        },
    }
    pending = retain_confirmed_preference_write(
        [],
        confirmed,
        confirmed_at=datetime.now(UTC),
    )
    direct = {
        "available": True,
        "errors": [{"idx": 1, "stage": "preferences", "error": "PREI unavailable"}],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "preferences": [{"area_id": 2, "mowing_height_cm": 5.0}],
            },
            {
                "idx": 1,
                "error": "PREI unavailable",
                "preferences": [],
            },
        ],
    }

    result, remaining = reconcile_pending_preference_readbacks(
        direct,
        pending,
        allow_convergence=False,
        authoritative=True,
    )

    assert result["maps"][1]["mode"] == 0
    assert result["maps"][1]["mode_name"] == "global"
    assert remaining == pending


def test_authoritative_null_mode_does_not_retire_mode_confirmation() -> None:
    pending = retain_confirmed_preference_write(
        [],
        {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 0,
            "area_id": None,
            "changed_fields": ["preference_mode"],
            "readback": {
                "map": {"idx": 0, "mode": 0, "mode_name": "global"},
                "preference": None,
            },
        },
        confirmed_at=datetime.now(UTC),
    )
    direct = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": None,
                "mode_name": None,
                "area_count": 1,
                "preferences": [{"area_id": 1, "mowing_height_cm": 5.0}],
            }
        ],
    }

    result, remaining = reconcile_pending_preference_readbacks(
        direct,
        pending,
        allow_convergence=False,
        authoritative=True,
    )

    assert remaining == pending
    assert result["maps"][0]["mode"] == 0
    assert result["maps"][0]["mode_name"] == "global"


def test_mode_only_batch_retires_matching_mode_confirmation() -> None:
    pending = retain_confirmed_preference_write(
        [],
        {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 0,
            "area_id": None,
            "changed_fields": ["preference_mode"],
            "readback": {
                "map": {"idx": 0, "mode": 0, "mode_name": "global"},
                "preference": None,
            },
        },
        confirmed_at=datetime.now(UTC),
    )
    batch = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "available": True,
                "mode": 0,
                "mode_name": "global",
                "area_count": 0,
                "preferences": [],
            }
        ],
    }

    result, remaining = reconcile_pending_preference_readbacks(batch, pending)

    assert remaining == []
    assert result == batch


def test_authoritative_empty_custom_map_retires_deleted_area_confirmation() -> None:
    pending = retain_confirmed_preference_write(
        [],
        {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 0,
            "area_id": 2,
            "changed_fields": ["mowing_height_cm"],
            "readback": {
                "map": {"idx": 0, "mode": 1, "mode_name": "custom"},
                "preference": {"area_id": 2, "mowing_height_cm": 4.5},
            },
        },
        confirmed_at=datetime.now(UTC),
    )
    direct = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "available": True,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 0,
                "preferences": [],
            }
        ],
    }

    result, remaining = reconcile_pending_preference_readbacks(
        direct,
        pending,
        allow_convergence=False,
        authoritative=True,
    )

    assert remaining == []
    assert result["maps"][0]["preferences"] == []


def test_empty_incomplete_batch_map_preserves_cached_areas() -> None:
    partial_batch = {
        "source": "batch_device_data_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 0,
                "preferences": [],
            }
        ],
    }
    cached = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 2, "mowing_height_cm": 5.0}],
            }
        ],
    }

    result = merge_mowing_preference_readbacks(partial_batch, cached)

    assert result["maps"][0]["preferences"] == [
        {"area_id": 2, "mowing_height_cm": 5.0}
    ]
    assert result["maps"][0]["area_count"] == 1


def test_mode_only_global_map_keeps_batch_only_area_zero_confirmation() -> None:
    pending = retain_confirmed_preference_write(
        [],
        {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 0,
            "area_id": 0,
            "changed_fields": ["mowing_height_cm"],
            "readback": {
                "map": {"idx": 0, "mode": 0, "mode_name": "global"},
                "preference": {"area_id": 0, "mowing_height_cm": 4.5},
            },
        },
        confirmed_at=datetime.now(UTC),
    )
    direct = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "available": True,
                "mode": 0,
                "mode_name": "global",
                "area_count": 0,
                "preferences": [],
            }
        ],
    }

    result, remaining = reconcile_pending_preference_readbacks(
        direct,
        pending,
        allow_convergence=False,
        authoritative=True,
    )

    assert remaining == pending
    assert result["maps"][0]["preferences"] == [
        {"map_index": 0, "area_id": 0, "mowing_height_cm": 4.5}
    ]


def test_mode_only_direct_read_can_supersede_confirmation() -> None:
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 0,
        "area_id": None,
        "changed_fields": ["preference_mode"],
        "readback": {
            "map": {"idx": 0, "mode": 0, "mode_name": "global"},
            "preference": None,
        },
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = retain_confirmed_preference_write(
        [],
        confirmed,
        confirmed_at=datetime.now(UTC),
    )
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [{"idx": 0}],
    }
    coordinator.batch_device_data = None
    direct = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "available": True,
                "mode": 1,
                "mode_name": "custom",
                "preferences": [],
            }
        ],
    }
    batch = {
        "available": True,
        "errors": [
            {
                "idx": 0,
                "area_id": 9,
                "stage": "preference",
                "error": "unrelated area unavailable",
            }
        ],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "preferences": [{"area_id": 0, "mowing_height_cm": 5.0}],
            }
        ],
    }
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=AsyncMock(return_value=batch),
    )

    result = asyncio.run(
        coordinator.async_refresh_mowing_preferences(source="preference_event")
    )

    assert result is not None
    assert result["maps"][0]["mode_name"] == "custom"
    assert coordinator._pending_preference_confirmations == []


def test_prei_mode_supersedes_confirmation_when_all_area_reads_fail() -> None:
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 0,
        "area_id": None,
        "changed_fields": ["preference_mode"],
        "readback": {
            "map": {"idx": 0, "mode": 0, "mode_name": "global"},
            "preference": None,
        },
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = retain_confirmed_preference_write(
        [],
        confirmed,
        confirmed_at=datetime.now(UTC),
    )
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [{"idx": 0}],
    }
    coordinator.batch_device_data = None
    area_error = {
        "idx": 0,
        "area_id": 0,
        "stage": "preference",
        "error": "PRE unavailable",
    }
    direct = {
        "available": True,
        "errors": [area_error],
        "maps": [
            {
                "idx": 0,
                "available": True,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [],
                "errors": [area_error],
                "error": "PRE unavailable",
            }
        ],
    }
    batch = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 0,
                "mode_name": "global",
                "preferences": [{"area_id": 0, "mowing_height_cm": 5.0}],
            }
        ],
    }
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=AsyncMock(return_value=batch),
    )

    result = asyncio.run(
        coordinator.async_refresh_mowing_preferences(source="preference_event")
    )

    assert result is not None
    assert result["maps"][0]["mode_name"] == "custom"
    assert result["maps"][0]["preferences"] == [{"area_id": 0, "mowing_height_cm": 5.0}]
    assert coordinator._pending_preference_confirmations == []


def test_active_preference_confirmations_are_not_evicted_by_new_writes() -> None:
    confirmed_at = datetime.now(UTC)
    pending = []
    for area_id in range(20):
        pending = retain_confirmed_preference_write(
            pending,
            {
                "executed": True,
                "request_verified": True,
                "verification_source": "preference_readback",
                "map_index": 0,
                "area_id": area_id,
                "changed_fields": ["mowing_height_cm"],
                "readback": {
                    "map": {"idx": 0, "mode": 1, "mode_name": "custom"},
                    "preference": {
                        "area_id": area_id,
                        "mowing_height_cm": 4.0,
                    },
                },
            },
            confirmed_at=confirmed_at + timedelta(seconds=area_id),
        )

    assert len(pending) == 20

    pending = retain_confirmed_preference_write(
        pending,
        {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 1,
            "area_id": None,
            "changed_fields": ["preference_mode"],
            "readback": {
                "map": {"idx": 1, "mode": 0, "mode_name": "global"},
                "preference": None,
            },
        },
        confirmed_at=confirmed_at + timedelta(days=3),
    )

    assert len(pending) == 21


def test_later_exact_readback_retires_contradicted_confirmation() -> None:
    confirmed_at = datetime.now(UTC)
    pending = retain_confirmed_preference_write(
        [],
        {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 0,
            "area_id": 1,
            "changed_fields": ["mowing_height_cm"],
            "readback": {
                "map": {"idx": 0, "mode": 1, "mode_name": "custom"},
                "preference": {"area_id": 1, "mowing_height_cm": 7.0},
            },
        },
        confirmed_at=confirmed_at,
    )

    pending = retain_confirmed_preference_write(
        pending,
        {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 0,
            "area_id": 1,
            "changed_fields": ["obstacle_avoidance_sensitivity"],
            "readback": {
                "map": {"idx": 0, "mode": 1, "mode_name": "custom"},
                "preference": {
                    "area_id": 1,
                    "mowing_height_cm": 6.0,
                    "obstacle_avoidance_sensitivity": 2,
                },
            },
        },
        confirmed_at=confirmed_at + timedelta(seconds=10),
    )

    assert [item.field for item in pending] == ["obstacle_avoidance_sensitivity"]


def test_noop_exact_readback_retires_contradicted_confirmation() -> None:
    confirmed_at = datetime.now(UTC)
    pending = retain_confirmed_preference_write(
        [],
        {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 0,
            "area_id": 1,
            "changed_fields": ["mowing_height_cm"],
            "readback": {
                "map": {"idx": 0, "mode": 1, "mode_name": "custom"},
                "preference": {"area_id": 1, "mowing_height_cm": 7.0},
            },
        },
        confirmed_at=confirmed_at,
    )

    pending = retain_confirmed_preference_write(
        pending,
        {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 0,
            "area_id": 1,
            "changed_fields": [],
            "readback": {
                "map": {"idx": 0, "mode": 1, "mode_name": "custom"},
                "preference": {"area_id": 1, "mowing_height_cm": 6.0},
            },
        },
        confirmed_at=confirmed_at + timedelta(seconds=10),
    )

    assert pending == []


def test_event_preference_refresh_uses_direct_readback_before_batch() -> None:
    direct_preferences = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 0,
                "mode_name": "global",
                "area_count": 0,
                "preferences": [],
            }
        ],
    }
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 0,
        "area_id": None,
        "changed_fields": ["preference_mode"],
        "readback": {
            "map": {"idx": 0, "mode": 0, "mode_name": "global"},
            "preference": None,
        },
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = retain_confirmed_preference_write(
        [],
        confirmed,
        confirmed_at=datetime.now(UTC),
    )
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [{"idx": 0}],
    }
    coordinator.batch_device_data = None
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct_preferences),
        async_get_batch_mowing_preferences=AsyncMock(
            return_value={
                "source": "batch_device_data_mowing_preferences",
                "available": True,
                "errors": [],
                "maps": [
                    {
                        "idx": 0,
                        "mode": 1,
                        "mode_name": "custom",
                        "area_count": 1,
                        "preferences": [{"area_id": 0, "mowing_height_cm": 5.0}],
                    }
                ],
            }
        ),
    )

    result = asyncio.run(
        coordinator.async_refresh_mowing_preferences(source="preference_event")
    )

    assert result is not None
    assert result["maps"][0]["mode_name"] == "global"
    assert result["maps"][0]["preferences"] == [{"area_id": 0, "mowing_height_cm": 5.0}]
    assert coordinator.batch_device_data["batch_mowing_preferences"] is result
    coordinator.client.async_get_mowing_preferences.assert_awaited_once_with(
        include_raw=False,
        map_indices=[0],
    )
    coordinator.client.async_get_batch_mowing_preferences.assert_awaited_once_with(
        include_raw=False,
        map_index_hints=[0],
        map_slot_index_hints=[0],
    )
    assert coordinator._pending_preference_confirmations


def test_invalid_direct_mode_pair_preserves_batch_mode_pair() -> None:
    direct = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "available": True,
                "mode": 999,
                "mode_name": None,
                "area_count": 1,
                "preferences": [{"area_id": 1, "mowing_height_cm": 4.5}],
            }
        ],
    }
    batch = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "available": True,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 1, "mowing_height_cm": 5.0}],
            }
        ],
    }

    result = merge_mowing_preference_readbacks(direct, batch)

    assert result["maps"][0]["mode"] == 1
    assert result["maps"][0]["mode_name"] == "custom"
    assert result["maps"][0]["preferences"] == [{"area_id": 1, "mowing_height_cm": 4.5}]


def test_empty_custom_direct_map_discards_stale_cached_areas() -> None:
    direct = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "available": True,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 0,
                "advertised_area_ids": [],
                "preferences": [],
            }
        ],
    }
    cached = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "available": True,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 2, "mowing_height_cm": 5.0}],
            }
        ],
    }

    result = merge_mowing_preference_readbacks(direct, cached)

    assert result["maps"][0]["area_count"] == 0
    assert result["maps"][0]["preferences"] == []


def test_authoritative_empty_custom_direct_read_completes_without_batch() -> None:
    direct = {
        "source": "app_action_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "available": True,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 0,
                "advertised_area_ids": [],
                "preferences": [],
            }
        ],
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = []
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [{"idx": 0}],
    }
    coordinator.batch_device_data = None
    coordinator.batch_device_data_refreshed_at = datetime.now(UTC)
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=AsyncMock(),
    )

    result = asyncio.run(
        coordinator.async_refresh_mowing_preferences(source="preference_event")
    )

    assert result == direct
    coordinator.client.async_get_batch_mowing_preferences.assert_not_awaited()


def test_partial_direct_inventory_prunes_deleted_cached_area() -> None:
    direct = {
        "available": True,
        "errors": [
            {"idx": 0, "area_id": 2, "stage": "preference", "error": "unavailable"}
        ],
        "maps": [
            {
                "idx": 0,
                "available": True,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 2,
                "advertised_area_ids": [1, 2],
                "preferences": [{"area_id": 1, "version": 11, "reported_version": 11}],
            }
        ],
    }
    cached = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "area_count": 3,
                "preferences": [
                    {"area_id": 1, "version": 10},
                    {"area_id": 2, "version": 20},
                    {"area_id": 3, "version": 30},
                ],
            }
        ],
    }

    result = merge_mowing_preference_readbacks(direct, cached)

    assert result["maps"][0]["preferences"] == [
        {"area_id": 1, "version": 11, "reported_version": 11},
        {"area_id": 2, "version": 20},
    ]


def test_mismatched_direct_version_preserves_fallback_area() -> None:
    direct = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "area_count": 1,
                "advertised_area_ids": [1],
                "preferences": [{"area_id": 1, "version": 10, "reported_version": 11}],
            }
        ],
    }
    fallback = {
        "available": True,
        "errors": [],
        "maps": [
            {"idx": 0, "area_count": 1, "preferences": [{"area_id": 1, "version": 9}]}
        ],
    }

    result = merge_mowing_preference_readbacks(direct, fallback)

    assert result["maps"][0]["preferences"] == [{"area_id": 1, "version": 9}]


def test_complete_direct_read_still_checks_batch_for_pending_confirmation() -> None:
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 0,
        "area_id": 1,
        "changed_fields": ["mowing_height_cm"],
        "readback": {
            "map": {"idx": 0, "mode": 1, "mode_name": "custom"},
            "preference": {
                "map_index": 0,
                "area_id": 1,
                "version": 164,
                "mowing_height_cm": 4.5,
            },
        },
    }
    direct = {
        "source": "app_action_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "preferences": [
                    {
                        "map_index": 0,
                        "area_id": 1,
                        "version": 164,
                        "mowing_height_cm": 4.5,
                    }
                ],
            }
        ],
    }
    batch = {
        "source": "batch_device_data_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "preferences": [
                    {
                        "map_index": 0,
                        "area_id": 1,
                        "version": 164,
                        "mowing_height_cm": 4.5,
                    }
                ],
            }
        ],
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = retain_confirmed_preference_write(
        [],
        confirmed,
        confirmed_at=datetime.now(UTC),
    )
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [{"idx": 0}],
    }
    coordinator.batch_device_data = None
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=AsyncMock(return_value=batch),
    )

    result = asyncio.run(
        coordinator.async_refresh_mowing_preferences(source="preference_event")
    )

    assert result is not None
    assert result["maps"][0]["preferences"][0]["mowing_height_cm"] == 4.5
    assert coordinator._pending_preference_confirmations == []
    coordinator.client.async_get_batch_mowing_preferences.assert_awaited_once_with(
        include_raw=False,
        map_index_hints=[0],
        map_slot_index_hints=[0],
    )


def test_mixed_multimap_direct_refresh_retains_global_batch_preference() -> None:
    direct_preferences = {
        "source": "app_action_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "preferences": [{"area_id": 3, "version": 12, "mowing_height_cm": 4.0}],
            },
            {
                "idx": 1,
                "mode": 0,
                "mode_name": "global",
                "area_count": 0,
                "advertised_area_ids": [],
                "preferences": [],
            },
        ],
    }
    batch_preferences = {
        "source": "batch_device_data_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "preferences": [{"area_id": 3, "version": 10, "mowing_height_cm": 5.0}],
            },
            {
                "idx": 1,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 3,
                "preferences": [
                    {"area_id": 0, "version": 160, "mowing_height_cm": 5.5},
                    {"area_id": 1, "version": 90, "mowing_height_cm": 6.0},
                    {"area_id": 2, "version": 80, "mowing_height_cm": 6.5},
                ],
            },
        ],
    }
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 1,
        "area_id": 0,
        "changed_fields": ["mowing_height_cm"],
        "readback": {
            "map": {"idx": 1, "mode": 0, "mode_name": "global"},
            "preference": {
                "area_id": 0,
                "version": 164,
                "mowing_height_cm": 4.5,
            },
        },
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = retain_confirmed_preference_write(
        [],
        confirmed,
        confirmed_at=datetime.now(UTC),
    )
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [{"idx": 0}, {"idx": 1}],
    }
    coordinator.batch_device_data = None
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct_preferences),
        async_get_batch_mowing_preferences=AsyncMock(return_value=batch_preferences),
    )

    result = asyncio.run(
        coordinator.async_refresh_mowing_preferences(source="preference_event")
    )

    assert result is not None
    assert result["source"] == ("app_action_mowing_preferences_with_batch_fallback")
    assert result["maps"][0]["preferences"] == [
        {"area_id": 3, "version": 12, "mowing_height_cm": 4.0}
    ]
    assert result["maps"][1]["mode_name"] == "global"
    assert result["maps"][1]["area_count"] == 1
    assert result["maps"][1]["preferences"] == [
        {"area_id": 0, "version": 164, "mowing_height_cm": 4.5}
    ]
    assert coordinator._pending_preference_confirmations
    coordinator.client.async_get_batch_mowing_preferences.assert_awaited_once_with(
        include_raw=False,
        map_index_hints=[0, 1],
        map_slot_index_hints=[0, 1],
    )


def test_direct_area_refresh_preserves_batch_mode_when_prei_omits_it() -> None:
    direct = {
        "source": "app_action_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "available": True,
                "mode": None,
                "mode_name": None,
                "area_count": 1,
                "preferences": [{"area_id": 3, "version": 12, "mowing_height_cm": 4.0}],
            }
        ],
    }
    batch = {
        "source": "batch_device_data_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "available": True,
                "mode": 0,
                "mode_name": "global",
                "area_count": 1,
                "preferences": [{"area_id": 3, "version": 10, "mowing_height_cm": 5.0}],
            }
        ],
    }

    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._pending_preference_confirmations = []
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=AsyncMock(return_value=batch),
    )

    result, returned_direct, returned_batch = asyncio.run(
        coordinator._async_get_current_mowing_preferences(
            map_index_hints=[0],
            map_slot_index_hints=[0],
        )
    )

    assert result["maps"][0]["mode"] == 0
    assert result["maps"][0]["mode_name"] == "global"
    assert result["maps"][0]["preferences"] == [
        {"area_id": 3, "version": 12, "mowing_height_cm": 4.0}
    ]
    assert returned_direct is direct
    assert returned_batch is batch
    coordinator.client.async_get_batch_mowing_preferences.assert_awaited_once_with(
        include_raw=False,
        map_index_hints=[0],
        map_slot_index_hints=[0],
    )


@pytest.mark.parametrize(
    "raw_payload",
    [
        pytest.param(tuple(range(17)), id="truncated"),
        pytest.param((*range(17), None, 1, 1, 4), id="full_width_null"),
    ],
)
def test_incomplete_optional_direct_payload_preserves_batch_fields(
    raw_payload: tuple[object, ...],
) -> None:
    direct_preference = {
        "area_id": 1,
        "version": 10,
        "reported_version": 10,
        "mowing_height_cm": 4.5,
        "obstacle_avoidance_sensitivity": None,
        "edge_cutting_attachment": True,
        "steering_mode": 1,
        "cutter_position_height": 4,
        "_raw_payload": raw_payload,
    }
    direct = {
        "source": "app_action_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "advertised_area_ids": [1],
                "preferences": [direct_preference],
            }
        ],
    }
    batch = {
        "source": "batch_device_data_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [
                    {
                        "area_id": 1,
                        "version": 9,
                        "reported_version": 9,
                        "mowing_height_cm": 5.0,
                        "obstacle_avoidance_sensitivity": 2,
                    }
                ],
            }
        ],
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._pending_preference_confirmations = []
    coordinator.batch_device_data = None
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=AsyncMock(return_value=batch),
    )

    result, returned_direct, returned_batch = asyncio.run(
        coordinator._async_get_current_mowing_preferences(
            map_index_hints=[0],
            map_slot_index_hints=[0],
        )
    )

    preference = result["maps"][0]["preferences"][0]
    assert preference["mowing_height_cm"] == 4.5
    assert preference["obstacle_avoidance_sensitivity"] == 2
    assert returned_direct is direct
    assert returned_batch is batch


@pytest.mark.parametrize("batch_available", [False, True])
def test_optional_direct_fallback_requires_current_batch_values(
    batch_available: bool,
) -> None:
    direct = {
        "source": "app_action_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "advertised_area_ids": [1],
                "preferences": [
                    {
                        "area_id": 1,
                        "version": 10,
                        "reported_version": 10,
                        "mowing_height_cm": 4.5,
                        "obstacle_avoidance_sensitivity": None,
                        "edge_cutting_attachment": None,
                        "steering_mode": None,
                        "cutter_position_height": None,
                        "_raw_payload": tuple(range(17)),
                    }
                ],
            }
        ],
    }

    def fallback(source: str, value: int) -> dict[str, object]:
        return {
            "source": source,
            "available": True,
            "errors": [],
            "maps": [
                {
                    "idx": 0,
                    "mode": 1,
                    "mode_name": "custom",
                    "area_count": 1,
                    "preferences": [
                        {
                            "area_id": 1,
                            "version": 9,
                            "reported_version": 9,
                            "obstacle_avoidance_sensitivity": value,
                            "edge_cutting_attachment": True,
                            "steering_mode": 1,
                            "cutter_position_height": 4,
                        }
                    ],
                }
            ],
        }

    cached = fallback("cached_mowing_preferences", 1)
    batch = fallback("batch_device_data_mowing_preferences", 2)
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = []
    coordinator.async_update_listeners = Mock()
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [{"idx": 0}],
    }
    coordinator.app_maps_refresh_succeeded = True
    coordinator.batch_device_data = {"batch_mowing_preferences": cached}
    coordinator.batch_device_data_refreshed_at = datetime.now(UTC)
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=(
            AsyncMock(return_value=batch)
            if batch_available
            else AsyncMock(side_effect=RuntimeError("batch unavailable"))
        ),
    )

    result = asyncio.run(
        coordinator.async_refresh_mowing_preferences(source="preference_event")
    )

    preferences = coordinator.batch_device_data["batch_mowing_preferences"]
    effective = preferences["maps"][0]["preferences"][0]
    if batch_available:
        assert result is not None
        assert effective["obstacle_avoidance_sensitivity"] == 2
        coordinator.async_update_listeners.assert_not_called()
    else:
        assert result is None
        assert effective["obstacle_avoidance_sensitivity"] == 1
        assert coordinator.batch_device_data_refreshed_at is None
        coordinator.async_update_listeners.assert_called_once_with()


def test_incomplete_advertised_direct_areas_use_batch_fallback() -> None:
    direct = {
        "source": "app_action_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "available": True,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 2,
                "preferences": [
                    {"area_id": 1, "version": 164, "mowing_height_cm": 4.5}
                ],
            }
        ],
    }
    batch = {
        "source": "batch_device_data_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "available": True,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 2,
                "preferences": [
                    {"area_id": 1, "version": 160, "mowing_height_cm": 5.5},
                    {"area_id": 2, "version": 90, "mowing_height_cm": 6.0},
                ],
            }
        ],
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._pending_preference_confirmations = []
    coordinator.batch_device_data = None
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=AsyncMock(return_value=batch),
    )

    result, returned_direct, returned_batch = asyncio.run(
        coordinator._async_get_current_mowing_preferences(
            map_index_hints=[0],
            map_slot_index_hints=[0],
        )
    )

    assert result["maps"][0]["preferences"] == [
        {"area_id": 1, "version": 164, "mowing_height_cm": 4.5},
        {"area_id": 2, "version": 90, "mowing_height_cm": 6.0},
    ]
    assert returned_direct is direct
    assert returned_batch is batch
    coordinator.client.async_get_batch_mowing_preferences.assert_awaited_once_with(
        include_raw=False,
        map_index_hints=[0],
        map_slot_index_hints=[0],
    )


def test_batch_converges_confirmation_when_its_direct_map_read_fails() -> None:
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 1,
        "area_id": 0,
        "changed_fields": ["mowing_height_cm"],
        "readback": {
            "map": {"idx": 1, "mode": 0, "mode_name": "global"},
            "preference": {
                "area_id": 0,
                "version": 164,
                "mowing_height_cm": 4.5,
            },
        },
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = retain_confirmed_preference_write(
        [],
        confirmed,
        confirmed_at=datetime.now(UTC),
    )
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [{"idx": 0}, {"idx": 1}],
    }
    coordinator.batch_device_data = None
    direct = {
        "available": True,
        "errors": [{"idx": 1, "stage": "preferences", "error": "PREI unavailable"}],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "preferences": [{"area_id": 2, "mowing_height_cm": 5.0}],
            },
            {"idx": 1, "error": "PREI unavailable", "preferences": []},
        ],
    }
    batch = {
        "available": True,
        "errors": [
            {
                "idx": 0,
                "area_id": 9,
                "stage": "preference",
                "error": "unrelated area unavailable",
            }
        ],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "preferences": [{"area_id": 2, "mowing_height_cm": 5.0}],
            },
            {
                "idx": 1,
                "mode": 0,
                "mode_name": "global",
                "preferences": [
                    {"area_id": 0, "version": 164, "mowing_height_cm": 4.5}
                ],
            },
        ],
    }
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=AsyncMock(return_value=batch),
    )

    result = asyncio.run(
        coordinator.async_refresh_mowing_preferences(source="preference_event")
    )

    assert result is not None
    assert result["maps"][1]["preferences"][0]["mowing_height_cm"] == 4.5
    assert coordinator._pending_preference_confirmations == []


def test_partial_direct_preference_refresh_merges_batch_only_for_failed_area() -> None:
    direct_preferences = {
        "source": "app_action_mowing_preferences",
        "available": True,
        "errors": [
            {
                "idx": 0,
                "area_id": 2,
                "stage": "preference",
                "error": "PRE temporarily unavailable",
            }
        ],
        "maps": [
            {
                "idx": 0,
                "mode": 0,
                "mode_name": "global",
                "area_count": 2,
                "preferences": [
                    {
                        "map_index": 0,
                        "area_id": 1,
                        "version": 164,
                        "mowing_height_cm": 4.5,
                    }
                ],
            }
        ],
    }
    batch_preferences = {
        "source": "batch_device_data_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 2,
                "preferences": [
                    {
                        "map_index": 0,
                        "area_id": 1,
                        "version": 160,
                        "mowing_height_cm": 5.5,
                    },
                    {
                        "map_index": 0,
                        "area_id": 2,
                        "version": 90,
                        "mowing_height_cm": 6.0,
                    },
                ],
            }
        ],
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = []
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [{"idx": 0}],
    }
    coordinator.batch_device_data = None
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct_preferences),
        async_get_batch_mowing_preferences=AsyncMock(return_value=batch_preferences),
    )

    result = asyncio.run(
        coordinator.async_refresh_mowing_preferences(source="preference_event")
    )

    assert result is not None
    assert result["source"] == ("app_action_mowing_preferences_with_batch_fallback")
    assert result["maps"][0]["mode_name"] == "global"
    areas = result["maps"][0]["preferences"]
    assert areas[0]["mowing_height_cm"] == 4.5
    assert areas[0]["version"] == 164
    assert areas[1]["mowing_height_cm"] == 6.0
    coordinator.client.async_get_batch_mowing_preferences.assert_awaited_once_with(
        include_raw=False,
        map_index_hints=[0],
        map_slot_index_hints=[0],
    )


def test_partial_direct_refresh_uses_cache_when_batch_read_fails() -> None:
    direct = {
        "source": "app_action_mowing_preferences",
        "available": True,
        "errors": [
            {
                "idx": 0,
                "area_id": 2,
                "stage": "preference",
                "error": "PRE unavailable",
            }
        ],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 2,
                "preferences": [
                    {"area_id": 1, "version": 164, "mowing_height_cm": 4.5}
                ],
            }
        ],
    }
    cached = {
        "source": "batch_device_data_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 2,
                "preferences": [
                    {"area_id": 1, "version": 160, "mowing_height_cm": 5.5},
                    {"area_id": 2, "version": 90, "mowing_height_cm": 6.0},
                ],
            }
        ],
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._pending_preference_confirmations = []
    coordinator.batch_device_data = {"batch_mowing_preferences": cached}
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=AsyncMock(
            side_effect=RuntimeError("batch unavailable")
        ),
    )

    result, returned_direct, returned_batch = asyncio.run(
        coordinator._async_get_current_mowing_preferences(
            map_index_hints=[0],
            map_slot_index_hints=[0],
        )
    )

    assert result["maps"][0]["preferences"] == [
        {"area_id": 1, "version": 164, "mowing_height_cm": 4.5},
        {"area_id": 2, "version": 90, "mowing_height_cm": 6.0},
    ]
    assert returned_direct is direct
    assert returned_batch is None


@pytest.mark.parametrize(
    "failed_batch_map",
    [
        None,
        {"idx": 1, "error": "invalid batch map", "preferences": []},
    ],
)
def test_partial_batch_refresh_preserves_cached_failed_map_and_retries(
    failed_batch_map: dict[str, object] | None,
) -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        schedule = {"available": True, "schedules": []}
        cached_preferences = {
            "source": "batch_device_data_mowing_preferences",
            "available": True,
            "errors": [],
            "maps": [
                {
                    "idx": 0,
                    "mode": 1,
                    "mode_name": "custom",
                    "preferences": [
                        {"area_id": 2, "version": 10, "mowing_height_cm": 5.0}
                    ],
                },
                {
                    "idx": 1,
                    "mode": 0,
                    "mode_name": "global",
                    "preferences": [
                        {"area_id": 0, "version": 90, "mowing_height_cm": 6.0}
                    ],
                },
            ],
        }
        direct = {
            "source": "app_action_mowing_preferences",
            "available": True,
            "errors": [{"idx": 1, "stage": "preferences", "error": "PREI unavailable"}],
            "maps": [
                {
                    "idx": 0,
                    "mode": 1,
                    "mode_name": "custom",
                    "preferences": [
                        {"area_id": 2, "version": 12, "mowing_height_cm": 4.0}
                    ],
                },
                {"idx": 1, "error": "PREI unavailable", "preferences": []},
            ],
        }
        partial_batch_maps = [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "preferences": [{"area_id": 2, "version": 11, "mowing_height_cm": 4.5}],
            }
        ]
        if failed_batch_map is not None:
            partial_batch_maps.append(failed_batch_map)
        partial_batch = {
            "source": "batch_device_data_mowing_preferences",
            "available": True,
            "errors": [],
            "maps": partial_batch_maps,
        }
        refreshed_at = datetime.now(UTC) - timedelta(minutes=16)
        coordinator.schedules = schedule
        coordinator._fresh_batch_schedule = lambda: schedule
        coordinator._published_schedule_read_generation = 0
        coordinator.batch_device_data = {
            "batch_schedule": schedule,
            "batch_mowing_preferences": cached_preferences,
            "batch_ota_info": {"available": True, "version": "1.0.0"},
        }
        coordinator.batch_device_data_refreshed_at = refreshed_at
        coordinator._schedule_cache_generation = 0
        coordinator._pending_preference_confirmations = []
        coordinator.app_maps = {
            "map_list_valid": True,
            "current_map_index": 0,
            "maps": [{"idx": 0}, {"idx": 1}],
        }
        coordinator.client = SimpleNamespace(
            async_get_mowing_preferences=AsyncMock(return_value=direct),
            async_get_batch_mowing_preferences=AsyncMock(return_value=partial_batch),
            async_get_batch_ota_info=AsyncMock(
                return_value={"available": True, "version": "2.0.0"}
            ),
        )

        result = await coordinator.async_refresh_batch_device_data(
            source="partial_batch_retry"
        )

        assert result is not None
        maps = result["batch_mowing_preferences"]["maps"]
        assert maps[0]["preferences"] == [
            {"area_id": 2, "version": 12, "mowing_height_cm": 4.0}
        ]
        assert maps[1]["preferences"] == [
            {"area_id": 0, "version": 90, "mowing_height_cm": 6.0}
        ]
        assert result["batch_ota_info"] == {
            "available": True,
            "version": "2.0.0",
        }
        assert coordinator.batch_device_data_refreshed_at is None

    asyncio.run(scenario())


def test_forced_partial_batch_refresh_invalidates_recent_freshness() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    schedule = {"available": True, "schedules": []}
    partial = {
        "available": True,
        "errors": [{"idx": 1, "stage": "preferences", "error": "unavailable"}],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 1, "version": 10}],
            },
            {"idx": 1, "error": "unavailable", "preferences": []},
        ],
    }
    coordinator.schedules = schedule
    coordinator.batch_device_data = {"batch_mowing_preferences": partial}
    coordinator.batch_device_data_refreshed_at = datetime.now(UTC)
    coordinator._schedule_cache_generation = 0
    coordinator._pending_preference_confirmations = []
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [{"idx": 0}, {"idx": 1}],
    }
    coordinator._async_fetch_batch_device_data = AsyncMock(
        return_value=(schedule, partial, {"available": True}, 0, partial, partial)
    )

    result = asyncio.run(coordinator.async_refresh_batch_device_data(force=True))

    assert result is not None
    assert coordinator.batch_device_data_refreshed_at is None


def test_partial_event_refresh_remains_retryable_for_failed_map() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = []
    coordinator.async_update_listeners = Mock()
    cached = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 1, "mowing_height_cm": 5.0}],
            },
            {
                "idx": 1,
                "mode": 0,
                "mode_name": "global",
                "area_count": 1,
                "preferences": [{"area_id": 0, "mowing_height_cm": 6.0}],
            },
        ],
    }
    direct = {
        "available": True,
        "errors": [{"idx": 1, "stage": "preferences", "error": "unavailable"}],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 1, "mowing_height_cm": 4.5}],
            },
            {"idx": 1, "error": "unavailable", "preferences": []},
        ],
    }
    partial_batch = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 1, "mowing_height_cm": 4.75}],
            }
        ],
    }
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [{"idx": 0}, {"idx": 1}],
    }
    coordinator.batch_device_data = {"batch_mowing_preferences": cached}
    coordinator.batch_device_data_refreshed_at = datetime.now(UTC)
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=AsyncMock(return_value=partial_batch),
    )

    result = asyncio.run(
        coordinator.async_refresh_mowing_preferences(source="preference_event")
    )

    assert result is None
    assert coordinator.batch_device_data_refreshed_at is None
    maps = coordinator.batch_device_data["batch_mowing_preferences"]["maps"]
    assert maps[0]["preferences"][0]["mowing_height_cm"] == 4.5
    assert maps[1]["preferences"][0]["mowing_height_cm"] == 6.0
    coordinator.async_update_listeners.assert_called_once_with()


def test_direct_map_attempts_define_coverage_without_map_list_hints() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = []
    coordinator.async_update_listeners = Mock()
    cached = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 1, "version": 10}],
            },
            {
                "idx": 1,
                "mode": 0,
                "mode_name": "global",
                "area_count": 1,
                "preferences": [{"area_id": 0, "version": 20}],
            },
        ],
    }
    direct = {
        "available": True,
        "errors": [{"idx": 1, "stage": "preferences", "error": "unavailable"}],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 1, "version": 11}],
            },
            {"idx": 1, "error": "unavailable", "preferences": []},
        ],
    }
    batch = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 1, "version": 9}],
            }
        ],
    }
    coordinator.app_maps = {"map_list_valid": False, "maps": []}
    coordinator.batch_device_data = {"batch_mowing_preferences": cached}
    coordinator.batch_device_data_refreshed_at = datetime.now(UTC)
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=AsyncMock(return_value=batch),
    )

    result = asyncio.run(
        coordinator.async_refresh_mowing_preferences(source="preference_event")
    )

    assert result is None
    assert coordinator.batch_device_data_refreshed_at is None
    maps = coordinator.batch_device_data["batch_mowing_preferences"]["maps"]
    assert maps[0]["preferences"] == [{"area_id": 1, "version": 11}]
    assert maps[1]["preferences"] == [{"area_id": 0, "version": 20}]
    coordinator.async_update_listeners.assert_called_once_with()


def test_pending_confirmation_preserves_cached_no_mapl_discovery() -> None:
    cached = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 1, "version": 10}],
            },
            {
                "idx": 1,
                "mode": 0,
                "mode_name": "global",
                "area_count": 1,
                "preferences": [{"area_id": 0, "version": 20}],
            },
        ],
    }
    direct = {
        "available": True,
        "errors": [{"idx": 1, "stage": "preferences", "error": "unavailable"}],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [
                    {"area_id": 1, "version": 11, "reported_version": 11}
                ],
            },
            {"idx": 1, "error": "unavailable", "preferences": []},
        ],
    }
    batch = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 1, "version": 9}],
            }
        ],
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._pending_preference_confirmations = [SimpleNamespace(map_index=0)]
    coordinator.batch_device_data = {"batch_mowing_preferences": cached}
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=AsyncMock(return_value=batch),
    )

    result, returned_direct, returned_batch = asyncio.run(
        coordinator._async_get_current_mowing_preferences(
            map_index_hints=[],
            map_slot_index_hints=[],
        )
    )

    assert [entry["idx"] for entry in result["maps"]] == [0, 1]
    assert result["maps"][1]["preferences"] == [{"area_id": 0, "version": 20}]
    assert returned_direct is direct
    assert returned_batch is batch
    coordinator.client.async_get_mowing_preferences.assert_awaited_once_with(
        include_raw=False,
        map_indices=[0, 1],
    )


def test_authoritative_map_hints_exclude_deleted_cached_map() -> None:
    cached = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": map_index,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [
                    {
                        "area_id": map_index + 1,
                        "version": 10,
                        "reported_version": 10,
                    }
                ],
            }
            for map_index in (0, 1)
        ],
    }
    direct = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "advertised_area_ids": [1],
                "preferences": [
                    {"area_id": 1, "version": 11, "reported_version": 11}
                ],
            }
        ],
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._pending_preference_confirmations = []
    coordinator.batch_device_data = {"batch_mowing_preferences": cached}
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=AsyncMock(),
    )

    result, returned_direct, returned_batch = asyncio.run(
        coordinator._async_get_current_mowing_preferences(
            map_index_hints=[0],
            map_slot_index_hints=[0],
            map_hints_authoritative=True,
        )
    )

    assert [entry["idx"] for entry in result["maps"]] == [0]
    assert returned_direct is direct
    assert returned_batch is None
    coordinator.client.async_get_mowing_preferences.assert_awaited_once_with(
        include_raw=False,
        map_indices=[0],
    )
    coordinator.client.async_get_batch_mowing_preferences.assert_not_awaited()


def test_authoritative_map_coverage_includes_fenced_pending_target() -> None:
    cached = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": map_index,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [
                    {
                        "area_id": map_index + 1,
                        "version": 10,
                        "mowing_height_cm": 5.0 + map_index,
                        "edge_mowing_auto": True,
                    }
                ],
            }
            for map_index in (0, 1)
        ],
    }
    direct = {
        "available": True,
        "errors": [{"idx": 1, "stage": "preferences", "error": "unavailable"}],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "advertised_area_ids": [1],
                "preferences": [
                    {"area_id": 1, "version": 11, "reported_version": 11}
                ],
            },
            {"idx": 1, "error": "unavailable", "preferences": []},
        ],
    }
    batch = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 1, "version": 9}],
            }
        ],
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._pending_preference_confirmations = [SimpleNamespace(map_index=1)]
    coordinator.batch_device_data = {"batch_mowing_preferences": cached}
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=AsyncMock(return_value=batch),
    )

    result, _, returned_batch = asyncio.run(
        coordinator._async_get_current_mowing_preferences(
            map_index_hints=[0],
            map_slot_index_hints=[0, 1],
            map_hints_authoritative=True,
        )
    )

    assert [entry["idx"] for entry in result["maps"]] == [0, 1]
    assert result["maps"][1]["preferences"][0]["mowing_height_cm"] == 6.0
    assert result["maps"][1]["preferences"][0]["edge_mowing_auto"] is True
    assert [entry["idx"] for entry in returned_batch["maps"]] == [0]
    coordinator.client.async_get_mowing_preferences.assert_awaited_once_with(
        include_raw=False,
        map_indices=[0, 1],
    )
    coordinator.client.async_get_batch_mowing_preferences.assert_awaited_once_with(
        include_raw=False,
        map_index_hints=[0],
        map_slot_index_hints=[0, 1],
        map_indices=[0, 1],
    )


def test_authoritative_batch_fallback_excludes_deleted_map_slot() -> None:
    direct = {
        "source": "app_action_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "advertised_area_ids": [1],
                "preferences": [
                    {
                        "area_id": 1,
                        "version": 11,
                        "reported_version": 11,
                        "obstacle_avoidance_sensitivity": None,
                        "edge_cutting_attachment": None,
                        "steering_mode": None,
                        "cutter_position_height": None,
                        "_raw_payload": tuple(range(17)),
                    }
                ],
            }
        ],
    }
    batch = {
        "source": "batch_device_data_mowing_preferences",
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": map_index,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [
                    {
                        "area_id": map_index + 1,
                        "version": 9,
                        "obstacle_avoidance_sensitivity": 2,
                        "edge_cutting_attachment": True,
                        "steering_mode": 1,
                        "cutter_position_height": 4,
                    }
                ],
            }
            for map_index in (0, 1)
        ],
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._pending_preference_confirmations = []
    coordinator.batch_device_data = {"batch_mowing_preferences": batch}
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(return_value=direct),
        async_get_batch_mowing_preferences=AsyncMock(return_value=batch),
    )

    result, _, returned_batch = asyncio.run(
        coordinator._async_get_current_mowing_preferences(
            map_index_hints=[0],
            map_slot_index_hints=[0, 1],
            map_hints_authoritative=True,
        )
    )

    assert [entry["idx"] for entry in result["maps"]] == [0]
    assert [entry["idx"] for entry in returned_batch["maps"]] == [0]
    coordinator.client.async_get_batch_mowing_preferences.assert_awaited_once_with(
        include_raw=False,
        map_index_hints=[0],
        map_slot_index_hints=[0, 1],
        map_indices=[0],
    )


def test_authoritative_empty_map_inventory_clears_deleted_cached_maps() -> None:
    cached = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 1, "mowing_height_cm": 5.0}],
            }
        ],
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator._pending_preference_confirmations = []
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": None,
        "maps": [{"idx": 0, "created": False}],
    }
    coordinator.app_maps_refresh_succeeded = True
    coordinator.batch_device_data = {"batch_mowing_preferences": cached}
    coordinator.batch_device_data_refreshed_at = datetime.now(UTC)
    coordinator.async_update_listeners = Mock()
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(),
        async_get_batch_mowing_preferences=AsyncMock(),
    )

    result = asyncio.run(
        coordinator.async_refresh_mowing_preferences(source="preference_event")
    )

    assert result is not None
    assert result["available"] is True
    assert result["map_inventory_authoritative"] is True
    assert result["maps"] == []
    assert coordinator.batch_device_data["batch_mowing_preferences"] is result
    coordinator.client.async_get_mowing_preferences.assert_not_awaited()
    coordinator.client.async_get_batch_mowing_preferences.assert_not_awaited()


def test_sparse_batch_area_is_not_a_complete_current_read() -> None:
    sparse_batch = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "mandatory_values_complete": False,
                "preferences": [
                    {
                        "area_id": 1,
                        "version": 11,
                        "reported_version": 11,
                        "mowing_height_cm": None,
                    }
                ],
            }
        ],
    }

    assert not coordinator_module._batch_mowing_preferences_read_complete(
        sparse_batch,
        expected_map_indices=[0],
    )


def test_partial_batch_cannot_converge_confirmation_from_cached_fallback() -> None:
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 1,
        "area_id": 0,
        "changed_fields": ["mowing_height_cm"],
        "readback": {
            "map": {"idx": 1, "mode": 0, "mode_name": "global"},
            "preference": {"area_id": 0, "mowing_height_cm": 4.5},
        },
    }
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator.async_update_listeners = Mock()
    coordinator._pending_preference_confirmations = retain_confirmed_preference_write(
        [],
        confirmed,
        confirmed_at=datetime.now(UTC),
    )
    cached = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 1, "mowing_height_cm": 5.0}],
            },
            {
                "idx": 1,
                "mode": 0,
                "mode_name": "global",
                "area_count": 1,
                "preferences": [{"area_id": 0, "mowing_height_cm": 4.5}],
            },
        ],
    }
    partial_batch = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "area_count": 1,
                "preferences": [{"area_id": 1, "mowing_height_cm": 5.25}],
            }
        ],
    }
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [{"idx": 0}, {"idx": 1}],
    }
    coordinator.batch_device_data = {"batch_mowing_preferences": cached}
    coordinator.batch_device_data_refreshed_at = datetime.now(UTC)
    coordinator.client = SimpleNamespace(
        async_get_mowing_preferences=AsyncMock(
            side_effect=RuntimeError("direct unavailable")
        ),
        async_get_batch_mowing_preferences=AsyncMock(return_value=partial_batch),
    )

    result = asyncio.run(
        coordinator.async_refresh_mowing_preferences(source="preference_event")
    )

    assert result is None
    assert coordinator._pending_preference_confirmations
    maps = coordinator.batch_device_data["batch_mowing_preferences"]["maps"]
    assert maps[1]["preferences"][0]["mowing_height_cm"] == 4.5
    coordinator.async_update_listeners.assert_called_once_with()


def test_preference_confirmation_waits_for_batch_version_convergence() -> None:
    confirmed_at = datetime.now(UTC)
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 0,
        "area_id": 1,
        "changed_fields": ["mowing_height_cm", "mowing_direction_degrees"],
        "readback": {
            "map": {"idx": 0, "mode": 0, "mode_name": "global"},
            "preference": {
                "map_index": 0,
                "area_id": 1,
                "version": 164,
                "reported_version": 164,
                "mowing_height_cm": 4.5,
                "mowing_direction_degrees": 134,
            },
        },
    }
    pending = retain_confirmed_preference_write(
        [],
        confirmed,
        confirmed_at=confirmed_at,
    )
    converged = {
        "available": True,
        "maps": [
            {
                "idx": 0,
                "mode": 0,
                "mode_name": "global",
                "preferences": [
                    {
                        "map_index": 0,
                        "area_id": 1,
                        "version": 164,
                        "reported_version": 164,
                        "mowing_height_cm": 4.5,
                        "mowing_direction_degrees": 134,
                    }
                ],
            }
        ],
    }
    newer_matching = {
        "available": True,
        "maps": [
            {
                "idx": 0,
                "mode": 0,
                "mode_name": "global",
                "preferences": [
                    {
                        "map_index": 0,
                        "area_id": 1,
                        "version": 165,
                        "reported_version": 165,
                        "mowing_height_cm": 4.5,
                        "mowing_direction_degrees": 134,
                    }
                ],
            }
        ],
    }
    superseded = {
        "available": True,
        "maps": [
            {
                "idx": 0,
                "mode": 0,
                "mode_name": "global",
                "preferences": [
                    {
                        "map_index": 0,
                        "area_id": 1,
                        "version": 165,
                        "reported_version": 165,
                        "mowing_height_cm": 5.0,
                        "mowing_direction_degrees": 140,
                    }
                ],
            }
        ],
    }
    reset_counter_batch = {
        "available": True,
        "maps": [
            {
                "idx": 0,
                "mode": 0,
                "mode_name": "global",
                "preferences": [
                    {
                        "map_index": 0,
                        "area_id": 1,
                        "version": 0,
                        "reported_version": 0,
                        "mowing_height_cm": 5.0,
                        "mowing_direction_degrees": 140,
                    }
                ],
            }
        ],
    }
    stale = {
        "available": True,
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "preferences": [
                    {
                        "map_index": 0,
                        "area_id": 1,
                        "version": 160,
                        "reported_version": 160,
                        "mowing_height_cm": 5.5,
                        "mowing_direction_degrees": 136,
                    }
                ],
            }
        ],
    }

    stale_result, stale_pending = reconcile_pending_preference_readbacks(
        stale,
        pending,
    )
    converged_result, converged_pending = reconcile_pending_preference_readbacks(
        converged,
        pending,
    )
    newer_matching_result, newer_matching_pending = (
        reconcile_pending_preference_readbacks(
            newer_matching,
            pending,
        )
    )
    superseded_result, superseded_pending = reconcile_pending_preference_readbacks(
        superseded,
        pending,
        authoritative=True,
    )
    newer_batch_result, newer_batch_pending = reconcile_pending_preference_readbacks(
        superseded,
        pending,
    )
    reset_result, reset_pending = reconcile_pending_preference_readbacks(
        reset_counter_batch,
        pending,
    )

    stale_preference = stale_result["maps"][0]["preferences"][0]
    assert stale_preference["mowing_height_cm"] == 4.5
    assert stale_preference["mowing_direction_degrees"] == 134
    assert stale_preference["version"] == 164
    assert stale_pending == pending
    assert converged_result is converged
    assert converged_pending == []
    assert newer_matching_result is newer_matching
    assert newer_matching_pending == []
    assert superseded_result is superseded
    assert superseded_pending == []
    assert newer_batch_result is superseded
    assert newer_batch_pending == []
    reset_preference = reset_result["maps"][0]["preferences"][0]
    assert reset_preference["mowing_height_cm"] == 4.5
    assert reset_preference["mowing_direction_degrees"] == 134
    assert reset_pending == pending


def test_matching_value_does_not_converge_an_older_batch_version() -> None:
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 0,
        "area_id": 1,
        "changed_fields": ["mowing_height_cm"],
        "readback": {
            "map": {"idx": 0, "mode": 1, "mode_name": "custom"},
            "preference": {
                "area_id": 1,
                "version": 164,
                "reported_version": 164,
                "mowing_height_cm": 5.0,
            },
        },
    }
    pending = retain_confirmed_preference_write(
        [],
        confirmed,
        confirmed_at=datetime.now(UTC),
    )

    def batch(version: int) -> dict[str, object]:
        return {
            "available": True,
            "maps": [
                {
                    "idx": 0,
                    "mode": 1,
                    "mode_name": "custom",
                    "preferences": [
                        {
                            "area_id": 1,
                            "version": version,
                            "reported_version": version,
                            "mowing_height_cm": 5.0,
                        }
                    ],
                }
            ],
        }

    missing_version = batch(160)
    missing_version_preference = missing_version["maps"][0]["preferences"][0]
    del missing_version_preference["version"]
    del missing_version_preference["reported_version"]
    missing_result, missing_pending = reconcile_pending_preference_readbacks(
        missing_version,
        pending,
    )
    stale_result, stale_pending = reconcile_pending_preference_readbacks(
        batch(160),
        pending,
    )
    converged_result, converged_pending = reconcile_pending_preference_readbacks(
        batch(164),
        pending,
    )

    missing_preference = missing_result["maps"][0]["preferences"][0]
    stale_preference = stale_result["maps"][0]["preferences"][0]
    assert missing_preference["version"] == 164
    assert missing_preference["reported_version"] == 164
    assert missing_pending == pending
    assert stale_preference["mowing_height_cm"] == 5.0
    assert stale_preference["version"] == 164
    assert stale_preference["reported_version"] == 164
    assert stale_pending == pending
    assert converged_result["maps"][0]["preferences"][0]["version"] == 164
    assert converged_pending == []


@pytest.mark.parametrize("fetch_raises", [False, True])
def test_failed_preference_reads_overlay_confirmation_on_cached_fallback(
    fetch_raises: bool,
) -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    schedule = {"available": True, "schedules": []}
    cached = {
        "available": False,
        "errors": [{"stage": "settings", "error": "inventory unavailable"}],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
                "preferences": [
                    {
                        "area_id": 1,
                        "version": 160,
                        "reported_version": 160,
                        "mowing_height_cm": 5.5,
                    }
                ],
            }
        ],
    }
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 0,
        "area_id": 1,
        "changed_fields": ["mowing_height_cm"],
        "readback": {
            "map": {"idx": 0, "mode": 1, "mode_name": "custom"},
            "preference": {
                "area_id": 1,
                "version": 164,
                "reported_version": 164,
                "mowing_height_cm": 4.5,
            },
        },
    }
    coordinator.schedules = schedule
    coordinator.batch_device_data = {"batch_mowing_preferences": cached}
    coordinator.batch_device_data_refreshed_at = None
    coordinator._schedule_cache_generation = 0
    coordinator._pending_preference_confirmations = retain_confirmed_preference_write(
        [],
        confirmed,
        confirmed_at=datetime.now(UTC),
    )
    coordinator.app_maps = {
        "map_list_valid": True,
        "current_map_index": 0,
        "maps": [{"idx": 0}],
    }
    coordinator._async_fetch_batch_device_data = (
        AsyncMock(side_effect=RuntimeError("batch refresh unavailable"))
        if fetch_raises
        else AsyncMock(return_value=(schedule, {}, {"available": True}, 0, None, None))
    )

    result = asyncio.run(coordinator.async_refresh_batch_device_data(force=True))

    assert result is not None
    preference = result["batch_mowing_preferences"]["maps"][0]["preferences"][0]
    assert preference["mowing_height_cm"] == 4.5
    assert preference["version"] == 164
    assert preference["reported_version"] == 164
    assert coordinator._pending_preference_confirmations
    assert coordinator.batch_device_data_refreshed_at is None


def test_surviving_confirmation_uses_authoritative_area_version() -> None:
    confirmed_at = datetime.now(UTC)
    confirmed = {
        "executed": True,
        "request_verified": True,
        "verification_source": "preference_readback",
        "map_index": 0,
        "area_id": 1,
        "changed_fields": ["mowing_height_cm", "mowing_direction_degrees"],
        "readback": {
            "map": {"idx": 0, "mode": 0, "mode_name": "global"},
            "preference": {
                "area_id": 1,
                "version": 164,
                "reported_version": 164,
                "mowing_height_cm": 4.5,
                "mowing_direction_degrees": 134,
            },
        },
    }
    pending = retain_confirmed_preference_write(
        [],
        confirmed,
        confirmed_at=confirmed_at,
    )
    direct = {
        "available": True,
        "maps": [
            {
                "idx": 0,
                "mode": 0,
                "mode_name": "global",
                "preferences": [
                    {
                        "area_id": 1,
                        "version": 165,
                        "reported_version": 165,
                        "mowing_height_cm": 4.5,
                        "mowing_direction_degrees": 140,
                    }
                ],
            }
        ],
    }

    result, remaining = reconcile_pending_preference_readbacks(
        direct,
        pending,
        authoritative=True,
    )

    preference = result["maps"][0]["preferences"][0]
    assert preference["version"] == 165
    assert preference["reported_version"] == 165
    assert preference["mowing_height_cm"] == 4.5
    assert preference["mowing_direction_degrees"] == 140
    assert len(remaining) == 1
    assert remaining[0].field == "mowing_height_cm"
    assert remaining[0].version_values == {
        "version": 165,
        "reported_version": 165,
    }


def test_preference_reconciliation_listener_does_not_mask_write_error() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._preference_write_lock = asyncio.Lock()
    coordinator.last_preference_write_result = None
    coordinator.batch_device_data_refreshed_at = datetime.now(UTC)
    coordinator.async_update_listeners = Mock(
        side_effect=RuntimeError("listener failed")
    )
    coordinator.async_refresh_batch_device_data = AsyncMock(return_value={})
    coordinator.async_request_refresh = AsyncMock()
    attempted_error = RuntimeError("readback did not confirm")
    mark_write_attempted(attempted_error, fields=["preference_mode"])
    coordinator.client = SimpleNamespace(
        async_plan_app_mowing_preference_update=AsyncMock(side_effect=attempted_error)
    )

    with pytest.raises(RuntimeError, match="readback did not confirm"):
        asyncio.run(
            coordinator.async_plan_mowing_preference_update(
                map_index=1,
                area_id=None,
                changes={"preference_mode": "global"},
                execute=True,
                confirm_write=True,
            )
        )

    coordinator.async_refresh_batch_device_data.assert_awaited_once_with(
        force=True,
        source="mowing_preference_write",
    )
    coordinator.async_request_refresh.assert_awaited_once_with()
    coordinator.async_update_listeners.assert_called_once_with()


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
    confirmed_at = datetime.now(UTC)
    coordinator._pending_preference_confirmations = []
    for map_index in (0, 1):
        coordinator._pending_preference_confirmations = (
            retain_confirmed_preference_write(
                coordinator._pending_preference_confirmations,
                {
                    "executed": True,
                    "request_verified": True,
                    "verification_source": "preference_readback",
                    "map_index": map_index,
                    "area_id": None,
                    "changed_fields": ["preference_mode"],
                    "readback": {
                        "map": {
                            "idx": map_index,
                            "mode": 1,
                            "mode_name": "custom",
                        },
                        "preference": None,
                    },
                },
                confirmed_at=confirmed_at,
            )
        )

    asyncio.run(coordinator.async_refresh_app_maps(force=True))

    assert coordinator.selected_map_index is None
    assert coordinator.selected_contour_id is None
    assert coordinator.selected_zone_id is None
    assert coordinator.selected_spot_id is None
    assert coordinator.selected_maintenance_point_id is None
    assert [
        item.map_index for item in coordinator._pending_preference_confirmations
    ] == [0]


def test_app_map_refresh_preserves_confirmation_created_during_mapl_read() -> None:
    async def scenario() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        map_read_started = asyncio.Event()
        release_map_read = asyncio.Event()

        async def read_app_maps(**_kwargs):
            map_read_started.set()
            await release_map_read.wait()
            return {
                "current_map_index": 0,
                "map_list_valid": True,
                "maps": [{"idx": 0, "created": True}],
            }

        coordinator.client = SimpleNamespace(async_get_app_maps=read_app_maps)
        coordinator.app_maps = {
            "current_map_index": 0,
            "map_list_valid": True,
            "maps": [{"idx": 0, "created": True}, {"idx": 1, "created": True}],
        }
        coordinator.app_maps_refreshed_at = None
        coordinator.app_maps_refresh_succeeded = True
        coordinator.selected_map_index = 0
        coordinator.selected_contour_id = None
        coordinator.selected_zone_id = None
        coordinator.selected_spot_id = None
        coordinator.selected_maintenance_point_id = None
        coordinator._pending_preference_confirmations = []

        refresh = asyncio.create_task(coordinator.async_refresh_app_maps(force=True))
        await map_read_started.wait()
        coordinator._pending_preference_confirmations = (
            retain_confirmed_preference_write(
                [],
                {
                    "executed": True,
                    "request_verified": True,
                    "verification_source": "preference_readback",
                    "map_index": 1,
                    "area_id": None,
                    "changed_fields": ["preference_mode"],
                    "readback": {
                        "map": {"idx": 1, "mode": 1, "mode_name": "custom"},
                        "preference": None,
                    },
                },
                confirmed_at=datetime.now(UTC),
            )
        )
        release_map_read.set()
        await refresh

        assert [
            item.map_index for item in coordinator._pending_preference_confirmations
        ] == [1]

    asyncio.run(scenario())


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
    coordinator._pending_preference_confirmations = retain_confirmed_preference_write(
        [],
        {
            "executed": True,
            "request_verified": True,
            "verification_source": "preference_readback",
            "map_index": 1,
            "area_id": None,
            "changed_fields": ["preference_mode"],
            "readback": {
                "map": {"idx": 1, "mode": 1, "mode_name": "custom"},
                "preference": None,
            },
        },
        confirmed_at=datetime.now(UTC),
    )
    pending = coordinator._pending_preference_confirmations

    result = asyncio.run(coordinator.async_refresh_app_maps(force=True))

    assert result["map_list_valid"] is False
    assert coordinator.app_maps_refresh_succeeded is False
    assert coordinator._metadata_phase_needs_retry("app_maps", result)
    assert coordinator._pending_preference_confirmations is pending
