"""Control metadata from verified mower-native map records."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def app_map_zone_metadata(zone_records: Sequence[Any]) -> list[dict[str, Any]]:
    """Summarize lawn records without exposing their coordinates."""
    result = []
    for record in zone_records:
        if not isinstance(record, Mapping):
            continue
        zone_id = record.get("id")
        if type(zone_id) is not int or zone_id <= 0 or "name" not in record:
            continue
        name = record.get("name")
        if name is not None and not isinstance(name, str):
            continue
        result.append(
            {
                "zone_id": zone_id,
                "name": name.strip()[:200]
                if isinstance(name, str) and name.strip()
                else None,
            }
        )
    return result


def verified_app_map_zone_names(
    app_maps: Any,
    map_index: int | None,
) -> dict[int, str | None]:
    """Use labels only from one created, hash-verified matching native map.

    Callers retain their existing zone membership. An explicit empty name
    overrides a stale cloud name, while unavailable native names use fallback.
    """
    if not isinstance(app_maps, Mapping) or app_maps.get("map_list_valid") is not True:
        return {}
    maps = app_maps.get("maps")
    if not isinstance(maps, Sequence) or isinstance(maps, str | bytes | bytearray):
        return {}
    candidates = [
        entry
        for entry in maps
        if isinstance(entry, Mapping)
        and type(entry.get("idx")) is int
        and entry["idx"] == map_index
    ]
    if len(candidates) != 1:
        return {}
    entry = candidates[0]
    if any(
        entry.get(flag) is not True for flag in ("created", "available", "hash_match")
    ):
        return {}
    summary = entry.get("summary")
    zones = summary.get("zones") if isinstance(summary, Mapping) else None
    if not isinstance(zones, list):
        return {}
    result: dict[int, str | None] = {}
    for zone in zones:
        if not isinstance(zone, Mapping):
            return {}
        zone_id, name = zone.get("zone_id"), zone.get("name")
        if (
            type(zone_id) is not int
            or zone_id <= 0
            or zone_id in result
            or (name is not None and (not isinstance(name, str) or len(name) > 200))
        ):
            return {}
        result[zone_id] = name.strip() or None if name else None
    return result


def _inside_polygon(x: float, y: float, polygon: Sequence[Sequence[float]]) -> bool:
    """Even-odd ray casting; points on an edge may fall on either side."""
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1, x2, y2 = previous[0], previous[1], current[0], current[1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside


def app_map_patrol_point_zones(
    zone_records: Sequence[Any], point_entries: Sequence[Any]
) -> dict[str, int]:
    """Map each patrol point id to the lawn zone whose outline contains it."""
    from .maintenance_point_records import app_map_patrol_point_ids

    zones: list[tuple[int, list[tuple[float, float]]]] = []
    for record in zone_records:
        if (
            not isinstance(record, Mapping)
            or type(record.get("id")) is not int or record["id"] <= 0
            or record.get("type", 0) != 0
        ):
            continue
        data = record.get("data")
        if not isinstance(data, Sequence) or isinstance(data, str | bytes | bytearray):
            continue
        polygon = [
            (float(point[0]), float(point[1])) for point in data
            if isinstance(point, Sequence) and len(point) >= 2
            and all(isinstance(value, int | float) and not isinstance(value, bool)
                    and math.isfinite(value)
                    for value in point[:2])
        ]
        if len(polygon) == len(data) and len(polygon) >= 3:
            zones.append((record["id"], polygon))
    result: dict[str, int] = {}
    for entry in point_entries:
        point_ids = app_map_patrol_point_ids([entry])
        if not point_ids or str(point_ids[0]) in result:
            continue
        x, y = entry["point"][0], entry["point"][1]
        zone_id = next(
            (zone_id for zone_id, polygon in zones if _inside_polygon(x, y, polygon)),
            None,
        )
        if zone_id is not None:
            result[str(entry["id"])] = zone_id
    return result


def current_app_map_patrol_point_ids_in_zones(
    app_maps: Any, zone_ids: Sequence[int]
) -> list[int]:
    """Return active-map patrol points located inside the requested zones."""
    summary = _current_verified_app_map_summary(app_maps)
    zones = summary.get("patrol_point_zones") if summary is not None else None
    if not isinstance(zones, Mapping):
        return []
    wanted = set(zone_ids)
    return [
        point_id for point_id in current_app_map_patrol_point_ids(app_maps)
        if zones.get(str(point_id)) in wanted
    ]


def _current_verified_app_map_summary(app_maps: Any) -> Mapping[str, Any] | None:
    if not isinstance(app_maps, Mapping) or app_maps.get("map_list_valid") is not True:
        return None
    maps = app_maps.get("maps")
    current_idx = app_maps.get("current_map_index")
    if type(current_idx) is not int or not isinstance(maps, Sequence) or isinstance(
        maps, str | bytes | bytearray
    ):
        return None
    candidates = [
        item for item in maps
        if isinstance(item, Mapping) and item.get("idx") == current_idx
        and type(item.get("idx")) is int
    ]
    if len(candidates) != 1 or any(
        candidates[0].get(flag) is not True
        for flag in ("created", "available", "hash_match")
    ):
        return None
    summary = candidates[0].get("summary")
    return summary if isinstance(summary, Mapping) else None


def current_app_map_patrol_point_ids(app_maps: Any) -> list[int]:
    """Return patrol point ids decoded from the active, verified app map."""
    if not isinstance(app_maps, Mapping) or app_maps.get("map_list_valid") is not True:
        return []
    maps = app_maps.get("maps")
    current_idx = app_maps.get("current_map_index")
    if (
        type(current_idx) is not int
        or current_idx < 0
        or not isinstance(maps, Sequence)
        or isinstance(maps, str | bytes | bytearray)
    ):
        return []
    candidates = [
        item
        for item in maps
        if isinstance(item, Mapping)
        and type(item.get("idx")) is int
        and item["idx"] == current_idx
    ]
    if len(candidates) != 1:
        return []
    item = candidates[0]
    if any(
        item.get(flag) is not True for flag in ("created", "available", "hash_match")
    ):
        return []
    summary = item.get("summary")
    point_ids = (
        summary.get("patrol_point_ids") if isinstance(summary, Mapping) else None
    )
    if not isinstance(point_ids, Sequence) or isinstance(
        point_ids, str | bytes | bytearray
    ):
        return []
    return [
        point_id for point_id in point_ids if type(point_id) is int and point_id > 0
    ]
