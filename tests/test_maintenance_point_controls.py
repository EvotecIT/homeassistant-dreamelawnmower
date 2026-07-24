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
        data=SimpleNamespace(activity=activity),
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
        async_request_refresh=AsyncMock(),
        app_maps_refreshed_at=None,
        vector_map_details_refreshed_at=None,
        async_refresh_app_maps=AsyncMock(),
        async_refresh_vector_map_details=AsyncMock(),
    )


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


def test_go_to_maintenance_point_uses_selected_configured_id() -> None:
    coordinator = _coordinator(activity="docked")
    _complete_fresh_map_refresh(coordinator)
    coordinator.selected_maintenance_point_id = 302
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    assert entity.available is True

    asyncio.run(entity.async_press())

    coordinator.client.async_go_to_maintenance_point.assert_awaited_once_with(302)
    assert coordinator.async_request_refresh.await_count == 2


def test_go_to_maintenance_point_is_blocked_during_mowing() -> None:
    coordinator = _coordinator(activity="mowing")
    _complete_fresh_map_refresh(coordinator)
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    assert entity.available is False
    with pytest.raises(ValueError, match="idle or docked"):
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
