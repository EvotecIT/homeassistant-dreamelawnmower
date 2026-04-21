"""Regression checks for mower batch vector-map fallback."""

from __future__ import annotations

import json
from types import SimpleNamespace

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.vector_map import (
    parse_batch_vector_map,
    render_vector_map_png,
    vector_map_to_details,
    vector_map_to_summary,
)
from dreame_lawn_mower_client import DreameLawnMowerClient
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor


def _client() -> DreameLawnMowerClient:
    return DreameLawnMowerClient(
        username="user@example.invalid",
        password="secret",
        country="eu",
        account_type="dreame",
        descriptor=DreameLawnMowerDescriptor(
            did="device-1",
            name="Garden Mower",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
    )


def _batch_payload() -> dict[str, str]:
    primary_map = {
        "mowingAreas": {
            "dataType": "Map",
            "value": [
                [
                    1,
                    {
                        "type": 0,
                        "shapeType": 0,
                        "path": [
                            {"x": 0, "y": 0},
                            {"x": 100, "y": 0},
                            {"x": 100, "y": 100},
                            {"x": 0, "y": 100},
                        ],
                        "name": "Front Yard",
                        "time": 120,
                        "etime": 90,
                        "area": 10.5,
                    },
                ]
            ],
        },
        "forbiddenAreas": {
            "dataType": "Map",
            "value": [
                [
                    8,
                    {
                        "type": 9,
                        "path": [
                            {"x": 20, "y": 20},
                            {"x": 30, "y": 20},
                            {"x": 30, "y": 30},
                            {"x": 20, "y": 30},
                        ],
                    },
                ]
            ],
        },
        "spotAreas": {
            "dataType": "Map",
            "value": [
                [
                    9,
                    {
                        "path": [
                            {"x": 60, "y": 60},
                            {"x": 80, "y": 60},
                            {"x": 80, "y": 80},
                            {"x": 60, "y": 80},
                        ]
                    },
                ]
            ],
        },
        "paths": {
            "dataType": "Map",
            "value": [
                [
                    201,
                    {
                        "type": 1,
                        "path": [
                            {"x": 0, "y": 50},
                            {"x": 120, "y": 50},
                        ],
                    },
                ]
            ],
        },
        "contours": {
            "dataType": "Map",
            "value": [
                [
                    [1, 0],
                    {
                        "type": 1,
                        "shapeType": 0,
                        "path": [
                            {"x": 0, "y": 0},
                            {"x": 100, "y": 0},
                        ],
                    },
                ]
            ],
        },
        "cleanPoints": {
            "dataType": "Map",
            "value": [
                [
                    301,
                    {"x": 25, "y": 25},
                ]
            ],
        },
        "boundary": {"x1": -10, "y1": -10, "x2": 120, "y2": 110},
        "totalArea": 10,
        "name": "Primary",
        "mapIndex": 0,
    }
    secondary_map = {
        "mowingAreas": {
            "dataType": "Map",
            "value": [
                [
                    2,
                    {
                        "path": [
                            {"x": 0, "y": 0},
                            {"x": 50, "y": 0},
                            {"x": 50, "y": 50},
                            {"x": 0, "y": 50},
                        ],
                        "name": "Back Yard",
                    },
                ]
            ],
        },
        "boundary": {"x1": 0, "y1": 0, "x2": 50, "y2": 50},
        "mapIndex": 1,
        "contours": {
            "dataType": "Map",
            "value": [
                [
                    "5,0",
                    {
                        "type": 1,
                        "shapeType": 0,
                        "path": [
                            {"x": 0, "y": 0},
                            {"x": 50, "y": 0},
                        ],
                    },
                ]
            ],
        },
    }

    primary_part = json.dumps(
        [json.dumps(primary_map, separators=(",", ":"))],
        separators=(",", ":"),
    )
    secondary_part = json.dumps(
        [json.dumps(secondary_map, separators=(",", ":"))],
        separators=(",", ":"),
    )
    raw_map = primary_part + secondary_part
    raw_path = "[][[10,20],[30,40],[32767,-32768],[50,60],[70,80]]"

    return {
        "MAP.0": raw_map[:80],
        "MAP.1": raw_map[80:160],
        "MAP.2": raw_map[160:],
        "MAP.info": str(len(primary_part)),
        "M_PATH.0": raw_path[:18],
        "M_PATH.1": raw_path[18:],
        "M_PATH.info": "2",
    }


def test_parse_batch_vector_map_handles_map_info_split_and_mow_paths() -> None:
    vector_map = parse_batch_vector_map(_batch_payload())

    assert vector_map is not None
    assert vector_map.map_index == 0
    assert vector_map.name == "Primary"
    assert vector_map.boundary is not None
    assert vector_map.boundary.width == 130
    assert len(vector_map.zones) == 1
    assert vector_map.zones[0].name == "Front Yard"
    assert len(vector_map.forbidden_areas) == 1
    assert len(vector_map.spot_areas) == 1
    assert len(vector_map.paths) == 1
    assert len(vector_map.contours) == 1
    assert vector_map.contours[0].contour_id == (1, 0)
    assert vector_map.clean_points == ((25, 25),)
    assert [(entry.map_id, entry.map_index, entry.name) for entry in vector_map.available_maps] == [
        (1, 0, "Primary"),
        (2, 1, ""),
    ]
    assert sorted(vector_map.maps) == [1, 2]
    assert vector_map.maps[2].contours[0].contour_id == (5, 0)
    assert len(vector_map.mow_paths) == 1
    assert vector_map.mow_paths[0].segments == (
        ((100, 200), (300, 400)),
        ((500, 600), (700, 800)),
    )


def test_vector_map_summary_and_renderer_return_drawable_output() -> None:
    vector_map = parse_batch_vector_map(_batch_payload())

    summary = vector_map_to_summary(vector_map)
    image_png = render_vector_map_png(vector_map)

    assert summary is not None
    assert summary.available is True
    assert summary.map_id == 0
    assert summary.width == 130
    assert summary.height == 120
    assert summary.segment_count == 1
    assert summary.no_go_area_count == 1
    assert summary.spot_area_count == 1
    assert summary.active_point_count == 1
    assert summary.pathway_count == 1
    assert summary.path_point_count == 6
    assert image_png is not None
    assert image_png.startswith(b"\x89PNG")


def test_vector_map_details_report_live_path_counts() -> None:
    vector_map = parse_batch_vector_map(_batch_payload())

    details = vector_map_to_details(vector_map)

    assert details["map_name"] == "Primary"
    assert details["map_index"] == 0
    assert details["map_id"] == 1
    assert details["total_area"] == 10
    assert details["zone_count"] == 1
    assert details["zone_names"] == ["Front Yard"]
    assert details["contour_count"] == 1
    assert details["contour_ids"] == [[1, 0]]
    assert details["available_map_count"] == 2
    assert details["available_maps"] == [
        {"map_id": 1, "map_index": 0, "name": "Primary", "total_area": 10.0},
        {"map_id": 2, "map_index": 1, "name": None, "total_area": None},
    ]
    assert details["maps"] == [
        {
            "map_id": 1,
            "map_index": 0,
            "map_name": "Primary",
            "total_area": 10,
            "zone_ids": [1],
            "zone_names": ["Front Yard"],
            "spot_ids": [9],
            "contour_ids": [[1, 0]],
            "contour_count": 1,
            "clean_point_count": 1,
            "cruise_point_count": 0,
            "mow_path_count": 1,
            "mow_path_segment_count": 2,
            "mow_path_point_count": 4,
            "mow_path_length_m": 5.66,
            "has_live_path": True,
        },
        {
            "map_id": 2,
            "map_index": 1,
            "map_name": None,
            "total_area": None,
            "zone_ids": [2],
            "zone_names": ["Back Yard"],
            "spot_ids": [],
            "contour_ids": [[5, 0]],
            "contour_count": 1,
            "clean_point_count": 0,
            "cruise_point_count": 0,
            "mow_path_count": 1,
            "mow_path_segment_count": 2,
            "mow_path_point_count": 4,
            "mow_path_length_m": 5.66,
            "has_live_path": True,
        },
    ]
    assert details["clean_point_count"] == 1
    assert details["cruise_point_count"] == 0
    assert details["mow_path_count"] == 1
    assert details["mow_path_segment_count"] == 2
    assert details["mow_path_point_count"] == 4
    assert details["mow_path_length_m"] == 5.66
    assert details["has_live_path"] is True


def test_client_edge_and_map_actions_use_expected_app_task_payloads() -> None:
    client = _client()
    recorded_payloads: list[dict] = []
    client._sync_call_app_action = lambda payload, **kwargs: recorded_payloads.append(  # type: ignore[method-assign]  # noqa: ARG005
        payload
    ) or {"ok": True}

    assert client._sync_start_edge_mowing([[1, 0]]) == {"ok": True}
    assert client._sync_switch_current_map(1) == {"ok": True}

    assert recorded_payloads == [
        {"m": "a", "p": 0, "o": 101, "d": {"edge": [[1, 0]]}},
        {"m": "a", "p": 0, "o": 200, "d": {"idx": 1}},
    ]


def test_map_view_uses_batch_vector_map_when_app_map_fails() -> None:
    client = _client()
    client._sync_get_app_maps = lambda **kwargs: {  # noqa: ARG005
        "source": "app_action_map",
        "available": False,
        "maps": [],
        "errors": [{"error": "no app map"}],
    }
    client._sync_get_vector_map_batch_data = lambda: _batch_payload()
    client._sync_wait_for_map = lambda timeout, interval: (_ for _ in ()).throw(  # noqa: ARG005
        AssertionError("legacy map path should not run when vector map works")
    )
    client._safe_map_diagnostics = lambda **kwargs: None

    view = client._sync_refresh_map_view(timeout=0, interval=0)

    assert view.source == "batch_vector_map"
    assert view.available is True
    assert view.image_png is not None
    assert view.image_png.startswith(b"\x89PNG")
    assert view.summary is not None
    assert view.summary.segment_count == 1
    assert view.summary.no_go_area_count == 1
    assert view.summary.spot_area_count == 1
    assert view.details is not None
    assert view.details["mow_path_point_count"] == 4
    assert view.details["mow_path_length_m"] == 5.66
    assert view.details["has_live_path"] is True
    assert view.app_maps is not None
    assert view.app_maps["source"] == "app_action_map"
    assert view.app_maps["error_count"] == 1


def test_vector_map_view_includes_cached_runtime_track_overlay() -> None:
    client = _client()
    client._sync_get_vector_map_batch_data = lambda: _batch_payload()
    client._safe_map_diagnostics = lambda **kwargs: None
    client.update_runtime_live_tracking(
        SimpleNamespace(
            hex="runtime-1",
            candidate_runtime_track_segments=(
                ((10, 20), (30, 40), (50, 40)),
            ),
            candidate_runtime_pose_x=50,
            candidate_runtime_pose_y=40,
            candidate_runtime_heading_deg=90.0,
        ),
        active=True,
    )

    view = client._sync_refresh_vector_map_view()

    assert view.source == "batch_vector_map"
    assert view.available is True
    assert view.image_png is not None
    assert view.image_png.startswith(b"\x89PNG")
    assert view.summary is not None
    assert view.summary.path_point_count == 9
    assert view.details is not None
    assert view.details["runtime_track_segment_count"] == 1
    assert view.details["runtime_track_point_count"] == 3
    assert view.details["runtime_track_length_m"] == 0.48
    assert view.details["runtime_pose_x"] == 50
    assert view.details["runtime_pose_y"] == 40
    assert view.details["runtime_heading_deg"] == 90.0
    assert view.details["has_live_path"] is True
