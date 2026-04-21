"""Attribute helpers for Home Assistant map entities."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .dreame_lawn_mower_client.models import DreameLawnMowerMapView, map_summary_to_dict

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
    app_maps = {} if view is None or view.app_maps is None else dict(view.app_maps)
    details = {} if view is None or view.details is None else dict(view.details)
    attributes.update(
        {
            "app_map_count": app_maps.get("map_count"),
            "app_current_map_index": app_maps.get("current_map_index"),
            "app_available_map_count": app_maps.get("available_map_count"),
            "app_created_map_count": app_maps.get("created_map_count"),
            "app_map_error_count": app_maps.get("error_count"),
            "app_map_object_count": app_maps.get("object_count"),
            "app_map_object_error": app_maps.get("object_error"),
            "app_map_objects": app_maps.get("objects"),
            "app_maps": app_maps.get("maps"),
            "map_name": details.get("map_name"),
            "map_id": details.get("map_id"),
            "map_index": details.get("map_index"),
            "map_current_map_id": details.get("current_map_id"),
            "map_total_area": details.get("total_area"),
            "map_zone_count": details.get("zone_count"),
            "map_zone_names": details.get("zone_names"),
            "map_contour_count": details.get("contour_count"),
            "map_contour_ids": details.get("contour_ids"),
            "map_clean_point_count": details.get("clean_point_count"),
            "map_cruise_point_count": details.get("cruise_point_count"),
            "map_trajectory_count": details.get("trajectory_count"),
            "map_trajectory_point_count": details.get("trajectory_point_count"),
            "map_trajectory_length_m": details.get("trajectory_length_m"),
            "map_cut_relation_count": details.get("cut_relation_count"),
            "mow_path_count": details.get("mow_path_count"),
            "mow_path_segment_count": details.get("mow_path_segment_count"),
            "mow_path_point_count": details.get("mow_path_point_count"),
            "mow_path_length_m": details.get("mow_path_length_m"),
            "runtime_track_segment_count": details.get("runtime_track_segment_count"),
            "runtime_track_point_count": details.get("runtime_track_point_count"),
            "runtime_track_length_m": details.get("runtime_track_length_m"),
            "runtime_pose_x": details.get("runtime_pose_x"),
            "runtime_pose_y": details.get("runtime_pose_y"),
            "runtime_heading_deg": details.get("runtime_heading_deg"),
            "map_has_live_path": details.get("has_live_path"),
            "map_available_vector_map_count": details.get("available_map_count"),
            "map_available_vector_maps": details.get("available_maps"),
            "map_details": details or None,
        }
    )
    if summary is not None and attributes.get("map_id") is None:
        attributes["map_id"] = summary["map_id"]
    return attributes
