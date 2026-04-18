"""Unit tests for Home Assistant map camera helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.dreame_lawn_mower.map_attributes import map_camera_attributes
from dreame_lawn_mower_client.models import (
    DreameLawnMowerMapSummary,
    DreameLawnMowerMapView,
)


def test_map_camera_attributes_include_app_map_summary_counts() -> None:
    """Camera attributes preserve app-map counts validated from live payloads."""
    refreshed_at = datetime(2026, 4, 18, 12, 30, tzinfo=UTC)
    view = DreameLawnMowerMapView(
        source="app_action_map",
        summary=DreameLawnMowerMapSummary(
            available=True,
            map_id=0,
            width=521,
            height=900,
            segment_count=2,
            active_area_count=2,
            spot_area_count=2,
            no_go_area_count=0,
            path_point_count=63,
            robot_present=True,
            charger_present=True,
        ),
        image_png=b"png",
    )

    attributes = map_camera_attributes(
        view,
        image_cached=True,
        refreshed_at=refreshed_at,
        last_error=None,
    )

    assert attributes["map_cached"] is True
    assert attributes["map_placeholder"] is False
    assert attributes["map_source"] == "app_action_map"
    assert attributes["map_has_image"] is True
    assert attributes["map_available"] is True
    assert attributes["map_id"] == 0
    assert attributes["width"] == 521
    assert attributes["height"] == 900
    assert attributes["segment_count"] == 2
    assert attributes["active_area_count"] == 2
    assert attributes["spot_area_count"] == 2
    assert attributes["no_go_area_count"] == 0
    assert attributes["path_point_count"] == 63
    assert attributes["robot_present"] is True
    assert attributes["charger_present"] is True
    assert attributes["last_map_refresh"] == "2026-04-18T12:30:00+00:00"


def test_map_camera_attributes_report_placeholder_without_view() -> None:
    """Empty cache attributes remain explicit for HA diagnostics."""
    attributes = map_camera_attributes(
        None,
        image_cached=False,
        refreshed_at=None,
        last_error="offline",
    )

    assert attributes["map_cached"] is False
    assert attributes["map_placeholder"] is True
    assert attributes["map_source"] is None
    assert attributes["map_has_image"] is False
    assert attributes["map_error"] == "offline"
    assert attributes["map_available"] is None
    assert attributes["spot_area_count"] is None
    assert attributes["no_go_area_count"] is None
    assert attributes["last_map_refresh"] is None
