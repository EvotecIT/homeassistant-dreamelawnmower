"""Unit tests for mower map summary helpers."""

from __future__ import annotations

from types import SimpleNamespace

from dreame_lawn_mower_client.models import map_summary_from_map_data


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
