"""Unit tests for current-map control selectors and start behavior."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.lawn_mower import LawnMowerActivity
from homeassistant.exceptions import HomeAssistantError

from custom_components.dreame_lawn_mower.control_options import (
    MOWING_ACTION_EDGE,
    MOWING_ACTION_SPOT,
    MOWING_ACTION_ZONE,
    active_map_index,
    current_contour_entries,
    current_spot_entries,
    current_zone_entries,
)
from custom_components.dreame_lawn_mower.coordinator import DreameLawnMowerCoordinator
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    DreameLawnMowerCommandRejectedError,
)
from custom_components.dreame_lawn_mower.lawn_mower import DreameLawnMower
from custom_components.dreame_lawn_mower.runtime_cache import (
    DreameLawnMowerRuntimeTelemetryCache,
    runtime_mission_session_started_at,
)
from custom_components.dreame_lawn_mower.select import (
    DreameLawnMowerEdgeSelect,
    DreameLawnMowerMapSelect,
    DreameLawnMowerMowingActionSelect,
    DreameLawnMowerSelectedMapRotationSelect,
    DreameLawnMowerSpotSelect,
    DreameLawnMowerZoneSelect,
)


def _batch_device_data() -> dict:
    return {
        "batch_mowing_preferences": {
            "maps": [
                {
                    "idx": 0,
                    "mode": 1,
                    "mode_name": "custom",
                    "area_count": 4,
                    "preferences": [
                        {"area_id": 0, "map_index": 0},
                        {
                            "area_id": 1,
                            "map_index": 0,
                            "reported_version": 8,
                            "mowing_height_cm": 4.0,
                            "efficient_mode_name": "efficient",
                            "mowing_direction_mode_name": "checkerboard",
                            "mowing_direction_degrees": 90,
                            "edge_mowing_auto": True,
                            "edge_mowing_walk_mode_name": "line",
                            "edge_mowing_obstacle_avoidance": True,
                            "cutter_position_name": "left",
                            "edge_mowing_num": 1,
                            "obstacle_avoidance_enabled": True,
                            "obstacle_avoidance_height_cm": 15,
                            "obstacle_avoidance_distance_cm": 20,
                            "obstacle_avoidance_ai_classes": ["people", "animals"],
                            "edge_mowing_safe": True,
                        },
                        {
                            "area_id": 3,
                            "map_index": 0,
                            "reported_version": 9,
                            "mowing_height_cm": 5.0,
                            "efficient_mode_name": "standard",
                            "mowing_direction_mode_name": "rotation",
                            "mowing_direction_method_name": "mow_at_angle",
                            "mowing_direction_degrees": 45,
                            "edge_mowing_auto": False,
                            "edge_mowing_walk_mode_name": "side",
                            "edge_mowing_obstacle_avoidance": False,
                            "cutter_position_name": "center",
                            "edge_mowing_num": 2,
                            "obstacle_avoidance_enabled": True,
                            "obstacle_avoidance_height_cm": 10,
                            "obstacle_avoidance_distance_cm": 15,
                            "obstacle_avoidance_ai_classes": ["animals"],
                            "edge_mowing_safe": False,
                        },
                        {"area_id": 201, "map_index": 0},
                    ],
                },
                {
                    "idx": 1,
                    "mode": 0,
                    "mode_name": "global",
                    "area_count": 3,
                    "preferences": [
                        {"area_id": 0, "map_index": 1},
                        {
                            "area_id": 5,
                            "map_index": 1,
                            "reported_version": 10,
                            "mowing_height_cm": 3.5,
                            "efficient_mode_name": "efficient",
                            "mowing_direction_mode_name": "rotation",
                            "mowing_direction_method_name": "mow_at_angle",
                            "mowing_direction_degrees": 10,
                            "edge_mowing_auto": True,
                            "edge_mowing_walk_mode_name": "line",
                            "edge_mowing_obstacle_avoidance": True,
                            "cutter_position_name": "center",
                            "edge_mowing_num": 1,
                            "obstacle_avoidance_enabled": True,
                            "obstacle_avoidance_height_cm": 5,
                            "obstacle_avoidance_distance_cm": 10,
                            "obstacle_avoidance_ai_classes": [
                                "people",
                                "animals",
                                "objects",
                            ],
                            "edge_mowing_safe": True,
                        },
                        {"area_id": 202, "map_index": 1},
                    ],
                },
            ]
        }
    }


def _app_maps(*, current_map_index: int = 0) -> dict:
    return {
        "current_map_index": current_map_index,
        "map_count": 2,
        "maps": [
            {
                "idx": 0,
                "current": current_map_index == 0,
                "summary": {"name": "Front Lawn"},
                "payload": {
                    "spot": [
                        {
                            "id": 1,
                            "data": [[0, 0], [10, 0], [10, 10], [0, 10]],
                        },
                        {
                            "id": 2,
                            "data": [[100, 100], [140, 100], [140, 140], [100, 140]],
                        },
                    ]
                },
            },
            {
                "idx": 1,
                "current": current_map_index == 1,
                "summary": {"name": "Back Lawn"},
                "payload": {
                    "spot": [
                        {
                            "id": 8,
                            "data": [[200, 200], [220, 200], [220, 220], [200, 220]],
                        }
                    ]
                },
            },
        ],
    }


def _vector_map_details() -> dict:
    return {
        "available": True,
        "available_map_count": 2,
        "map_names": ["Front Lawn Map", "Back Lawn Map"],
        "maps": [
            {
                "map_id": 1,
                "map_index": 0,
                "map_name": "Front Lawn Map",
                "zones": [
                    {"zone_id": 1, "name": "Front Garden"},
                    {"zone_id": 3, "name": None},
                ],
                "contour_ids": [[1, 0], [3, 0]],
                "contour_count": 2,
                "mow_path_count": 0,
                "mow_path_segment_count": 0,
                "mow_path_point_count": 0,
                "has_live_path": False,
            },
            {
                "map_id": 2,
                "map_index": 1,
                "map_name": "Back Lawn Map",
                "zones": [{"zone_id": 5, "name": "Back Orchard"}],
                "contour_ids": [[5, 0]],
                "contour_count": 1,
                "mow_path_count": 1,
                "mow_path_segment_count": 1,
                "mow_path_point_count": 3,
                "has_live_path": True,
            },
        ],
    }


def test_active_map_identity_prefers_refreshed_mower_state() -> None:
    assert (
        active_map_index(
            _app_maps(current_map_index=1),
            selected_map_index=0,
        )
        == 1
    )


def _snapshot(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "state": 1,
        "mower_state": "mowing",
        "state_name": "mowing",
        "mower_state_name": "mowing",
        "task_status": 1,
        "mowing_task_status": "working",
        "task_status_name": "working",
        "mowing_task_status_name": "working",
        "last_realtime_method": "properties",
        "error_code": 0,
        "error_name": None,
        "error_text": None,
        "error_display": None,
        "error_source": None,
        "raw_error_code": None,
        "realtime_error_code": None,
        "cleaning_mode": 0,
        "mowing_mode": 0,
        "cleaning_mode_name": "all_area",
        "mowing_mode_name": "all_area",
        "cleaned_area": 12.5,
        "mowed_area": 12.5,
        "cleaning_time": 34,
        "mowing_time": 34,
        "active_segment_count": 1,
        "current_zone_id": 3,
        "current_zone_name": "Zone #3",
        "child_lock": False,
        "online": True,
        "device_connected": True,
        "cloud_connected": True,
        "charging": False,
        "raw_charging": False,
        "started": True,
        "raw_started": True,
        "returning": False,
        "raw_returning": False,
        "docked": False,
        "raw_docked": False,
        "mapping_available": True,
        "scheduled_clean": False,
        "scheduled_mow": False,
        "shortcut_task": False,
        "serial_number": "SN123",
        "cloud_update_time": 123456,
        "capabilities": {"maps", "zones"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_lawn_mower_activity_reports_returning_to_dock() -> None:
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(data=_snapshot(activity="returning"))

    assert entity.activity is LawnMowerActivity.RETURNING


def test_current_zone_entries_filter_global_and_edge_area_ids() -> None:
    entries = current_zone_entries(_batch_device_data(), _app_maps())

    assert entries == [
        {
            "area_id": 1,
            "label": "Zone #1",
            "map_index": 0,
            "preference": {
                "area_id": 1,
                "map_index": 0,
                "reported_version": 8,
                "mowing_height_cm": 4.0,
                "efficient_mode_name": "efficient",
                "mowing_direction_mode_name": "checkerboard",
                "mowing_direction_degrees": 90,
                "edge_mowing_auto": True,
                "edge_mowing_walk_mode_name": "line",
                "edge_mowing_obstacle_avoidance": True,
                "cutter_position_name": "left",
                "edge_mowing_num": 1,
                "obstacle_avoidance_enabled": True,
                "obstacle_avoidance_height_cm": 15,
                "obstacle_avoidance_distance_cm": 20,
                "obstacle_avoidance_ai_classes": ["people", "animals"],
                "edge_mowing_safe": True,
            },
        },
        {
            "area_id": 3,
            "label": "Zone #3",
            "map_index": 0,
            "preference": {
                "area_id": 3,
                "map_index": 0,
                "reported_version": 9,
                "mowing_height_cm": 5.0,
                "efficient_mode_name": "standard",
                "mowing_direction_mode_name": "rotation",
                "mowing_direction_method_name": "mow_at_angle",
                "mowing_direction_degrees": 45,
                "edge_mowing_auto": False,
                "edge_mowing_walk_mode_name": "side",
                "edge_mowing_obstacle_avoidance": False,
                "cutter_position_name": "center",
                "edge_mowing_num": 2,
                "obstacle_avoidance_enabled": True,
                "obstacle_avoidance_height_cm": 10,
                "obstacle_avoidance_distance_cm": 15,
                "obstacle_avoidance_ai_classes": ["animals"],
                "edge_mowing_safe": False,
            },
        },
    ]


def test_current_zone_entries_use_vector_names_by_id_with_safe_fallback() -> None:
    entries = current_zone_entries(
        _batch_device_data(),
        _app_maps(),
        _vector_map_details(),
    )

    assert [entry["label"] for entry in entries] == [
        "Front Garden (#1)",
        "Zone #3",
    ]


def test_current_spot_entries_expose_spot_ids_and_centers() -> None:
    entries = current_spot_entries(_app_maps(), _batch_device_data())

    assert entries == [
        {
            "spot_id": 1,
            "label": "Spot #1",
            "center": (5, 5),
            "spot": {"id": 1, "data": [[0, 0], [10, 0], [10, 10], [0, 10]]},
        },
        {
            "spot_id": 2,
            "label": "Spot #2",
            "center": (120, 120),
            "spot": {
                "id": 2,
                "data": [[100, 100], [140, 100], [140, 140], [100, 140]],
            },
        },
    ]


def test_current_zone_entries_can_follow_selected_map_override() -> None:
    entries = current_zone_entries(
        _batch_device_data(),
        _app_maps(current_map_index=0),
        selected_map_index=1,
    )

    assert entries == [
        {
            "area_id": 5,
            "label": "Zone #5",
            "map_index": 1,
            "preference": {
                "area_id": 5,
                "map_index": 1,
                "reported_version": 10,
                "mowing_height_cm": 3.5,
                "efficient_mode_name": "efficient",
                "mowing_direction_mode_name": "rotation",
                "mowing_direction_method_name": "mow_at_angle",
                "mowing_direction_degrees": 10,
                "edge_mowing_auto": True,
                "edge_mowing_walk_mode_name": "line",
                "edge_mowing_obstacle_avoidance": True,
                "cutter_position_name": "center",
                "edge_mowing_num": 1,
                "obstacle_avoidance_enabled": True,
                "obstacle_avoidance_height_cm": 5,
                "obstacle_avoidance_distance_cm": 10,
                "obstacle_avoidance_ai_classes": [
                    "people",
                    "animals",
                    "objects",
                ],
                "edge_mowing_safe": True,
            },
        }
    ]


def test_current_spot_entries_can_follow_selected_map_override() -> None:
    entries = current_spot_entries(
        _app_maps(current_map_index=0),
        _batch_device_data(),
        selected_map_index=1,
    )

    assert entries == [
        {
            "spot_id": 8,
            "label": "Spot #8",
            "center": (210, 210),
            "spot": {"id": 8, "data": [[200, 200], [220, 200], [220, 220], [200, 220]]},
        }
    ]


def test_current_contour_entries_can_follow_selected_map_override() -> None:
    entries = current_contour_entries(
        _vector_map_details(),
        _app_maps(current_map_index=0),
        _batch_device_data(),
        selected_map_index=1,
    )

    assert entries == [
        {
            "contour_id": (5, 0),
            "label": "Edge (5, 0)",
            "map_index": 1,
        }
    ]


def test_map_select_switches_active_mower_map() -> None:
    entity = object.__new__(DreameLawnMowerMapSelect)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(current_map_index=1),
        selected_map_index=None,
        async_switch_current_map=AsyncMock(),
    )

    assert entity.options == ["Front Lawn (#1)", "Back Lawn (#2)"]
    assert entity.current_option == "Back Lawn (#2)"

    asyncio.run(entity.async_select_option("Front Lawn (#1)"))

    entity.coordinator.async_switch_current_map.assert_awaited_once_with(0)


def test_selected_map_rotation_is_persisted_per_map() -> None:
    update_entry = Mock()
    entity = object.__new__(DreameLawnMowerSelectedMapRotationSelect)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(current_map_index=1),
        selected_map_index=None,
        entry=SimpleNamespace(
            options={"map_rotation": 180, "map_rotations": {"0": 90}}
        ),
        hass=SimpleNamespace(
            config_entries=SimpleNamespace(async_update_entry=update_entry)
        ),
        async_update_listeners=Mock(),
    )

    assert entity.current_option == "180 degrees"

    asyncio.run(entity.async_select_option("270 degrees clockwise"))

    update_entry.assert_called_once_with(
        entity.coordinator.entry,
        options={
            "map_rotation": 180,
            "map_rotations": {"0": 90, "1": 270},
        },
    )
    entity.coordinator.async_update_listeners.assert_called_once()


def test_coordinator_switch_current_map_updates_device_and_scoped_caches() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.client = SimpleNamespace(async_switch_current_map=AsyncMock())
    coordinator.selected_map_index = 0
    coordinator.schedules_refreshed_at = object()
    coordinator.schedules = {"active_selection_available": True}
    coordinator.selected_contour_id = (3, 0)
    coordinator.selected_zone_id = 3
    coordinator.selected_spot_id = 2
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_refresh_app_maps = AsyncMock()
    coordinator.async_refresh_vector_map_details = AsyncMock()
    coordinator.async_update_listeners = lambda: None

    asyncio.run(coordinator.async_switch_current_map(1))

    coordinator.client.async_switch_current_map.assert_awaited_once_with(1)
    coordinator.async_request_refresh.assert_awaited_once()
    coordinator.async_refresh_app_maps.assert_awaited_once_with(
        force=True,
        source="app_maps_switch_current_map",
    )
    coordinator.async_refresh_vector_map_details.assert_awaited_once_with(
        force=True,
        source="vector_map_switch_current_map",
    )
    assert coordinator.selected_map_index == 1
    assert coordinator.selected_contour_id is None
    assert coordinator.selected_zone_id is None
    assert coordinator.selected_spot_id is None
    assert coordinator.schedules_refreshed_at is None
    assert coordinator.schedules["active_selection_available"] is False


def test_app_map_refresh_invalidates_schedule_selection_after_external_switch() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.client = SimpleNamespace(
        async_get_app_maps=AsyncMock(
            return_value={
                "current_map_index": 1,
                "maps": [
                    {"idx": 0, "created": True},
                    {"idx": 1, "created": True},
                ],
                "map_list_valid": True,
            }
        )
    )
    coordinator.app_maps = {"current_map_index": 0}
    coordinator.app_maps_refreshed_at = None
    coordinator.app_maps_refresh_succeeded = True
    coordinator.selected_map_index = 0
    coordinator.selected_contour_id = None
    coordinator.selected_zone_id = None
    coordinator.selected_spot_id = None
    coordinator.schedules_refreshed_at = object()
    coordinator.schedules = {"active_selection_available": True}

    asyncio.run(coordinator.async_refresh_app_maps(force=True))

    assert coordinator.selected_map_index == 1
    assert coordinator.schedules_refreshed_at is None
    assert coordinator.schedules["active_selection_available"] is False


def test_app_map_refresh_invalidates_schedule_after_final_map_deletion() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.client = SimpleNamespace(
        async_get_app_maps=AsyncMock(
            return_value={
                "current_map_index": None,
                "maps": [],
                "map_list_valid": True,
            }
        )
    )
    coordinator.app_maps = {"current_map_index": 0}
    coordinator.app_maps_refreshed_at = None
    coordinator.app_maps_refresh_succeeded = True
    coordinator.selected_map_index = 0
    coordinator.selected_contour_id = (3, 0)
    coordinator.selected_zone_id = 3
    coordinator.selected_spot_id = 2
    coordinator.schedules_refreshed_at = object()
    coordinator.schedules = {"active_selection_available": True}

    asyncio.run(coordinator.async_refresh_app_maps(force=True))

    assert coordinator.selected_map_index is None
    assert coordinator.selected_contour_id is None
    assert coordinator.selected_zone_id is None
    assert coordinator.selected_spot_id is None
    assert coordinator.schedules_refreshed_at is None
    assert coordinator.schedules["active_selection_available"] is False


def test_app_map_refresh_clears_selected_map_absent_from_inventory() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.client = SimpleNamespace(
        async_get_app_maps=AsyncMock(
            return_value={
                "current_map_index": None,
                "maps": [{"idx": 0, "created": True}],
                "map_list_valid": True,
            }
        )
    )
    coordinator.app_maps = {
        "current_map_index": 1,
        "maps": [
            {"idx": 0, "created": True},
            {"idx": 1, "created": True},
        ],
        "map_list_valid": True,
    }
    coordinator.app_maps_refreshed_at = None
    coordinator.app_maps_refresh_succeeded = True
    coordinator.selected_map_index = 1
    coordinator.selected_contour_id = (3, 0)
    coordinator.selected_zone_id = 3
    coordinator.selected_spot_id = 2
    coordinator.schedules_refreshed_at = object()
    coordinator.schedules = {"active_selection_available": True}

    asyncio.run(coordinator.async_refresh_app_maps(force=True))

    assert coordinator.selected_map_index is None
    assert coordinator.selected_contour_id is None
    assert coordinator.selected_zone_id is None
    assert coordinator.selected_spot_id is None
    assert coordinator.schedules_refreshed_at is None
    assert coordinator.schedules["active_selection_available"] is False


def test_app_map_refresh_invalidates_schedules_when_inventory_changes() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.client = SimpleNamespace(
        async_get_app_maps=AsyncMock(
            return_value={
                "current_map_index": 0,
                "maps": [{"idx": 0, "created": True}],
                "map_list_valid": True,
            }
        )
    )
    coordinator.app_maps = {
        "current_map_index": 0,
        "maps": [
            {"idx": 0, "created": True},
            {"idx": 1, "created": True},
        ],
        "map_list_valid": True,
    }
    coordinator.app_maps_refreshed_at = None
    coordinator.app_maps_refresh_succeeded = True
    coordinator.selected_map_index = 0
    coordinator.selected_contour_id = None
    coordinator.selected_zone_id = None
    coordinator.selected_spot_id = None
    coordinator.schedules_refreshed_at = object()
    coordinator.schedules = {"active_selection_available": True}

    asyncio.run(coordinator.async_refresh_app_maps(force=True))

    assert coordinator.selected_map_index == 0
    assert coordinator.schedules_refreshed_at is None
    assert coordinator.schedules["active_selection_available"] is False


def test_app_map_refresh_invalidates_when_hints_become_authoritative() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.client = SimpleNamespace(
        async_get_app_maps=AsyncMock(
            return_value={
                "current_map_index": 0,
                "maps": [{"idx": 0, "created": True}],
                "map_list_valid": True,
            }
        )
    )
    coordinator.app_maps = {
        "current_map_index": 0,
        "maps": [{"idx": 0, "created": True}],
        "map_list_valid": False,
    }
    coordinator.app_maps_refreshed_at = None
    coordinator.app_maps_refresh_succeeded = True
    coordinator.selected_map_index = 0
    coordinator.selected_contour_id = None
    coordinator.selected_zone_id = None
    coordinator.selected_spot_id = None
    coordinator.schedules_refreshed_at = object()
    coordinator.schedules = {"active_selection_available": True}

    asyncio.run(coordinator.async_refresh_app_maps(force=True))

    assert coordinator.selected_map_index == 0
    assert coordinator.schedules_refreshed_at is None
    assert coordinator.schedules["active_selection_available"] is False


def test_zone_select_sets_zone_and_switches_action() -> None:
    entity = object.__new__(DreameLawnMowerZoneSelect)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(),
        vector_map_details=_vector_map_details(),
        selected_map_index=None,
        selected_zone_id=None,
        selected_mowing_action="all_area",
        async_update_listeners=lambda: None,
    )

    assert entity.options == ["Front Garden (#1)", "Zone #3"]
    assert entity.current_option == "Front Garden (#1)"

    asyncio.run(entity.async_select_option("Zone #3"))

    assert entity.coordinator.selected_zone_id == 3
    assert entity.coordinator.selected_mowing_action == MOWING_ACTION_ZONE


def test_spot_select_sets_spot_and_switches_action() -> None:
    entity = object.__new__(DreameLawnMowerSpotSelect)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(),
        selected_map_index=None,
        selected_spot_id=None,
        selected_mowing_action="all_area",
        async_update_listeners=lambda: None,
    )

    assert entity.options == ["Spot #1", "Spot #2"]
    assert entity.current_option == "Spot #1"

    asyncio.run(entity.async_select_option("Spot #2"))

    assert entity.coordinator.selected_spot_id == 2
    assert entity.coordinator.selected_mowing_action == MOWING_ACTION_SPOT


def test_edge_select_sets_contour_and_switches_action() -> None:
    entity = object.__new__(DreameLawnMowerEdgeSelect)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        vector_map_details=_vector_map_details(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(),
        selected_map_index=None,
        selected_contour_id=None,
        selected_mowing_action="all_area",
        async_update_listeners=lambda: None,
    )

    assert entity.options == ["Edge (1, 0)", "Edge (3, 0)"]
    assert entity.current_option == "Edge (1, 0)"

    asyncio.run(entity.async_select_option("Edge (3, 0)"))

    assert entity.coordinator.selected_contour_id == (3, 0)
    assert entity.coordinator.selected_mowing_action == MOWING_ACTION_EDGE


def test_mowing_action_select_updates_current_action() -> None:
    entity = object.__new__(DreameLawnMowerMowingActionSelect)
    entity.coordinator = SimpleNamespace(
        selected_mowing_action="all_area",
        async_update_listeners=lambda: None,
    )

    assert entity.current_option == "All area"

    asyncio.run(entity.async_select_option("Spot"))

    assert entity.coordinator.selected_mowing_action == "spot"
    assert entity.current_option == "Spot"


def test_zone_select_uses_selected_map_scope() -> None:
    entity = object.__new__(DreameLawnMowerZoneSelect)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(current_map_index=0),
        vector_map_details=_vector_map_details(),
        selected_map_index=1,
        selected_zone_id=None,
        selected_mowing_action="all_area",
        async_update_listeners=lambda: None,
    )

    assert entity.options == ["Back Orchard (#5)"]
    assert entity.current_option == "Back Orchard (#5)"


def test_spot_select_uses_selected_map_scope() -> None:
    entity = object.__new__(DreameLawnMowerSpotSelect)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(current_map_index=0),
        selected_map_index=1,
        selected_spot_id=None,
        selected_mowing_action="all_area",
        async_update_listeners=lambda: None,
    )

    assert entity.options == ["Spot #8"]
    assert entity.current_option == "Spot #8"


def test_edge_select_uses_selected_map_scope() -> None:
    entity = object.__new__(DreameLawnMowerEdgeSelect)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(),
        vector_map_details=_vector_map_details(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(current_map_index=0),
        selected_map_index=1,
        selected_contour_id=None,
        selected_mowing_action="all_area",
        async_update_listeners=lambda: None,
    )

    assert entity.options == ["Edge (5, 0)"]
    assert entity.current_option == "Edge (5, 0)"


def test_lawn_mower_attributes_include_current_vector_map_state() -> None:
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        data=_snapshot(),
        client=SimpleNamespace(device=SimpleNamespace()),
        selected_mowing_action=MOWING_ACTION_EDGE,
        selected_map_index=None,
        selected_contour_id=(5, 0),
        selected_zone_id=None,
        selected_spot_id=None,
        vector_map_details=_vector_map_details(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(current_map_index=1),
    )

    attributes = entity.extra_state_attributes

    assert attributes["vector_map_available"] is True
    assert attributes["available_vector_map_count"] == 2
    assert attributes["available_vector_map_names"] == [
        "Front Lawn Map",
        "Back Lawn Map",
    ]
    assert attributes["selected_map_preference_available"] is True
    assert attributes["selected_map_preference"] == {
        "map_index": 1,
        "label": "Back Lawn (#2)",
        "mode": 0,
        "mode_name": "global",
        "area_count": 3,
        "preference_count": 3,
    }
    assert attributes["selected_map_preference_mode"] == "global"
    assert attributes["selected_map_preference_area_count"] == 3
    assert attributes["selected_map_preference_count"] == 3
    assert attributes["selected_zone_preference_available"] is True
    assert attributes["selected_zone_preference"] == {
        "map_index": 1,
        "area_id": 5,
        "label": "Back Orchard (#5)",
        "mode": 0,
        "mode_name": "global",
        "reported_version": 10,
        "mowing_height_cm": 3.5,
        "efficient_mode_name": "efficient",
        "mowing_direction_mode_name": "rotation",
        "mowing_direction_method_name": "mow_at_angle",
        "mowing_direction_degrees": 10,
        "edge_mowing_auto": True,
        "edge_mowing_walk_mode_name": "line",
        "edge_mowing_obstacle_avoidance": True,
        "cutter_position_name": "center",
        "edge_mowing_num": 1,
        "obstacle_avoidance_enabled": True,
        "obstacle_avoidance_height_cm": 5,
        "obstacle_avoidance_distance_cm": 10,
        "obstacle_avoidance_ai_classes": ["people", "animals", "objects"],
        "edge_mowing_safe": True,
    }
    assert attributes["app_current_map_label"] == "Back Lawn (#2)"
    assert attributes["current_vector_map_id"] == 2
    assert attributes["current_vector_map_name"] == "Back Lawn Map"
    assert attributes["current_vector_map_contour_count"] == 1
    assert attributes["current_vector_map_has_live_path"] is True
    assert attributes["current_vector_map_mow_path_point_count"] == 3
    assert attributes["mowing_mode"] == 0
    assert attributes["mowing_mode_name"] == "all_area"
    assert attributes["mowed_area"] == 12.5
    assert attributes["mowing_time"] == 34
    assert attributes["scheduled_mow"] is False
    assert attributes["cleaning_mode"] == 0
    assert attributes["cleaned_area"] == 12.5
    assert attributes["cleaning_time"] == 34
    assert attributes["scheduled_clean"] is False


def test_lawn_mower_attributes_fallback_to_vector_map_ids_for_names() -> None:
    vector_map_details = _vector_map_details()
    vector_map_details["map_names"] = None
    for entry in vector_map_details["maps"]:
        entry["map_name"] = None

    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        data=_snapshot(),
        client=SimpleNamespace(device=SimpleNamespace()),
        selected_mowing_action=MOWING_ACTION_EDGE,
        selected_map_index=None,
        selected_contour_id=(5, 0),
        selected_zone_id=None,
        selected_spot_id=None,
        vector_map_details=vector_map_details,
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(current_map_index=1),
    )

    attributes = entity.extra_state_attributes

    assert attributes["available_vector_map_names"] == ["Map 1", "Map 2"]
    assert attributes["current_vector_map_name"] == "Map 2"


def test_lawn_mower_start_uses_selected_edge() -> None:
    client = SimpleNamespace(
        async_start_edge_mowing=AsyncMock(),
        async_start_zone_mowing=AsyncMock(),
        async_start_spot_mowing=AsyncMock(),
        async_start_mowing=AsyncMock(),
    )
    entity = object.__new__(DreameLawnMower)
    previous_blob = SimpleNamespace(candidate_runtime_area_progress_percent=42.0)
    cache = DreameLawnMowerRuntimeTelemetryCache()
    assert cache.update(previous_blob, active_session=True) is True
    entity.coordinator = SimpleNamespace(
        client=client,
        selected_mowing_action=MOWING_ACTION_EDGE,
        selected_map_index=None,
        selected_contour_id=(3, 0),
        selected_zone_id=None,
        selected_spot_id=None,
        vector_map_details=_vector_map_details(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(),
        runtime_telemetry_cache=cache,
        async_request_refresh=AsyncMock(),
    )

    asyncio.run(entity.async_start_mowing())

    client.async_start_edge_mowing.assert_awaited_once_with([[3, 0]])
    client.async_start_zone_mowing.assert_not_called()
    client.async_start_spot_mowing.assert_not_called()
    client.async_start_mowing.assert_not_called()
    assert cache.blob is None
    assert runtime_mission_session_started_at(cache) is not None
    entity.coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.parametrize("start_result", (False, None))
def test_lawn_mower_resume_or_unknown_preserves_runtime_session_cache(
    start_result: bool | None,
) -> None:
    client = SimpleNamespace(async_start_mowing=AsyncMock(return_value=start_result))
    previous_blob = SimpleNamespace(candidate_runtime_area_progress_percent=42.0)
    cache = DreameLawnMowerRuntimeTelemetryCache()
    assert cache.update(previous_blob, active_session=True) is True
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=client,
        selected_mowing_action="all_area",
        runtime_telemetry_cache=cache,
        async_request_refresh=AsyncMock(),
    )

    asyncio.run(entity.async_start_mowing())

    assert cache.blob is previous_blob
    entity.coordinator.async_request_refresh.assert_awaited_once()


def test_lawn_mower_start_preserves_callback_telemetry_before_command_return() -> None:
    """Command acceptance cannot erase a mission already observed by MQTT."""
    previous_blob = SimpleNamespace(candidate_runtime_area_progress_percent=100.0)
    current_blob = SimpleNamespace(candidate_runtime_area_progress_percent=3.0)
    cache = DreameLawnMowerRuntimeTelemetryCache()
    assert cache.update(previous_blob, completion_confirmed=True) is True

    async def accept_after_callback() -> bool:
        cache.observe_session_state(active_session=True)
        assert cache.update(current_blob, active_session=True) is True
        return True

    client = SimpleNamespace(
        async_start_mowing=AsyncMock(side_effect=accept_after_callback)
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=client,
        selected_mowing_action="all_area",
        runtime_telemetry_cache=cache,
        async_request_refresh=AsyncMock(),
    )

    asyncio.run(entity.async_start_mowing())

    assert cache.blob is current_blob
    entity.coordinator.async_request_refresh.assert_awaited_once()


def test_lawn_mower_records_command_boundary_after_acceptance() -> None:
    """A delayed prior notice cannot postdate the accepted mission boundary."""
    command_accepted = False

    async def accept_start() -> bool:
        nonlocal command_accepted
        command_accepted = True
        return True

    def accepted_at() -> float:
        assert command_accepted is True
        return 20.0

    cache = DreameLawnMowerRuntimeTelemetryCache()
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=SimpleNamespace(async_start_mowing=AsyncMock(side_effect=accept_start)),
        selected_mowing_action="all_area",
        runtime_telemetry_cache=cache,
        async_request_refresh=AsyncMock(),
    )

    with patch(
        "custom_components.dreame_lawn_mower.lawn_mower.time",
        side_effect=accepted_at,
    ):
        asyncio.run(entity.async_start_mowing())

    assert runtime_mission_session_started_at(cache) == 20.0


def test_lawn_mower_start_edge_service_uses_explicit_contours() -> None:
    client = SimpleNamespace(
        async_start_edge_mowing=AsyncMock(),
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=client,
        app_maps=_app_maps(),
        batch_device_data=_batch_device_data(),
        vector_map_details=_vector_map_details(),
        selected_map_index=None,
        selected_mowing_action="all_area",
        selected_contour_id=None,
        selected_zone_id=3,
        selected_spot_id=2,
        async_request_refresh=AsyncMock(),
    )

    asyncio.run(entity.async_start_edge_mowing([[1, 0], ("3", "0")]))

    client.async_start_edge_mowing.assert_awaited_once_with([[1, 0], [3, 0]])
    assert entity.coordinator.selected_mowing_action == MOWING_ACTION_EDGE
    assert entity.coordinator.selected_contour_id is None
    assert entity.coordinator.selected_zone_id is None
    assert entity.coordinator.selected_spot_id is None
    entity.coordinator.async_request_refresh.assert_awaited_once()


def test_lawn_mower_start_edge_service_requires_valid_pairs() -> None:
    client = SimpleNamespace(
        async_start_edge_mowing=AsyncMock(),
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=client,
        async_request_refresh=AsyncMock(),
    )

    with pytest.raises(HomeAssistantError, match="two-item list"):
        asyncio.run(entity.async_start_edge_mowing([[7]]))

    client.async_start_edge_mowing.assert_not_called()


def test_lawn_mower_start_edge_service_rejects_unknown_active_map_contour() -> None:
    client = SimpleNamespace(async_start_edge_mowing=AsyncMock())
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=client,
        app_maps=_app_maps(),
        batch_device_data=_batch_device_data(),
        vector_map_details=_vector_map_details(),
        selected_map_index=None,
        async_request_refresh=AsyncMock(),
    )

    with pytest.raises(HomeAssistantError, match=r"Edge contour \(7, 1\)"):
        asyncio.run(entity.async_start_edge_mowing([[7, 1]]))

    client.async_start_edge_mowing.assert_not_called()
    entity.coordinator.async_request_refresh.assert_not_awaited()


def test_lawn_mower_switch_current_map_service_updates_scope_and_refreshes() -> None:
    coordinator = SimpleNamespace(
        app_maps=_app_maps(current_map_index=0),
        batch_device_data=_batch_device_data(),
        async_switch_current_map=AsyncMock(),
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = coordinator

    asyncio.run(entity.async_switch_current_map(1))

    coordinator.async_switch_current_map.assert_awaited_once_with(1)


def test_lawn_mower_switch_current_map_service_rejects_unknown_map_index() -> None:
    client = SimpleNamespace(
        async_switch_current_map=AsyncMock(),
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=client,
        app_maps=_app_maps(current_map_index=0),
        batch_device_data=_batch_device_data(),
        selected_map_index=0,
        selected_contour_id=(3, 0),
        selected_zone_id=3,
        selected_spot_id=2,
        async_request_refresh=AsyncMock(),
        async_refresh_app_maps=AsyncMock(),
        async_refresh_vector_map_details=AsyncMock(),
        async_update_listeners=lambda: None,
    )

    with pytest.raises(HomeAssistantError, match="Map index 7 is not available"):
        asyncio.run(entity.async_switch_current_map(7))

    client.async_switch_current_map.assert_not_called()


def test_lawn_mower_plan_zone_preference_update_uses_selected_scope() -> None:
    client = SimpleNamespace(
        async_plan_app_mowing_preference_update=AsyncMock(
            return_value={
                "source": "app_action_mowing_preference_write",
                "action": "plan_mowing_preference_update",
                "map_index": 1,
                "area_id": 5,
                "changed_fields": ["mowing_height_cm"],
            }
        ),
    )
    coordinator = SimpleNamespace(
        client=client,
        app_maps=_app_maps(current_map_index=0),
        batch_device_data=_batch_device_data(),
        selected_map_index=1,
        selected_contour_id=None,
        selected_zone_id=5,
        selected_spot_id=None,
        last_preference_write_result=None,
        async_request_refresh=AsyncMock(),
        async_update_listeners=lambda: None,
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = coordinator

    asyncio.run(
        entity.async_plan_zone_mowing_preference_update(
            mowing_height_cm=4.5,
            edge_mowing_auto=False,
        )
    )

    client.async_plan_app_mowing_preference_update.assert_awaited_once_with(
        map_index=1,
        area_id=5,
        changes={
            "mowing_height_cm": 4.5,
            "edge_mowing_auto": False,
        },
        execute=False,
        confirm_write=False,
    )
    assert coordinator.last_preference_write_result["selection_scope"] == {
        "selected_map_index": 1,
        "selected_map_label": "Back Lawn (#2)",
        "selected_zone_id": 5,
        "selected_zone_label": "Zone #5",
    }
    coordinator.async_request_refresh.assert_not_awaited()


def test_lawn_mower_plan_zone_preference_update_can_execute_confirmed_write() -> None:
    client = SimpleNamespace(
        async_plan_app_mowing_preference_update=AsyncMock(
            return_value={
                "source": "app_action_mowing_preference_write",
                "action": "plan_mowing_preference_update",
                "map_index": 1,
                "area_id": 5,
                "executed": True,
                "response_data": {"r": 0},
                "changed_fields": ["mowing_height_cm"],
            }
        ),
    )
    coordinator = SimpleNamespace(
        client=client,
        app_maps=_app_maps(current_map_index=0),
        batch_device_data=_batch_device_data(),
        selected_map_index=1,
        selected_contour_id=None,
        selected_zone_id=5,
        selected_spot_id=None,
        last_preference_write_result=None,
        async_refresh_batch_device_data=AsyncMock(),
        async_request_refresh=AsyncMock(),
        async_update_listeners=lambda: None,
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = coordinator

    asyncio.run(
        entity.async_plan_zone_mowing_preference_update(
            mowing_height_cm=4.5,
            execute=True,
            confirm_preference_write=True,
        )
    )

    client.async_plan_app_mowing_preference_update.assert_awaited_once_with(
        map_index=1,
        area_id=5,
        changes={
            "mowing_height_cm": 4.5,
        },
        execute=True,
        confirm_write=True,
    )
    coordinator.async_refresh_batch_device_data.assert_awaited_once_with(
        force=True,
        source="mowing_preference_write",
    )
    coordinator.async_request_refresh.assert_awaited_once()


def test_lawn_mower_plan_zone_preference_update_blocks_unconfirmed_write() -> None:
    client = SimpleNamespace(
        async_plan_app_mowing_preference_update=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        client=client,
        app_maps=_app_maps(current_map_index=0),
        batch_device_data=_batch_device_data(),
        selected_map_index=1,
        selected_contour_id=None,
        selected_zone_id=5,
        selected_spot_id=None,
        last_preference_write_result=None,
        async_request_refresh=AsyncMock(),
        async_update_listeners=lambda: None,
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = coordinator

    with pytest.raises(HomeAssistantError, match="confirm_preference_write"):
        asyncio.run(
            entity.async_plan_zone_mowing_preference_update(
                mowing_height_cm=4.5,
                execute=True,
                confirm_preference_write=False,
            )
        )

    client.async_plan_app_mowing_preference_update.assert_not_called()


def test_lawn_mower_plan_zone_preference_update_rejects_unknown_zone() -> None:
    client = SimpleNamespace(
        async_plan_app_mowing_preference_update=AsyncMock(),
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=client,
        app_maps=_app_maps(current_map_index=0),
        batch_device_data=_batch_device_data(),
        selected_map_index=1,
        selected_contour_id=None,
        selected_zone_id=5,
        selected_spot_id=None,
        last_preference_write_result=None,
        async_update_listeners=lambda: None,
    )

    with pytest.raises(HomeAssistantError, match="Zone #9 is not available"):
        asyncio.run(
            entity.async_plan_zone_mowing_preference_update(
                zone_id=9,
                mowing_height_cm=4.5,
            )
        )

    client.async_plan_app_mowing_preference_update.assert_not_called()


def test_lawn_mower_plan_zone_preference_update_allows_map_mode_only_request() -> None:
    client = SimpleNamespace(
        async_plan_app_mowing_preference_update=AsyncMock(
            return_value={
                "source": "app_action_mowing_preference_write",
                "action": "plan_mowing_preference_update",
                "map_index": 1,
                "area_id": None,
                "changed_fields": ["preference_mode"],
                "target_mode_name": "custom",
            }
        ),
    )
    coordinator = SimpleNamespace(
        client=client,
        app_maps=_app_maps(current_map_index=1),
        batch_device_data={"batch_mowing_preferences": {"maps": []}},
        selected_map_index=1,
        selected_contour_id=None,
        selected_zone_id=None,
        selected_spot_id=None,
        last_preference_write_result=None,
        async_request_refresh=AsyncMock(),
        async_update_listeners=lambda: None,
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = coordinator

    asyncio.run(
        entity.async_plan_zone_mowing_preference_update(
            preference_mode="custom",
        )
    )

    client.async_plan_app_mowing_preference_update.assert_awaited_once_with(
        map_index=1,
        area_id=None,
        changes={"preference_mode": "custom"},
        execute=False,
        confirm_write=False,
    )
    assert coordinator.last_preference_write_result["selection_scope"] == {
        "selected_map_index": 1,
        "selected_map_label": "Back Lawn (#2)",
    }
    coordinator.async_request_refresh.assert_not_awaited()


def test_lawn_mower_plan_map_preference_mode_update_uses_selected_map() -> None:
    client = SimpleNamespace(
        async_plan_app_mowing_preference_update=AsyncMock(
            return_value={
                "source": "app_action_mowing_preference_write",
                "action": "plan_mowing_preference_update",
                "map_index": 1,
                "area_id": None,
                "changed_fields": ["preference_mode"],
                "target_mode_name": "custom",
            }
        ),
    )
    coordinator = SimpleNamespace(
        client=client,
        app_maps=_app_maps(current_map_index=1),
        batch_device_data={"batch_mowing_preferences": {"maps": []}},
        selected_map_index=1,
        selected_contour_id=None,
        selected_zone_id=5,
        selected_spot_id=None,
        last_preference_write_result=None,
        async_request_refresh=AsyncMock(),
        async_update_listeners=lambda: None,
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = coordinator

    asyncio.run(entity.async_plan_map_preference_mode_update("custom"))

    client.async_plan_app_mowing_preference_update.assert_awaited_once_with(
        map_index=1,
        area_id=None,
        changes={"preference_mode": 1},
        execute=False,
        confirm_write=False,
    )
    assert coordinator.last_preference_write_result["selection_scope"] == {
        "selected_map_index": 1,
        "selected_map_label": "Back Lawn (#2)",
    }
    coordinator.async_request_refresh.assert_not_awaited()


def test_lawn_mower_plan_map_preference_mode_update_can_execute_confirmed_write() -> (
    None
):
    client = SimpleNamespace(
        async_plan_app_mowing_preference_update=AsyncMock(
            return_value={
                "source": "app_action_mowing_preference_write",
                "action": "plan_mowing_preference_update",
                "map_index": 1,
                "area_id": None,
                "executed": True,
                "response_data": {"r": 0},
                "changed_fields": ["preference_mode"],
                "target_mode_name": "custom",
            }
        ),
    )
    coordinator = SimpleNamespace(
        client=client,
        app_maps=_app_maps(current_map_index=1),
        batch_device_data={"batch_mowing_preferences": {"maps": []}},
        selected_map_index=1,
        selected_contour_id=None,
        selected_zone_id=None,
        selected_spot_id=None,
        last_preference_write_result=None,
        async_refresh_batch_device_data=AsyncMock(),
        async_request_refresh=AsyncMock(),
        async_update_listeners=lambda: None,
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = coordinator

    asyncio.run(
        entity.async_plan_map_preference_mode_update(
            "custom",
            execute=True,
            confirm_preference_write=True,
        )
    )

    client.async_plan_app_mowing_preference_update.assert_awaited_once_with(
        map_index=1,
        area_id=None,
        changes={"preference_mode": 1},
        execute=True,
        confirm_write=True,
    )
    coordinator.async_refresh_batch_device_data.assert_awaited_once_with(
        force=True,
        source="mowing_preference_write",
    )
    coordinator.async_request_refresh.assert_awaited_once()


def test_lawn_mower_plan_map_preference_mode_update_blocks_unconfirmed_write() -> None:
    client = SimpleNamespace(
        async_plan_app_mowing_preference_update=AsyncMock(),
    )
    coordinator = SimpleNamespace(
        client=client,
        app_maps=_app_maps(current_map_index=1),
        batch_device_data={"batch_mowing_preferences": {"maps": []}},
        selected_map_index=1,
        selected_contour_id=None,
        selected_zone_id=None,
        selected_spot_id=None,
        last_preference_write_result=None,
        async_request_refresh=AsyncMock(),
        async_update_listeners=lambda: None,
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = coordinator

    with pytest.raises(HomeAssistantError, match="confirm_preference_write"):
        asyncio.run(
            entity.async_plan_map_preference_mode_update(
                "custom",
                execute=True,
                confirm_preference_write=False,
            )
        )

    client.async_plan_app_mowing_preference_update.assert_not_called()


def test_lawn_mower_start_uses_selected_zone() -> None:
    client = SimpleNamespace(
        async_start_zone_mowing=AsyncMock(),
        async_start_spot_mowing=AsyncMock(),
        async_start_mowing=AsyncMock(),
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=client,
        selected_mowing_action=MOWING_ACTION_ZONE,
        selected_map_index=None,
        selected_contour_id=None,
        selected_zone_id=3,
        selected_spot_id=None,
        vector_map_details=_vector_map_details(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(),
        async_request_refresh=AsyncMock(),
    )

    asyncio.run(entity.async_start_mowing())

    client.async_start_zone_mowing.assert_awaited_once_with([3])
    client.async_start_mowing.assert_not_called()
    entity.coordinator.async_request_refresh.assert_awaited_once()


def test_lawn_mower_start_uses_selected_spot_area_id() -> None:
    client = SimpleNamespace(
        async_start_zone_mowing=AsyncMock(),
        async_start_spot_mowing=AsyncMock(),
        async_start_mowing=AsyncMock(),
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=client,
        selected_mowing_action=MOWING_ACTION_SPOT,
        selected_map_index=None,
        selected_contour_id=None,
        selected_zone_id=None,
        selected_spot_id=2,
        vector_map_details=_vector_map_details(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(),
        async_request_refresh=AsyncMock(),
    )

    asyncio.run(entity.async_start_mowing())

    client.async_start_spot_mowing.assert_awaited_once_with([2])
    client.async_start_mowing.assert_not_called()
    entity.coordinator.async_request_refresh.assert_awaited_once()


def test_lawn_mower_start_zone_requires_available_zone() -> None:
    client = SimpleNamespace(
        async_start_edge_mowing=AsyncMock(),
        async_start_zone_mowing=AsyncMock(),
        async_start_spot_mowing=AsyncMock(),
        async_start_mowing=AsyncMock(),
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=client,
        selected_mowing_action=MOWING_ACTION_ZONE,
        selected_map_index=None,
        selected_contour_id=None,
        selected_zone_id=None,
        selected_spot_id=None,
        vector_map_details={},
        batch_device_data={"batch_mowing_preferences": {"maps": []}},
        app_maps=_app_maps(),
        async_request_refresh=AsyncMock(),
    )

    with pytest.raises(HomeAssistantError, match="No current-map zone"):
        asyncio.run(entity.async_start_mowing())


def test_lawn_mower_start_blocks_map_scope_mismatch() -> None:
    client = SimpleNamespace(
        async_start_edge_mowing=AsyncMock(),
        async_start_zone_mowing=AsyncMock(),
        async_start_spot_mowing=AsyncMock(),
        async_start_mowing=AsyncMock(),
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=client,
        selected_mowing_action=MOWING_ACTION_ZONE,
        selected_map_index=0,
        selected_contour_id=None,
        selected_zone_id=3,
        selected_spot_id=None,
        vector_map_details=_vector_map_details(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(current_map_index=1),
        async_request_refresh=AsyncMock(),
    )

    with pytest.raises(
        HomeAssistantError,
        match="Selected map does not match the active mower map",
    ):
        asyncio.run(entity.async_start_mowing())

    client.async_start_zone_mowing.assert_not_called()


def test_lawn_mower_zone_service_uses_validated_current_map_ids() -> None:
    client = SimpleNamespace(async_start_zone_mowing=AsyncMock())
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=client,
        selected_map_index=None,
        vector_map_details=_vector_map_details(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(),
        selected_mowing_action="all_area",
        selected_contour_id=(3, 0),
        selected_zone_id=1,
        selected_spot_id=2,
        async_request_refresh=AsyncMock(),
    )

    asyncio.run(entity.async_start_zone_mowing([1, 3]))

    client.async_start_zone_mowing.assert_awaited_once_with([1, 3])
    assert entity.coordinator.selected_mowing_action == MOWING_ACTION_ZONE
    assert entity.coordinator.selected_zone_id is None
    assert entity.coordinator.selected_contour_id is None
    assert entity.coordinator.selected_spot_id is None
    entity.coordinator.async_request_refresh.assert_awaited_once()


def test_lawn_mower_zone_service_raises_home_assistant_error_on_failed_confirmation(
) -> None:
    client = SimpleNamespace(
        async_start_zone_mowing=AsyncMock(
            side_effect=DreameLawnMowerCommandRejectedError(
                "The mower task type could not be confirmed."
            )
        )
    )
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=client,
        selected_map_index=None,
        vector_map_details=_vector_map_details(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(),
        selected_mowing_action="all_area",
        selected_contour_id=(3, 0),
        selected_zone_id=1,
        selected_spot_id=2,
        async_request_refresh=AsyncMock(),
    )

    with pytest.raises(
        HomeAssistantError,
        match="task type could not be confirmed",
    ):
        asyncio.run(entity.async_start_zone_mowing([1]))

    client.async_start_zone_mowing.assert_awaited_once_with([1])
    assert entity.coordinator.selected_mowing_action == "all_area"
    assert entity.coordinator.selected_zone_id == 1
    assert entity.coordinator.selected_contour_id == (3, 0)
    assert entity.coordinator.selected_spot_id == 2
    entity.coordinator.async_request_refresh.assert_not_awaited()


def test_lawn_mower_zone_service_rejects_unknown_active_map_id() -> None:
    client = SimpleNamespace(async_start_zone_mowing=AsyncMock())
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=client,
        selected_map_index=None,
        vector_map_details=_vector_map_details(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(),
        async_request_refresh=AsyncMock(),
    )

    with pytest.raises(HomeAssistantError, match="Zone #9 is not available"):
        asyncio.run(entity.async_start_zone_mowing([9]))

    client.async_start_zone_mowing.assert_not_called()
    entity.coordinator.async_request_refresh.assert_not_awaited()


def test_lawn_mower_spot_service_sends_saved_area_ids_not_coordinates() -> None:
    client = SimpleNamespace(async_start_spot_mowing=AsyncMock())
    entity = object.__new__(DreameLawnMower)
    entity.coordinator = SimpleNamespace(
        client=client,
        selected_map_index=None,
        vector_map_details=_vector_map_details(),
        batch_device_data=_batch_device_data(),
        app_maps=_app_maps(),
        selected_mowing_action="all_area",
        selected_contour_id=(3, 0),
        selected_zone_id=1,
        selected_spot_id=None,
        async_request_refresh=AsyncMock(),
    )

    asyncio.run(entity.async_start_spot_mowing([2]))

    client.async_start_spot_mowing.assert_awaited_once_with([2])
    assert entity.coordinator.selected_mowing_action == MOWING_ACTION_SPOT
    assert entity.coordinator.selected_spot_id == 2
    assert entity.coordinator.selected_zone_id is None
    assert entity.coordinator.selected_contour_id is None
    entity.coordinator.async_request_refresh.assert_awaited_once()
