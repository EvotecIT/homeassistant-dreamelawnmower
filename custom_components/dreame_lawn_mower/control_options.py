"""Helpers for current-map mowing selectors and actions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MOWING_ACTION_ALL_AREA = "all_area"
MOWING_ACTION_EDGE = "edge"
MOWING_ACTION_ZONE = "zone"
MOWING_ACTION_SPOT = "spot"

MOWING_ACTION_LABELS: dict[str, str] = {
    MOWING_ACTION_ALL_AREA: "All area",
    MOWING_ACTION_EDGE: "Edge",
    MOWING_ACTION_ZONE: "Zone",
    MOWING_ACTION_SPOT: "Spot",
}


def current_map_index(
    app_maps: Mapping[str, Any] | None,
    batch_device_data: Mapping[str, Any] | None = None,
    selected_map_index: int | None = None,
) -> int:
    """Return the current app map index with a safe fallback."""
    available_indices = _known_map_indices(app_maps, batch_device_data)
    if (
        isinstance(selected_map_index, int)
        and selected_map_index >= 0
        and selected_map_index in available_indices
    ):
        return selected_map_index

    if isinstance(app_maps, Mapping):
        current_idx = app_maps.get("current_map_index")
        if isinstance(current_idx, int) and current_idx >= 0:
            return current_idx

    maps = _batch_preference_maps(batch_device_data)
    if len(maps) == 1:
        idx = maps[0].get("idx")
        if isinstance(idx, int):
            return idx
    return 0


def current_app_map_entry(
    app_maps: Mapping[str, Any] | None,
    batch_device_data: Mapping[str, Any] | None = None,
    selected_map_index: int | None = None,
) -> Mapping[str, Any] | None:
    """Return the current app-map entry when available."""
    if not isinstance(app_maps, Mapping):
        return None

    current_idx = current_map_index(
        app_maps,
        batch_device_data,
        selected_map_index=selected_map_index,
    )
    maps = app_maps.get("maps")
    if not isinstance(maps, Sequence) or isinstance(maps, str | bytes | bytearray):
        return None

    for entry in maps:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("idx") == current_idx:
            return entry

    for entry in maps:
        if isinstance(entry, Mapping) and entry.get("current"):
            return entry
    return None


def current_zone_entries(
    batch_device_data: Mapping[str, Any] | None,
    app_maps: Mapping[str, Any] | None = None,
    selected_map_index: int | None = None,
) -> list[dict[str, Any]]:
    """Return selectable zone entries for the current map."""
    current_idx = current_map_index(
        app_maps,
        batch_device_data,
        selected_map_index=selected_map_index,
    )
    for entry in _batch_preference_maps(batch_device_data):
        if entry.get("idx") != current_idx:
            continue
        preferences = entry.get("preferences")
        if not isinstance(preferences, Sequence) or isinstance(
            preferences,
            str | bytes | bytearray,
        ):
            return []
        zones: list[dict[str, Any]] = []
        for item in preferences:
            if not isinstance(item, Mapping):
                continue
            area_id = item.get("area_id")
            if not isinstance(area_id, int):
                continue
            # Area 0 is the whole-lawn/global entry; 200+ looks like edge metadata.
            if area_id <= 0 or area_id >= 200:
                continue
            zones.append(
                {
                    "area_id": area_id,
                    "label": f"Zone #{area_id}",
                    "map_index": current_idx,
                    "preference": dict(item),
                }
            )
        return zones
    return []


def current_contour_entries(
    vector_map_details: Mapping[str, Any] | None,
    app_maps: Mapping[str, Any] | None = None,
    batch_device_data: Mapping[str, Any] | None = None,
    selected_map_index: int | None = None,
) -> list[dict[str, Any]]:
    """Return selectable contour entries for the current or selected map."""
    if not isinstance(vector_map_details, Mapping):
        return []

    current_idx = current_map_index(
        app_maps,
        batch_device_data,
        selected_map_index=selected_map_index,
    )
    maps = vector_map_details.get("maps")
    if not isinstance(maps, Sequence) or isinstance(maps, str | bytes | bytearray):
        return []

    result: list[dict[str, Any]] = []
    for map_entry in maps:
        if (
            not isinstance(map_entry, Mapping)
            or map_entry.get("map_index") != current_idx
        ):
            continue
        contour_ids = map_entry.get("contour_ids")
        if not isinstance(contour_ids, Sequence) or isinstance(
            contour_ids,
            str | bytes | bytearray,
        ):
            return []
        for contour_id in contour_ids:
            normalized = _normalize_contour_id(contour_id)
            if normalized is None:
                continue
            result.append(
                {
                    "contour_id": normalized,
                    "label": contour_label(normalized),
                    "map_index": current_idx,
                }
            )
        return result
    return []


def current_spot_entries(
    app_maps: Mapping[str, Any] | None,
    batch_device_data: Mapping[str, Any] | None = None,
    selected_map_index: int | None = None,
) -> list[dict[str, Any]]:
    """Return selectable spot entries for the current map."""
    entry = current_app_map_entry(
        app_maps,
        batch_device_data,
        selected_map_index=selected_map_index,
    )
    if not isinstance(entry, Mapping):
        return []

    payload = entry.get("payload")
    if not isinstance(payload, Mapping):
        return []

    spots = payload.get("spot")
    if not isinstance(spots, Sequence) or isinstance(spots, str | bytes | bytearray):
        return []

    result: list[dict[str, Any]] = []
    for item in spots:
        if not isinstance(item, Mapping):
            continue
        spot_id = item.get("id")
        if not isinstance(spot_id, int):
            continue
        center = spot_center(item)
        result.append(
            {
                "spot_id": spot_id,
                "label": f"Spot #{spot_id}",
                "center": center,
                "spot": dict(item),
            }
        )
    return result


def find_spot_center(
    app_maps: Mapping[str, Any] | None,
    spot_id: int,
    batch_device_data: Mapping[str, Any] | None = None,
    selected_map_index: int | None = None,
) -> tuple[int, int] | None:
    """Return the center point for a spot id on the current map."""
    for entry in current_spot_entries(
        app_maps,
        batch_device_data,
        selected_map_index=selected_map_index,
    ):
        if entry["spot_id"] == spot_id:
            return entry["center"]
    return None


def map_entries(
    app_maps: Mapping[str, Any] | None,
    batch_device_data: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return selectable map entries with stable labels."""
    maps = app_maps.get("maps") if isinstance(app_maps, Mapping) else None
    if isinstance(maps, Sequence) and not isinstance(maps, str | bytes | bytearray):
        entries: list[dict[str, Any]] = []
        for item in maps:
            if not isinstance(item, Mapping):
                continue
            map_index = item.get("idx")
            if not isinstance(map_index, int) or map_index < 0:
                continue
            entries.append(
                {
                    "map_index": map_index,
                    "label": map_label(map_index, _map_name(item)),
                    "current": bool(item.get("current")),
                    "available": bool(item.get("available")),
                    "entry": dict(item),
                }
            )
        if entries:
            return sorted(entries, key=lambda entry: int(entry["map_index"]))

    fallback_entries: list[dict[str, Any]] = []
    for item in _batch_preference_maps(batch_device_data):
        map_index = item.get("idx")
        if not isinstance(map_index, int) or map_index < 0:
            continue
        fallback_entries.append(
            {
                "map_index": map_index,
                "label": map_label(map_index),
                "current": False,
                "available": bool(item.get("preferences")),
                "entry": dict(item),
            }
        )
    return sorted(fallback_entries, key=lambda entry: int(entry["map_index"]))


