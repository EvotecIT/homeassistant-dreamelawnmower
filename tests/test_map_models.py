"""Unit tests for mower map summary helpers."""

from __future__ import annotations

from types import SimpleNamespace

from dreame_lawn_mower_client.models import (
    DreameLawnMowerMapView,
    map_summary_from_map_data,
    map_summary_to_dict,
)


def test_map_summary_from_map_data_returns_none_for_missing_map() -> None:
    assert map_summary_from_map_data(None) is None


def test_map_summary_from_map_data_counts_mower_map_features() -> None:
    map_data = SimpleNamespace(
        map_id=17,
        frame_id=23,
        timestamp_ms=123456789,
        rotation=90,
        dimensions=SimpleNamespace(width=512, height=256, grid_size=50),
        saved_map=True,
        temporary_map=False,
        recovery_map=False,
        empty_map=False,
        segments={1: object(), 2: object()},
        active_segments=[1],
        active_areas=[object(), object()],
        active_points=[object()],
        path=[object(), object(), object()],
        no_go_areas=[object()],
        virtual_walls=[object(), object()],
        pathways=[object()],
        obstacles={"1": object(), "2": object(), "3": object()},
        charger_position=object(),
        robot_position=object(),
    )

    summary = map_summary_from_map_data(map_data)

    assert summary is not None
    assert summary.available is True
    assert summary.map_id == 17
    assert summary.frame_id == 23
    assert summary.timestamp_ms == 123456789
    assert summary.rotation == 90
    assert summary.width == 512
    assert summary.height == 256
    assert summary.grid_size == 50
    assert summary.saved_map is True
    assert summary.segment_count == 2
    assert summary.active_segment_count == 1
    assert summary.active_area_count == 2
    assert summary.active_point_count == 1
    assert summary.path_point_count == 3
    assert summary.no_go_area_count == 1
    assert summary.virtual_wall_count == 2
    assert summary.pathway_count == 1
    assert summary.obstacle_count == 3
    assert summary.charger_present is True
    assert summary.robot_present is True


def test_map_summary_to_dict_returns_json_safe_payload() -> None:
    map_data = SimpleNamespace(
        map_id=17,
        frame_id=23,
        timestamp_ms=123456789,
        rotation=90,
        dimensions=SimpleNamespace(width=512, height=256, grid_size=50),
        saved_map=True,
        temporary_map=False,
        recovery_map=False,
        empty_map=False,
        segments={1: object()},
        active_segments=[],
        active_areas=[],
        active_points=[],
        path=[],
        no_go_areas=[],
        virtual_walls=[],
        pathways=[],
        obstacles={},
        charger_position=None,
        robot_position=None,
    )

    summary = map_summary_from_map_data(map_data)

    assert map_summary_to_dict(summary) == {
        "available": True,
        "map_id": 17,
        "frame_id": 23,
        "timestamp_ms": 123456789,
        "rotation": 90,
        "width": 512,
        "height": 256,
        "grid_size": 50,
        "saved_map": True,
        "temporary_map": False,
        "recovery_map": False,
        "empty_map": False,
        "segment_count": 1,
        "active_segment_count": 0,
        "active_area_count": 0,
        "active_point_count": 0,
        "path_point_count": 0,
        "no_go_area_count": 0,
        "virtual_wall_count": 0,
        "pathway_count": 0,
        "obstacle_count": 0,
        "charger_present": False,
        "robot_present": False,
    }


def test_map_view_as_dict_omits_image_bytes() -> None:
    view = DreameLawnMowerMapView(
        source="legacy_current_map",
        summary=None,
        image_png=b"not-json-safe",
        error="test",
    )

    assert view.has_image is True
    assert view.as_dict() == {
        "source": "legacy_current_map",
        "available": False,
        "has_image": True,
        "error": "test",
        "summary": None,
    }
