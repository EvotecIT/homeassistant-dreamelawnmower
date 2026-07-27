"""Contract tests for configured maintenance-point controls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.dreame_lawn_mower.button import (
    DreameLawnMowerGoToMaintenancePointButton,
)
from custom_components.dreame_lawn_mower.select import (
    DreameLawnMowerMaintenancePointSelect,
)


def _coordinator(*, activity: str = "idle") -> SimpleNamespace:
    return SimpleNamespace(
        data=SimpleNamespace(
            activity=activity,
            available=True,
            battery_level=80,
            docked=activity == "docked",
            mowing_session_active=None,
            mowing=activity == "mowing",
            raw_attributes={},
            returning=False,
            state="charging" if activity == "docked" else "idle",
            task_status=None,
        ),
        app_maps={"current_map_index": 0, "maps": [{"idx": 0, "current": True}]},
        batch_device_data={},
        vector_map_details={
            "maps": [
                {
                    "map_index": 0,
                    "clean_points": [
                        {
                            "point_id": 301,
                            "label": "Maintenance Point #301",
                        },
                        {
                            "point_id": 302,
                            "label": "Maintenance Point #302",
                        },
                    ],
                }
            ]
        },
        selected_map_index=0,
        selected_maintenance_point_id=None,
        client=SimpleNamespace(async_go_to_maintenance_point=AsyncMock()),
        async_update_listeners=Mock(),
        last_update_success=True,
        async_refresh=AsyncMock(),
        async_request_refresh=AsyncMock(),
        app_maps_refreshed_at=None,
        vector_map_details_refreshed_at=None,
        async_refresh_app_maps=AsyncMock(),
        async_refresh_vector_map_details=AsyncMock(),
    )


def _a2_app_map_coordinator(*, activity: str = "idle") -> SimpleNamespace:
    coordinator = _coordinator(activity=activity)
    coordinator.app_maps = {
        "current_map_index": 0,
        "maps": [
            {
                "idx": 0,
                "current": True,
                "summary": {
                    "point_count": 2,
                    "maintenance_point_ids": [401, 402],
                    "point_type_codes": [1],
                },
            },
            {
                "idx": 1,
                "current": False,
                "summary": {
                    "point_count": 1,
                    "maintenance_point_ids": [999],
                    "point_type_codes": [1],
                },
            },
        ],
    }
    coordinator.vector_map_details = {
        "maps": [
            {
                "map_index": 0,
                "clean_points": [],
            }
        ]
    }
    return coordinator


def _complete_fresh_map_refresh(coordinator: SimpleNamespace) -> None:
    async def refresh_app_maps(**kwargs):
        coordinator.app_maps_refreshed_at = object()
        return coordinator.app_maps

    async def refresh_vector_map_details(**kwargs):
        coordinator.vector_map_details_refreshed_at = object()
        return coordinator.vector_map_details

    coordinator.async_refresh_app_maps.side_effect = refresh_app_maps
    coordinator.async_refresh_vector_map_details.side_effect = (
        refresh_vector_map_details
    )


def test_maintenance_point_select_uses_map_point_ids() -> None:
    coordinator = _coordinator()
    entity = object.__new__(DreameLawnMowerMaintenancePointSelect)
    entity.coordinator = coordinator

    assert entity.available is True
    assert entity.options == [
        "Maintenance Point #301",
        "Maintenance Point #302",
    ]
    assert entity.current_option == "Maintenance Point #301"

    asyncio.run(entity.async_select_option("Maintenance Point #302"))

    assert coordinator.selected_maintenance_point_id == 302
    coordinator.async_update_listeners.assert_called_once()
    assert entity.current_option == "Maintenance Point #302"


def test_maintenance_point_select_falls_back_to_current_a2_app_map_ids() -> None:
    coordinator = _a2_app_map_coordinator()
    entity = object.__new__(DreameLawnMowerMaintenancePointSelect)
    entity.coordinator = coordinator

    assert entity.available is True
    assert entity.options == [
        "Maintenance Point #401",
        "Maintenance Point #402",
    ]
    assert "Maintenance Point #999" not in entity.options

    asyncio.run(entity.async_select_option("Maintenance Point #402"))

    assert coordinator.selected_maintenance_point_id == 402
    coordinator.async_update_listeners.assert_called_once()


def test_vector_point_ids_remain_authoritative_over_app_map_fallback() -> None:
    coordinator = _coordinator()
    coordinator.app_maps["maps"][0]["summary"] = {
        "point_count": 1,
        "maintenance_point_ids": [999],
        "point_type_codes": [1],
    }
    entity = object.__new__(DreameLawnMowerMaintenancePointSelect)
    entity.coordinator = coordinator

    assert entity.options == [
        "Maintenance Point #301",
        "Maintenance Point #302",
    ]
    assert "Maintenance Point #999" not in entity.options


def test_go_to_maintenance_point_uses_selected_configured_id() -> None:
    coordinator = _coordinator(activity="docked")
    _complete_fresh_map_refresh(coordinator)
    coordinator.selected_maintenance_point_id = 302
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    assert entity.available is True

    asyncio.run(entity.async_press())

    coordinator.client.async_go_to_maintenance_point.assert_awaited_once_with(302)
    assert coordinator.async_refresh.await_count == 2
    coordinator.async_request_refresh.assert_awaited_once()


def test_go_to_maintenance_point_uses_fresh_a2_app_map_id() -> None:
    coordinator = _a2_app_map_coordinator(activity="docked")
    _complete_fresh_map_refresh(coordinator)
    coordinator.selected_maintenance_point_id = 402
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    assert entity.available is True

    asyncio.run(entity.async_press())

    coordinator.client.async_go_to_maintenance_point.assert_awaited_once_with(402)
    assert coordinator.async_refresh.await_count == 2
    coordinator.async_request_refresh.assert_awaited_once()


def test_go_to_maintenance_point_allows_reporter_inactive_paused_state() -> None:
    coordinator = _a2_app_map_coordinator(activity="paused")
    coordinator.data.state = "paused"
    coordinator.data.task_status = "idle"
    coordinator.data.mowing_session_active = False
    _complete_fresh_map_refresh(coordinator)
    coordinator.selected_maintenance_point_id = 402
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    assert entity.available is True

    asyncio.run(entity.async_press())

    coordinator.client.async_go_to_maintenance_point.assert_awaited_once_with(402)
    assert coordinator.async_refresh.await_count == 2


def test_go_to_maintenance_point_blocks_active_paused_session() -> None:
    coordinator = _a2_app_map_coordinator(activity="paused")
    coordinator.data.state = "paused"
    coordinator.data.task_status = "paused"
    coordinator.data.mowing_session_active = True
    _complete_fresh_map_refresh(coordinator)
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    assert entity.available is False
    with pytest.raises(ValueError, match="must be idle or docked"):
        asyncio.run(entity.async_press())

    coordinator.async_refresh_app_maps.assert_not_awaited()
    coordinator.client.async_go_to_maintenance_point.assert_not_awaited()


def test_go_to_maintenance_point_blocks_paused_state_without_session_evidence() -> None:
    coordinator = _a2_app_map_coordinator(activity="paused")
    coordinator.data.state = "paused"
    coordinator.data.mowing_session_active = None
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    assert entity.available is False
    with pytest.raises(ValueError, match="must be idle or docked"):
        asyncio.run(entity.async_press())

    coordinator.async_refresh_app_maps.assert_not_awaited()
    coordinator.client.async_go_to_maintenance_point.assert_not_awaited()


def test_go_to_maintenance_point_blocks_unavailable_snapshot() -> None:
    coordinator = _a2_app_map_coordinator(activity="idle")
    coordinator.data.available = False
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    assert entity.available is False
    with pytest.raises(ValueError, match="not available"):
        asyncio.run(entity.async_press())

    coordinator.async_refresh_app_maps.assert_not_awaited()
    coordinator.client.async_go_to_maintenance_point.assert_not_awaited()


def test_go_to_maintenance_point_blocks_failed_initial_state_refresh() -> None:
    coordinator = _a2_app_map_coordinator(activity="idle")
    coordinator.last_update_success = False
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    with pytest.raises(ValueError, match="Fresh mower state"):
        asyncio.run(entity.async_press())

    coordinator.async_refresh.assert_awaited_once()
    coordinator.async_refresh_app_maps.assert_not_awaited()
    coordinator.client.async_go_to_maintenance_point.assert_not_awaited()


def test_go_to_maintenance_point_blocks_failed_final_state_refresh() -> None:
    coordinator = _a2_app_map_coordinator(activity="idle")
    _complete_fresh_map_refresh(coordinator)
    refresh_count = 0

    async def refresh_state() -> None:
        nonlocal refresh_count
        refresh_count += 1
        coordinator.last_update_success = refresh_count == 1

    coordinator.async_refresh.side_effect = refresh_state
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    with pytest.raises(ValueError, match="Fresh mower state"):
        asyncio.run(entity.async_press())

    assert coordinator.async_refresh.await_count == 2
    coordinator.async_refresh_app_maps.assert_awaited_once()
    coordinator.async_refresh_vector_map_details.assert_awaited_once()
    coordinator.client.async_go_to_maintenance_point.assert_not_awaited()


@pytest.mark.parametrize(
    ("data_change", "message"),
    [
        ({"battery_level": 10}, "battery is low"),
        ({"raw_attributes": {"mapping": True}}, "while mapping"),
    ],
)
def test_go_to_maintenance_point_applies_shared_movement_safety_gate(
    data_change: dict[str, object],
    message: str,
) -> None:
    coordinator = _a2_app_map_coordinator(activity="docked")
    for name, value in data_change.items():
        setattr(coordinator.data, name, value)
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    assert entity.available is False
    with pytest.raises(ValueError, match=message):
        asyncio.run(entity.async_press())

    coordinator.async_refresh_app_maps.assert_not_awaited()
    coordinator.client.async_go_to_maintenance_point.assert_not_awaited()


def test_go_to_maintenance_point_revalidates_safety_after_map_refresh() -> None:
    coordinator = _a2_app_map_coordinator(activity="docked")
    _complete_fresh_map_refresh(coordinator)

    original_vector_refresh = coordinator.async_refresh_vector_map_details.side_effect

    async def refresh_vector_and_lower_battery(**kwargs):
        result = await original_vector_refresh(**kwargs)
        coordinator.data.battery_level = 10
        return result

    coordinator.async_refresh_vector_map_details.side_effect = (
        refresh_vector_and_lower_battery
    )
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    with pytest.raises(ValueError, match="battery is low"):
        asyncio.run(entity.async_press())

    assert coordinator.async_refresh.await_count == 2
    coordinator.client.async_go_to_maintenance_point.assert_not_awaited()


def test_app_map_fallback_rejects_unidentified_point_records() -> None:
    coordinator = _a2_app_map_coordinator()
    coordinator.app_maps["maps"][0]["summary"]["maintenance_point_ids"] = [
        True,
        0,
        -1,
        "403",
    ]
    entity = object.__new__(DreameLawnMowerMaintenancePointSelect)
    entity.coordinator = coordinator

    assert entity.available is False
    assert entity.options == []


def test_go_to_maintenance_point_is_blocked_during_mowing() -> None:
    coordinator = _coordinator(activity="mowing")
    _complete_fresh_map_refresh(coordinator)
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    assert entity.available is False
    with pytest.raises(ValueError, match="mower is active"):
        asyncio.run(entity.async_press())

    coordinator.client.async_go_to_maintenance_point.assert_not_awaited()


def test_go_to_maintenance_point_refuses_stale_map_metadata() -> None:
    coordinator = _coordinator(activity="docked")
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    with pytest.raises(ValueError, match="Fresh map metadata"):
        asyncio.run(entity.async_press())

    coordinator.client.async_go_to_maintenance_point.assert_not_awaited()


def test_go_to_maintenance_point_does_not_fallback_from_removed_selection() -> None:
    coordinator = _coordinator(activity="docked")
    coordinator.selected_maintenance_point_id = 999
    _complete_fresh_map_refresh(coordinator)
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    with pytest.raises(ValueError, match="No maintenance point"):
        asyncio.run(entity.async_press())

    coordinator.client.async_go_to_maintenance_point.assert_not_awaited()