def mowing_action_label(action: str) -> str:
    """Return the display label for a mowing action key."""
    return MOWING_ACTION_LABELS.get(
        action, MOWING_ACTION_LABELS[MOWING_ACTION_ALL_AREA]
    )


def map_label(map_index: int, name: str | None = None) -> str:
    """Return the display label for an app map entry."""
    suffix = f"#{map_index + 1}"
    if name:
        return f"{name} ({suffix})"
    return f"Map {suffix}"


def zone_label(area_id: int) -> str:
    """Return the display label for a zone id."""
    return f"Zone #{area_id}"


def contour_label(contour_id: tuple[int, int]) -> str:
    """Return the display label for a contour id pair."""
    return f"Edge ({contour_id[0]}, {contour_id[1]})"


def spot_label(spot_id: int) -> str:
    """Return the display label for a spot id."""
    return f"Spot #{spot_id}"


def spot_center(value: Mapping[str, Any]) -> tuple[int, int] | None:
    """Return the integer center point of a spot polygon."""
    coordinates = value.get("data")
    if not isinstance(coordinates, Sequence) or isinstance(
        coordinates,
        str | bytes | bytearray,
    ):
        return None

    x_coords: list[int] = []
    y_coords: list[int] = []
    for point in coordinates:
        if (
            not isinstance(point, Sequence)
            or isinstance(point, str | bytes | bytearray)
            or len(point) < 2
        ):
            continue
        x = point[0]
        y = point[1]
        if isinstance(x, int | float) and isinstance(y, int | float):
            x_coords.append(int(round(x)))
            y_coords.append(int(round(y)))

    if not x_coords or not y_coords:
        return None
    return (
        int(round((min(x_coords) + max(x_coords)) / 2)),
        int(round((min(y_coords) + max(y_coords)) / 2)),
    )


def _batch_preference_maps(
    batch_device_data: Mapping[str, Any] | None,
) -> list[Mapping[str, Any]]:
    if not isinstance(batch_device_data, Mapping):
        return []
    preferences = batch_device_data.get("batch_mowing_preferences")
    if not isinstance(preferences, Mapping):
        return []
    maps = preferences.get("maps")
    if not isinstance(maps, Sequence) or isinstance(maps, str | bytes | bytearray):
        return []
    return [entry for entry in maps if isinstance(entry, Mapping)]


def _known_map_indices(
    app_maps: Mapping[str, Any] | None,
    batch_device_data: Mapping[str, Any] | None,
) -> set[int]:
    indices: set[int] = set()
    for entry in map_entries(app_maps, batch_device_data):
        map_index = entry.get("map_index")
        if isinstance(map_index, int) and map_index >= 0:
            indices.add(map_index)
    return indices


def _map_name(entry: Mapping[str, Any]) -> str | None:
    summary = entry.get("summary")
    if isinstance(summary, Mapping):
        summary_name = summary.get("name")
        if isinstance(summary_name, str) and summary_name.strip():
            return summary_name.strip()

    payload = entry.get("payload")
    if isinstance(payload, Mapping):
        payload_name = payload.get("name")
        if isinstance(payload_name, str) and payload_name.strip():
            return payload_name.strip()

    name = entry.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _normalize_contour_id(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return None
    if len(value) < 2:
        return None
    try:
        return int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None
