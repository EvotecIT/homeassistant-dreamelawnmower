"""Attribute helpers for Home Assistant map entities."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .dreame_client.models import DreameLawnMowerMapView, map_summary_to_dict

_MAP_SUMMARY_ATTRIBUTE_KEYS = (
    "map_id",
    "frame_id",
    "timestamp_ms",
    "rotation",
    "width",
    "height",
    "grid_size",
    "saved_map",
    "temporary_map",
    "recovery_map",
    "empty_map",
    "segment_count",
    "active_segment_count",
    "active_area_count",
    "active_point_count",
    "path_point_count",
    "no_go_area_count",
    "spot_area_count",
    "virtual_wall_count",
    "pathway_count",
    "obstacle_count",
    "charger_present",
    "robot_present",
)


def map_camera_attributes(
    view: DreameLawnMowerMapView | None,
    *,
    image_cached: bool,
    refreshed_at: datetime | None,
    last_error: str | None,
) -> dict[str, Any]:
    """Return Home Assistant attributes for a cached map camera view."""
    summary = map_summary_to_dict(None if view is None else view.summary)
    attributes: dict[str, Any] = {
        "map_cached": image_cached,
        "map_placeholder": not image_cached,
        "map_source": None if view is None else view.source,
        "map_has_image": False if view is None else view.has_image,
        "map_error": last_error or (None if view is None else view.error),
        "map_available": None if summary is None else summary["available"],
        "last_map_refresh": None if refreshed_at is None else refreshed_at.isoformat(),
    }
    attributes.update(
        {
            key: None if summary is None else summary[key]
            for key in _MAP_SUMMARY_ATTRIBUTE_KEYS
        }
    )
    return attributes
