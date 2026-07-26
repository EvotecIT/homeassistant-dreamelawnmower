"""Map selection and summary helpers for Dreame lawn mower sensors."""

from __future__ import annotations

import math
from typing import Any

from .control_options import (
    MOWING_ACTION_EDGE,
    MOWING_ACTION_SPOT,
    MOWING_ACTION_ZONE,
    contour_label,
    current_contour_entries,
    current_spot_entries,
    current_zone_entries,
    map_entries,
    mowing_action_label,
    spot_label,
    zone_label,
)
from .control_options import (
    current_map_index as selected_current_map_index,
)


def app_map_object_attributes(result: dict[str, Any] | None) -> dict[str, Any]:
    """Return compact cached 3D app-map object attributes."""
    summary = _app_map_object_summary(_app_map_object_section(result))
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "app_map_objects": summary,
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def app_map_attributes(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return compact cached app-map attributes."""
    summary = _app_maps_summary(result, batch_device_data)
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "app_maps": summary,
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def vector_map_attributes(result: dict[str, Any] | None) -> dict[str, Any]:
    """Return compact cached vector-map attributes."""
    summary = _vector_map_summary(result)
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "vector_maps": summary,
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def current_app_map_attributes(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return compact cached current app-map attributes."""
    summary = _current_app_map_summary(result, batch_device_data)
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "current_app_map": summary,
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def current_vector_map_attributes(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return compact cached current vector-map attributes."""
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not summary:
        return {}
    attributes = {
        "captured_at": result.get("captured_at") if result else None,
        "source": result.get("source") if result else None,
        "current_vector_map": summary,
    }
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def _coordinate_path_length_m(points: Any) -> float:
    if not isinstance(points, (list, tuple)) or len(points) < 2:
        return 0.0

    total = 0.0
    previous = points[0]
    for current in points[1:]:
        if not (
            isinstance(previous, (list, tuple))
            and len(previous) >= 2
            and isinstance(current, (list, tuple))
            and len(current) >= 2
        ):
            previous = current
            continue
        total += math.hypot(current[0] - previous[0], current[1] - previous[1])
        previous = current
    return total / 100.0


def _app_map_object_count(result: dict[str, Any] | None) -> int | None:
    summary = _app_map_object_summary(_app_map_object_section(result))
    if not isinstance(summary, dict):
        return None
    value = summary.get("object_count")
    return value if isinstance(value, int) else None


def _app_map_count(result: dict[str, Any] | None) -> int | None:
    summary = _app_maps_summary(result)
    if not isinstance(summary, dict):
        return None
    value = summary.get("map_count")
    return value if isinstance(value, int) else None


def _available_vector_map_count(result: dict[str, Any] | None) -> int | None:
    summary = _vector_map_summary(result)
    if not isinstance(summary, dict):
        return None
    value = summary.get("available_map_count")
    return value if isinstance(value, int) else None


def _current_vector_map_name(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> str | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("map_name")
    if isinstance(value, str) and value.strip():
        return value.strip()
    map_id = summary.get("map_id")
    if isinstance(map_id, int):
        return f"Map {map_id}"
    return None


def _current_app_map_index(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_app_map_summary(result, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("idx")
    return value if isinstance(value, int) else None


def _selected_mowing_action_label(coordinator: Any) -> str | None:
    action = getattr(coordinator, "selected_mowing_action", None)
    if not isinstance(action, str) or not action.strip():
        return None
    return mowing_action_label(action)


def _selected_map_index(
    app_maps: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None,
    selected_map_index: int | None,
) -> int | None:
    entries = map_entries(app_maps, batch_device_data)
    if not entries:
        return None
    normalized = selected_current_map_index(
        app_maps,
        batch_device_data,
        selected_map_index=selected_map_index,
    )
    if any(entry["map_index"] == normalized for entry in entries):
        return normalized
    return None


def _selected_map_label(
    app_maps: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None,
    selected_map_index: int | None,
) -> str | None:
    normalized = _selected_map_index(app_maps, batch_device_data, selected_map_index)
    if normalized is None:
        return None
    for entry in map_entries(app_maps, batch_device_data):
        if entry["map_index"] == normalized:
            return str(entry["label"])
    return None


def _selected_contour_id(value: Any) -> tuple[int, int] | None:
    if (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and isinstance(value[0], int)
        and isinstance(value[1], int)
    ):
        return (int(value[0]), int(value[1]))
    return None


def _selected_target_summary(coordinator: Any) -> dict[str, Any]:
    action = getattr(coordinator, "selected_mowing_action", None)
    selected_map_index = _selected_map_index(
        getattr(coordinator, "app_maps", None),
        getattr(coordinator, "batch_device_data", None),
        getattr(coordinator, "selected_map_index", None),
    )
    selected_map = _selected_map_label(
        getattr(coordinator, "app_maps", None),
        getattr(coordinator, "batch_device_data", None),
        getattr(coordinator, "selected_map_index", None),
    )
    if action == MOWING_ACTION_ZONE:
        entries = current_zone_entries(
            getattr(coordinator, "batch_device_data", None),
            getattr(coordinator, "app_maps", None),
            getattr(coordinator, "vector_map_details", None),
            selected_map_index=selected_map_index,
        )
        selected_zone_id = getattr(coordinator, "selected_zone_id", None)
        if isinstance(selected_zone_id, int):
            for entry in entries:
                if entry["area_id"] == selected_zone_id:
                    return {
                        "target_type": "zone",
                        "target_id": selected_zone_id,
                        "target_label": str(entry["label"]),
                        "available_target_count": len(entries),
                        "selected_map_index": selected_map_index,
                        "selected_map_label": selected_map,
                    }
        if entries:
            fallback = int(entries[0]["area_id"])
            return {
                "target_type": "zone",
                "target_id": fallback,
                "target_label": str(entries[0]["label"]),
                "available_target_count": len(entries),
                "selected_map_index": selected_map_index,
                "selected_map_label": selected_map,
            }
        return {}

    if action == MOWING_ACTION_SPOT:
        entries = current_spot_entries(
            getattr(coordinator, "app_maps", None),
            getattr(coordinator, "batch_device_data", None),
            selected_map_index=selected_map_index,
        )
        selected_spot_id = getattr(coordinator, "selected_spot_id", None)
        if isinstance(selected_spot_id, int):
            for entry in entries:
                if entry["spot_id"] == selected_spot_id:
                    return {
                        "target_type": "spot",
                        "target_id": selected_spot_id,
                        "target_label": str(entry["label"]),
                        "available_target_count": len(entries),
                        "selected_map_index": selected_map_index,
                        "selected_map_label": selected_map,
                    }
        if entries:
            fallback = int(entries[0]["spot_id"])
            return {
                "target_type": "spot",
                "target_id": fallback,
                "target_label": spot_label(fallback),
                "available_target_count": len(entries),
                "selected_map_index": selected_map_index,
                "selected_map_label": selected_map,
            }
        return {}

    if action == MOWING_ACTION_EDGE:
        entries = current_contour_entries(
            getattr(coordinator, "vector_map_details", None),
            getattr(coordinator, "app_maps", None),
            getattr(coordinator, "batch_device_data", None),
            selected_map_index=selected_map_index,
        )
        selected_contour_id = _selected_contour_id(
            getattr(coordinator, "selected_contour_id", None)
        )
        if selected_contour_id is not None:
            for entry in entries:
                if entry["contour_id"] == selected_contour_id:
                    return {
                        "target_type": "edge",
                        "target_id": list(selected_contour_id),
                        "target_label": str(entry["label"]),
                        "available_target_count": len(entries),
                        "selected_map_index": selected_map_index,
                        "selected_map_label": selected_map,
                    }
        if entries:
            entry_id = entries[0]["contour_id"]
            if isinstance(entry_id, tuple):
                return {
                    "target_type": "edge",
                    "target_id": list(entry_id),
                    "target_label": contour_label(entry_id),
                    "available_target_count": len(entries),
                    "selected_map_index": selected_map_index,
                    "selected_map_label": selected_map,
                }
        return {}

    return {
        "selected_map_index": selected_map_index,
        "selected_map_label": selected_map,
    }


def _selected_target_label(coordinator: Any) -> str | None:
    summary = _selected_target_summary(coordinator)
    value = summary.get("target_label")
    return value if isinstance(value, str) and value.strip() else None


def _selected_map_preference_summary(coordinator: Any) -> dict[str, Any]:
    selected_map_index = _selected_map_index(
        getattr(coordinator, "app_maps", None),
        getattr(coordinator, "batch_device_data", None),
        getattr(coordinator, "selected_map_index", None),
    )
    if selected_map_index is None:
        return {}

    preference_maps = (
        getattr(coordinator, "batch_device_data", {}).get("batch_mowing_preferences")
        if isinstance(getattr(coordinator, "batch_device_data", None), dict)
        else None
    )
    maps = preference_maps.get("maps") if isinstance(preference_maps, dict) else None
    if not isinstance(maps, list):
        return {}

    selected_map_label = _selected_map_label(
        getattr(coordinator, "app_maps", None),
        getattr(coordinator, "batch_device_data", None),
        getattr(coordinator, "selected_map_index", None),
    )

    for map_entry in maps:
        if (
            not isinstance(map_entry, dict)
            or map_entry.get("idx") != selected_map_index
        ):
            continue
        preferences = map_entry.get("preferences")
        summary = {
            "selected_map_index": selected_map_index,
            "selected_map_label": selected_map_label,
            "mode": map_entry.get("mode"),
            "mode_name": map_entry.get("mode_name"),
            "area_count": map_entry.get("area_count"),
            "preference_count": len(preferences)
            if isinstance(preferences, list)
            else None,
        }
        return {
            key: value for key, value in summary.items() if value not in (None, [], {})
        }
    return {}


def _selected_map_preference_value(coordinator: Any, key: str) -> Any:
    return _selected_map_preference_summary(coordinator).get(key)


def _selected_zone_preference_summary(coordinator: Any) -> dict[str, Any]:
    selected_map_index = _selected_map_index(
        getattr(coordinator, "app_maps", None),
        getattr(coordinator, "batch_device_data", None),
        getattr(coordinator, "selected_map_index", None),
    )
    if selected_map_index is None:
        return {}

    zone_entries = current_zone_entries(
        getattr(coordinator, "batch_device_data", None),
        getattr(coordinator, "app_maps", None),
        getattr(coordinator, "vector_map_details", None),
        selected_map_index=getattr(coordinator, "selected_map_index", None),
    )
    selected_zone_id = getattr(coordinator, "selected_zone_id", None)
    if not isinstance(selected_zone_id, int) or not any(
        entry["area_id"] == selected_zone_id for entry in zone_entries
    ):
        selected_zone_id = int(zone_entries[0]["area_id"]) if zone_entries else None
    if selected_zone_id is None:
        return {}

    preference_maps = (
        getattr(coordinator, "batch_device_data", {}).get("batch_mowing_preferences")
        if isinstance(getattr(coordinator, "batch_device_data", None), dict)
        else None
    )
    maps = preference_maps.get("maps") if isinstance(preference_maps, dict) else None
    if not isinstance(maps, list):
        return {}

    selected_map_label = _selected_map_label(
        getattr(coordinator, "app_maps", None),
        getattr(coordinator, "batch_device_data", None),
        getattr(coordinator, "selected_map_index", None),
    )
    zone_entry = next(
        (entry for entry in zone_entries if entry["area_id"] == selected_zone_id),
        None,
    )
    zone_label_value = (
        str(zone_entry["label"])
        if isinstance(zone_entry, dict)
        else zone_label(selected_zone_id)
    )

    for map_entry in maps:
        if (
            not isinstance(map_entry, dict)
            or map_entry.get("idx") != selected_map_index
        ):
            continue
        preferences = map_entry.get("preferences")
        if not isinstance(preferences, list):
            return {}
        for preference in preferences:
            if (
                not isinstance(preference, dict)
                or preference.get("area_id") != selected_zone_id
            ):
                continue
            summary = {
                "selected_map_index": selected_map_index,
                "selected_map_label": selected_map_label,
                "selected_zone_id": selected_zone_id,
                "selected_zone_label": zone_label_value,
                "preference_scope": "zone",
                "mode": map_entry.get("mode"),
                "mode_name": map_entry.get("mode_name"),
                "reported_version": preference.get("reported_version"),
                "mowing_height_cm": preference.get("mowing_height_cm"),
                "efficient_mode": preference.get("efficient_mode"),
                "efficient_mode_name": preference.get("efficient_mode_name"),
                "mowing_direction_mode": preference.get("mowing_direction_mode"),
                "mowing_direction_mode_name": preference.get(
                    "mowing_direction_mode_name"
                ),
                "mowing_direction_method_name": preference.get(
                    "mowing_direction_method_name"
                ),
                "mowing_direction_degrees": preference.get("mowing_direction_degrees"),
                "edge_mowing_auto": preference.get("edge_mowing_auto"),
                "edge_mowing_walk_mode": preference.get("edge_mowing_walk_mode"),
                "edge_mowing_walk_mode_name": preference.get(
                    "edge_mowing_walk_mode_name"
                ),
                "turning_method_name": preference.get("turning_method_name"),
                "edge_mowing_obstacle_avoidance": preference.get(
                    "edge_mowing_obstacle_avoidance"
                ),
                "cutter_position_name": preference.get("cutter_position_name"),
                "edge_mowing_num": preference.get("edge_mowing_num"),
                "obstacle_avoidance_enabled": preference.get(
                    "obstacle_avoidance_enabled"
                ),
                "obstacle_avoidance_height_cm": preference.get(
                    "obstacle_avoidance_height_cm"
                ),
                "obstacle_avoidance_distance_cm": preference.get(
                    "obstacle_avoidance_distance_cm"
                ),
                "obstacle_avoidance_ai": preference.get("obstacle_avoidance_ai"),
                "obstacle_avoidance_ai_classes": preference.get(
                    "obstacle_avoidance_ai_classes"
                ),
                "edge_mowing_safe": preference.get("edge_mowing_safe"),
                "edge_cutting_attachment": preference.get(
                    "edge_cutting_attachment"
                ),
            }
            return {
                key: value
                for key, value in summary.items()
                if value not in (None, [], {})
            }
        return {}
    return {}


def _selected_zone_preference_value(coordinator: Any, key: str) -> Any:
    return _selected_zone_preference_summary(coordinator).get(key)


def _selected_run_scope_attributes(coordinator: Any) -> dict[str, Any]:
    action = getattr(coordinator, "selected_mowing_action", None)
    attributes = {
        "selected_mowing_action": action,
        "selected_mowing_action_label": _selected_mowing_action_label(coordinator),
        "selected_map_index": _selected_map_index(
            getattr(coordinator, "app_maps", None),
            getattr(coordinator, "batch_device_data", None),
            getattr(coordinator, "selected_map_index", None),
        ),
        "selected_map_label": _selected_map_label(
            getattr(coordinator, "app_maps", None),
            getattr(coordinator, "batch_device_data", None),
            getattr(coordinator, "selected_map_index", None),
        ),
    }
    attributes.update(_selected_target_summary(coordinator))
    return {
        key: value for key, value in attributes.items() if value not in (None, [], {})
    }


def _current_app_map_total_area(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> float | int | None:
    summary = _current_app_map_summary(result, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("total_area")
    return value if isinstance(value, int | float) else None


def _current_app_map_zone_count(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_app_map_summary(result, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("map_area_count")
    return value if isinstance(value, int) else None


def _current_app_map_spot_count(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_app_map_summary(result, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("spot_count")
    return value if isinstance(value, int) else None


def _current_app_map_trajectory_point_count(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_app_map_summary(result, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("trajectory_point_count")
    return value if isinstance(value, int) else None


def _current_app_map_trajectory_length_m(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> float | int | None:
    summary = _current_app_map_summary(result, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("trajectory_length_m")
    return value if isinstance(value, int | float) else None


def _current_app_map_cut_relation_count(
    result: dict[str, Any] | None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_app_map_summary(result, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("cut_relation_count")
    return value if isinstance(value, int) else None


def _current_vector_map_contour_count(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("contour_count")
    return value if isinstance(value, int) else None


def _current_vector_map_id(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("map_id")
    return value if isinstance(value, int) else None


def _current_vector_map_mow_path_point_count(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("mow_path_point_count")
    return value if isinstance(value, int) else None


def _current_vector_map_mow_path_length_m(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> float | int | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("mow_path_length_m")
    return value if isinstance(value, int | float) else None


def _current_vector_map_runtime_track_segment_count(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("runtime_track_segment_count")
    return value if isinstance(value, int) else None


def _current_vector_map_runtime_track_point_count(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> int | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("runtime_track_point_count")
    return value if isinstance(value, int) else None


def _current_vector_map_runtime_track_length_m(
    result: dict[str, Any] | None,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> float | int | None:
    summary = _current_vector_map_summary(result, app_maps, batch_device_data)
    if not isinstance(summary, dict):
        return None
    value = summary.get("runtime_track_length_m")
    return value if isinstance(value, int | float) else None


def _app_map_object_section(result: dict[str, Any] | None) -> dict[str, Any] | None:
    value = result.get("app_map_objects") if isinstance(result, dict) else None
    return value if isinstance(value, dict) else None


def _app_map_object_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    objects = [
        {
            key: item.get(key)
            for key in ("extension", "url_present", "error")
            if item.get(key) is not None
        }
        for item in value.get("objects", [])
        if isinstance(item, dict)
    ]
    extension_counts: dict[str, int] = {}
    for item in objects:
        extension = item.get("extension")
        if isinstance(extension, str) and extension:
            extension_counts[extension] = extension_counts.get(extension, 0) + 1
    summary = {
        "source": value.get("source"),
        "object_count": value.get("object_count", len(objects)),
        "urls_included": value.get("urls_included"),
        "extension_counts": extension_counts,
        "objects": objects,
    }
    raw = value.get("raw")
    if isinstance(raw, dict):
        summary["raw_keys"] = sorted(raw.keys())
    error = value.get("error")
    if error is not None:
        summary["error"] = error
    return {key: item for key, item in summary.items() if item not in (None, [], {})}


def _app_maps_summary(
    value: Any,
    batch_device_data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    maps = [
        _app_map_entry_summary(entry)
        for entry in value.get("maps", [])
        if isinstance(entry, dict)
    ]
    summary = {
        "source": value.get("source"),
        "available": value.get("available"),
        "map_count": len(maps),
        "current_map_index": selected_current_map_index(value, batch_device_data),
        "maps": maps,
    }
    errors = value.get("errors")
    if isinstance(errors, list):
        summary["error_count"] = len(errors)
        if errors:
            summary["errors"] = errors
    return {key: item for key, item in summary.items() if item not in (None, [], {})}


def _vector_map_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    maps = [
        _vector_map_entry_summary(entry)
        for entry in value.get("maps", [])
        if isinstance(entry, dict)
    ]
    summary = {
        "available": value.get("available"),
        "map_id": value.get("map_id"),
        "map_index": value.get("map_index"),
        "current_map_id": value.get("current_map_id"),
        "available_map_count": value.get("available_map_count", len(maps)),
        "available_maps": value.get("available_maps"),
        "map_names": [
            item.get("map_name")
            for item in maps
            if isinstance(item.get("map_name"), str) and item.get("map_name")
        ],
        "maps": maps,
    }
    return {key: item for key, item in summary.items() if item not in (None, [], {})}


def _current_app_map_summary(
    value: Any,
    batch_device_data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    current_idx = selected_current_map_index(value, batch_device_data)
    maps = value.get("maps")
    if not isinstance(maps, list):
        return None

    for entry in maps:
        if isinstance(entry, dict) and entry.get("idx") == current_idx:
            return _app_map_entry_summary(entry)

    for entry in maps:
        if isinstance(entry, dict) and entry.get("current"):
            return _app_map_entry_summary(entry)
    return None


def _current_vector_map_summary(
    value: Any,
    app_maps: dict[str, Any] | None = None,
    batch_device_data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    maps = value.get("maps")
    current_idx = selected_current_map_index(app_maps, batch_device_data)
    if isinstance(maps, list):
        for entry in maps:
            if isinstance(entry, dict) and entry.get("map_index") == current_idx:
                return _vector_map_entry_summary(entry)

    top_level = _vector_map_entry_summary(value)
    if current_idx is None and top_level:
        return top_level

    if isinstance(top_level, dict) and top_level.get("map_index") == current_idx:
        return top_level
    return None


def _app_map_entry_summary(entry: dict[str, Any]) -> dict[str, Any]:
    summary = entry.get("summary") if isinstance(entry.get("summary"), dict) else {}
    result = {
        "idx": entry.get("idx"),
        "current": entry.get("current"),
        "available": entry.get("available"),
        "created": entry.get("created"),
        "hash_match": entry.get("hash_match"),
        "force_load": entry.get("force_load"),
        "chunk_count": entry.get("chunk_count"),
        "total_area": summary.get("total_area"),
        "map_area_total": summary.get("map_area_total"),
        "map_area_count": summary.get("map_area_count"),
        "spot_count": summary.get("spot_count"),
        "trajectory_count": summary.get("trajectory_count"),
        "trajectory_point_count": summary.get("trajectory_point_count"),
        "trajectory_length_m": summary.get("trajectory_length_m"),
        "cut_relation_count": summary.get("cut_relation_count"),
        "has_live_path": bool(summary.get("trajectory_point_count")),
        "error": entry.get("error"),
    }
    return {key: item for key, item in result.items() if item not in (None, [], {})}


def _vector_map_entry_summary(entry: dict[str, Any]) -> dict[str, Any]:
    result = {
        "map_id": entry.get("map_id"),
        "map_index": entry.get("map_index"),
        "map_name": entry.get("map_name"),
        "total_area": entry.get("total_area"),
        "zone_ids": entry.get("zone_ids"),
        "zone_names": entry.get("zone_names"),
        "spot_ids": entry.get("spot_ids"),
        "contour_ids": entry.get("contour_ids"),
        "contour_count": entry.get("contour_count"),
        "clean_point_count": entry.get("clean_point_count"),
        "cruise_point_count": entry.get("cruise_point_count"),
        "mow_path_count": entry.get("mow_path_count"),
        "mow_path_segment_count": entry.get("mow_path_segment_count"),
        "mow_path_point_count": entry.get("mow_path_point_count"),
        "mow_path_length_m": entry.get("mow_path_length_m"),
        "runtime_track_segment_count": entry.get("runtime_track_segment_count"),
        "runtime_track_point_count": entry.get("runtime_track_point_count"),
        "runtime_track_length_m": entry.get("runtime_track_length_m"),
        "runtime_pose_x": entry.get("runtime_pose_x"),
        "runtime_pose_y": entry.get("runtime_pose_y"),
        "runtime_heading_deg": entry.get("runtime_heading_deg"),
        "has_live_path": entry.get("has_live_path"),
    }
    return {key: item for key, item in result.items() if item not in (None, [], {})}
