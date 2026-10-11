"""Adapt verified mower-native app maps to the shared geometry model."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .app_map_rendering import _is_app_map_pathway
from .maintenance_point_records import is_app_map_maintenance_point_record
from .vector_map import (
    DreameLawnMowerVectorBoundary,
    DreameLawnMowerVectorMap,
    DreameLawnMowerVectorMowPath,
    DreameLawnMowerVectorPath,
    DreameLawnMowerVectorZone,
)

MAX_APP_MAP_POINTS = 50000


def parse_app_map_geometry(
    app_maps: Mapping[str, Any] | None, map_index: int | None,
) -> DreameLawnMowerVectorMap | None:
    """Bind geometry only to one current, created, hash-verified app slot.

    Unverified, ambiguous or incomplete records cannot establish a coordinate
    frame for runtime positions. Pathways remain open navigation lines.
    """
    if (
        app_maps is None
        or app_maps.get("map_list_valid") is not True
        or type(map_index) is not int
        or app_maps.get("current_map_index") != map_index
    ):
        return None
    entries = app_maps.get("maps")
    if not isinstance(entries, list):
        return None
    matches = [
        entry for entry in entries if isinstance(entry, Mapping)
        and type(entry.get("idx")) is int and entry["idx"] == map_index
    ]
    if len(matches) != 1:
        return None
    entry = matches[0]
    if any(entry.get(flag) is not True for flag in (
        "created", "available", "hash_match",
    )):
        return None
    payload = entry.get("payload")
    if not isinstance(payload, Mapping):
        return None
    try:
        return _geometry(payload, map_index)
    except (ValueError, OverflowError):
        return None


def _points(value: Any, budget: list[int]) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list):
        raise ValueError("App-map coordinates must be a list.")
    budget[0] += len(value)
    if budget[0] > MAX_APP_MAP_POINTS:
        raise ValueError("App-map geometry exceeds the point budget.")
    result = []
    for point in value:
        if not isinstance(point, list) or len(point) < 2:
            raise ValueError("Incomplete app-map coordinate.")
        x, y = point[:2]
        if any(
            isinstance(v, bool) or not isinstance(v, int | float)
            or not math.isfinite(v) or abs(v) > 10000000 for v in (x, y)
        ):
            raise ValueError("Invalid app-map coordinate.")
        result.append((round(x), round(y)))
    return tuple(result)


def _records(payload: Mapping[str, Any], key: str) -> list[Any]:
    records = payload.get(key, [])
    if not isinstance(records, list) or len(records) > MAX_APP_MAP_POINTS:
        raise ValueError("Invalid app-map records.")
    return records


def _maintenance_points(
    payload: Mapping[str, Any], budget: list[int],
) -> tuple[tuple[tuple[int, int], ...], tuple[int, ...]]:
    """Keep recognized native point records without guessing future semantics."""
    points = []
    ids = []
    for entry in _records(payload, "point"):
        budget[0] += 1
        if budget[0] > MAX_APP_MAP_POINTS:
            raise ValueError("App-map geometry exceeds the point budget.")
        if isinstance(entry, Mapping):
            if not is_app_map_maintenance_point_record(entry):
                continue
            point_id = entry.get("id")
            if type(point_id) is not int or point_id <= 0 or point_id in ids:
                continue
            coordinates = entry.get("point")
            if not isinstance(coordinates, list):
                continue
        else:
            coordinates = entry
            point_id = None
        try:
            point = _points([coordinates], [0])[0]
        except (ValueError, OverflowError):
            continue
        points.append(point)
        if point_id is not None:
            ids.append(point_id)
    return tuple(points), tuple(ids)


def _geometry(payload: Mapping[str, Any], map_index: int) -> DreameLawnMowerVectorMap:
    budget = [0]
    zones: list[DreameLawnMowerVectorZone] = []
    paths = []
    spots: list[DreameLawnMowerVectorZone] = []
    for key, target in (("map", zones), ("spot", spots)):
        seen = set()
        for item in _records(payload, key):
            if not isinstance(item, Mapping):
                raise ValueError("Invalid app-map area.")
            area_id = item.get("id")
            if type(area_id) is not int or area_id in seen:
                raise ValueError("Ambiguous app-map area identity.")
            seen.add(area_id)
            points = _points(item.get("data"), budget)
            if key == "map" and _is_app_map_pathway(item):
                if len(points) < 2:
                    raise ValueError("Incomplete app-map pathway.")
                paths.append(DreameLawnMowerVectorPath(area_id, points))
            else:
                if len(set(points)) < 3:
                    raise ValueError("Incomplete app-map polygon.")
                name = item.get("name")
                area = item.get("area")
                target.append(DreameLawnMowerVectorZone(
                    area_id, points,
                    name=name.strip()[:200] if isinstance(name, str) else "",
                    area=float(area) if isinstance(area, int | float)
                    and not isinstance(area, bool)
                    and math.isfinite(area) and area > 0 else 0,
                ))
    # Historical trajectory must never enlarge the frame used to validate pose.
    shapes: tuple[DreameLawnMowerVectorZone | DreameLawnMowerVectorPath, ...] = (
        *zones, *spots, *paths,
    )
    static_points = [point for shape in shapes for point in shape.points]
    if not zones or not static_points:
        raise ValueError("No app-map lawn geometry.")
    xs, ys = zip(*static_points, strict=True)
    boundary = DreameLawnMowerVectorBoundary(min(xs), min(ys), max(xs), max(ys))
    if boundary.width <= 0 or boundary.height <= 0:
        raise ValueError("Empty app-map boundary.")
    trajectories = []
    for item in _records(payload, "trajectory"):
        points = _points(
            item.get("data") if isinstance(item, Mapping) else item, budget,
        )
        if len(points) >= 2:
            trajectories.append(points)
    clean_points, clean_point_ids = _maintenance_points(payload, budget)
    name = payload.get("name")
    total_area = payload.get("total_area")
    return DreameLawnMowerVectorMap(
        zones=tuple(zones), paths=tuple(paths), spot_areas=tuple(spots),
        clean_points=clean_points, clean_point_ids=clean_point_ids, boundary=boundary,
        name=name.strip()[:200] if isinstance(name, str) else "",
        total_area=float(total_area) if isinstance(total_area, int | float)
        and not isinstance(total_area, bool)
        and math.isfinite(total_area) and total_area > 0
        else sum(zone.area for zone in zones),
        map_id=map_index + 1, map_index=map_index, current_map_id=map_index + 1,
        mow_paths=(DreameLawnMowerVectorMowPath(0, tuple(trajectories)),)
        if trajectories else (),
    )
