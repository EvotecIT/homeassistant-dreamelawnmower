"""Coordinator availability regression checks."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
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
        stop = asyncio.create_task(
            coordinator.async_shutdown_for_home_assistant_stop()
        )
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
        async_plan_app_mowing_preference_update=AsyncMock(
            side_effect=attempted_error
        )
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
        async_plan_app_mowing_preference_update=AsyncMock(
            side_effect=attempted_error
        )
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
        async_plan_app_mowing_preference_update=AsyncMock(return_value=confirmed)
    )
    return coordinator


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
    assert "reported_version" not in preferences[0]
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
            return (
                batch_schedule,
                {
                    "available": True,
                    "maps": [
                        {
                            "idx": 0,
                            "mode": 1,
                            "mode_name": "custom",
                            "preferences": [],
                        }
                    ],
                },
                {"available": True},
                0,
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
        assert result["batch_mowing_preferences"]["maps"][0]["mode_name"] == (
            "global"
        )

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
            return (
                batch_schedule,
                {
                    "available": True,
                    "maps": [
                        {
                            "idx": 0,
                            "mode": mode,
                            "mode_name": "global" if mode == 0 else "custom",
                            "preferences": [],
                        }
                    ],
                },
                {"available": True},
                0,
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
        now=confirmed_at + timedelta(seconds=5),
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
        now=confirmed_at + timedelta(seconds=5),
    )

    assert result["available"] is False
    assert result["errors"] == [{"stage": "settings", "error": "partial read"}]
    assert result["maps"][0]["mode_name"] == "global"
    assert result["maps"][1] is incoming["maps"][1]
    assert remaining == pending


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
        confirmed_at=confirmed_at + timedelta(minutes=3),
    )

    assert len(pending) == 1


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


def test_batch_freshness_expires_with_pending_preference_confirmation() -> None:
    confirmed_at = datetime.now(UTC)
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._pending_preference_confirmations = retain_confirmed_preference_write(
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

    refreshed_at = coordinator._batch_device_data_refreshed_at_for_preferences(
        confirmed_at + timedelta(seconds=30)
    )

    assert (
        refreshed_at + coordinator_module.BATCH_DEVICE_DATA_REFRESH_INTERVAL
        == confirmed_at + timedelta(minutes=2)
    )


def test_event_preference_refresh_preserves_recent_exact_confirmation() -> None:
    stale_preferences = {
        "available": True,
        "errors": [],
        "maps": [
            {
                "idx": 0,
                "mode": 1,
                "mode_name": "custom",
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
    coordinator.app_maps = {"maps": [{"idx": 0}]}
    coordinator.batch_device_data = None
    coordinator.client = SimpleNamespace(
        async_get_batch_mowing_preferences=AsyncMock(return_value=stale_preferences)
    )

    result = asyncio.run(
        coordinator.async_refresh_mowing_preferences(source="preference_event")
    )

    assert result is not None
    assert result["maps"][0]["mode_name"] == "global"
    assert coordinator.batch_device_data["batch_mowing_preferences"] is result


def test_preference_confirmation_clears_on_convergence_and_expires() -> None:
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
    converged = {
        "available": True,
        "maps": [
            {
                "idx": 0,
                "mode": 0,
                "mode_name": "global",
                "preferences": [],
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
                "preferences": [],
            }
        ],
    }

    converged_result, converged_pending = reconcile_pending_preference_readbacks(
        converged,
        pending,
        now=confirmed_at + timedelta(seconds=5),
    )
    expired_result, expired_pending = reconcile_pending_preference_readbacks(
        stale,
        pending,
        now=confirmed_at + timedelta(minutes=2, seconds=1),
    )

    assert converged_result is converged
    assert converged_pending == []
    assert expired_result is stale
    assert expired_pending == []


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
        async_plan_app_mowing_preference_update=AsyncMock(
            side_effect=attempted_error
        )
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
