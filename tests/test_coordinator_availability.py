"""Coordinator availability regression checks."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from custom_components.dreame_lawn_mower.coordinator import (
    DreameLawnMowerCoordinator,
    _runtime_tracking_active,
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
