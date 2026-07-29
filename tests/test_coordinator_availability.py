"""Coordinator availability regression checks."""

from __future__ import annotations

import asyncio
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
    coordinator.runtime_telemetry_cache = SimpleNamespace(update=Mock())
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
    )
    assert tracking_updates == [(status_blob, True, 2)]
    assert coordinator.runtime_status_blob is status_blob
    assert coordinator.bluetooth_connected is True
    coordinator.async_set_updated_data.assert_called_once_with(snapshot)
    assert coordinator._client_update_task is None


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
        runtime_started = asyncio.Event()
        release_runtime = asyncio.Event()

        async def runtime_status(*, refresh: bool, include_cloud: bool):
            assert refresh is False
            assert include_cloud is False
            runtime_started.set()
            await release_runtime.wait()
            return SimpleNamespace()

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
        coordinator.runtime_telemetry_cache = SimpleNamespace(update=Mock())
        coordinator.bluetooth_connected = None
        coordinator.client = SimpleNamespace(
            async_get_cached_snapshot=AsyncMock(return_value=cached_snapshot),
            async_refresh=AsyncMock(return_value=video_snapshot),
            async_refresh_authoritative_snapshot=AsyncMock(
                return_value=video_snapshot
            ),
            async_get_runtime_status_blob=runtime_status,
            async_get_bluetooth_connected=AsyncMock(return_value=True),
            update_runtime_live_tracking=Mock(),
        )

        with patch.object(
            DataUpdateCoordinator,
            "async_set_updated_data",
        ) as publish:
            cached_task = asyncio.create_task(
                coordinator._async_process_client_update()
            )
            await asyncio.wait_for(runtime_started.wait(), timeout=1)
            result = await coordinator.async_refresh_video_safety_state()
            release_runtime.set()
            await cached_task

        assert result is video_snapshot
        publish.assert_called_once_with(video_snapshot)
        assert coordinator.runtime_status_blob is None
        coordinator.runtime_telemetry_cache.update.assert_not_called()
        coordinator.client.update_runtime_live_tracking.assert_not_called()
        coordinator.client.async_get_bluetooth_connected.assert_not_awaited()
        assert coordinator.bluetooth_connected is None

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
        async_get_app_maps=AsyncMock(
            return_value={"current_map_index": 2, "maps": []}
        )
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
