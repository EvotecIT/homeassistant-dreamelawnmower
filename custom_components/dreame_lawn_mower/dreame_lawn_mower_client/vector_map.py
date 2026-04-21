"""Read-only mower vector-map parsing and rendering helpers."""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .models import DreameLawnMowerMapSummary

_PATH_SENTINEL = (32767, -32768)
_MAX_IMAGE_SIDE = 2048
_MIN_IMAGE_SIDE = 400
_PADDING = 40
_BACKGROUND_COLOR = (245, 245, 240, 255)
_FORBIDDEN_COLOR = (200, 50, 50, 120)
_FORBIDDEN_OUTLINE_COLOR = (200, 50, 50, 220)
_PATH_COLOR = (180, 180, 180, 200)
_PATH_WIDTH = 3
_MOW_PATH_COLOR = (50, 120, 50, 180)
_MOW_PATH_WIDTH = 2
_RUNTIME_PATH_COLOR = (55, 145, 220, 220)
_RUNTIME_PATH_WIDTH = 4
_RUNTIME_POSITION_COLOR = (255, 140, 0, 255)
_SPOT_COLOR = (110, 170, 225, 120)
_SPOT_OUTLINE_COLOR = (70, 130, 190, 220)
_POINT_COLOR = (55, 55, 55, 255)
_LABEL_COLOR = (60, 60, 60, 255)
_ZONE_COLORS = (
    ((164, 210, 145, 200), (134, 190, 115, 255)),
    ((160, 200, 220, 200), (130, 170, 200, 255)),
    ((240, 200, 170, 200), (220, 175, 140, 255)),
    ((240, 180, 180, 200), (220, 150, 150, 255)),
    ((230, 220, 160, 200), (210, 200, 130, 255)),
    ((190, 170, 220, 200), (170, 145, 200, 255)),
)


@dataclass(slots=True)
class DreameLawnMowerVectorBoundary:
    """Bounding box for the mower vector map."""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1


@dataclass(slots=True)
class DreameLawnMowerVectorZone:
    """Polygonal mower zone or area."""

    zone_id: int
    points: tuple[tuple[int, int], ...]
    name: str = ""
    zone_type: int = 0
    shape_type: int = 0
    area: float = 0
    time_minutes: int = 0
    estimated_minutes: int = 0


@dataclass(slots=True)
class DreameLawnMowerVectorPath:
    """Navigation path between mower zones."""

    path_id: int
    points: tuple[tuple[int, int], ...]
    path_type: int = 0


@dataclass(slots=True)
class DreameLawnMowerVectorContour:
    """Contour entry used by edge mowing."""

    contour_id: tuple[int, int]
    points: tuple[tuple[int, int], ...]
    contour_type: int = 0
    shape_type: int = 0


@dataclass(slots=True)
class DreameLawnMowerAvailableMap:
    """Map descriptor discovered in batch vector-map data."""

    map_id: int
    map_index: int
    name: str = ""
    total_area: float = 0


@dataclass(slots=True)
class DreameLawnMowerVectorMowPath:
    """Historical mowing trail segments."""

    zone_id: int
    segments: tuple[tuple[tuple[int, int], ...], ...]


@dataclass(slots=True)
class DreameLawnMowerVectorMap:
    """Normalized mower vector-map payload."""

    zones: tuple[DreameLawnMowerVectorZone, ...] = field(default_factory=tuple)
    forbidden_areas: tuple[DreameLawnMowerVectorZone, ...] = field(
        default_factory=tuple
    )
    spot_areas: tuple[DreameLawnMowerVectorZone, ...] = field(default_factory=tuple)
    paths: tuple[DreameLawnMowerVectorPath, ...] = field(default_factory=tuple)
    contours: tuple[DreameLawnMowerVectorContour, ...] = field(default_factory=tuple)
    clean_points: tuple[tuple[int, int], ...] = field(default_factory=tuple)
    cruise_points: tuple[tuple[int, int], ...] = field(default_factory=tuple)
    obstacles: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    boundary: DreameLawnMowerVectorBoundary | None = None
    total_area: float = 0
    name: str = ""
    map_id: int = 1
    map_index: int = 0
    current_map_id: int | None = None
    available_maps: tuple[DreameLawnMowerAvailableMap, ...] = field(default_factory=tuple)
    last_updated: float | None = None
    mow_paths: tuple[DreameLawnMowerVectorMowPath, ...] = field(default_factory=tuple)
    maps: Mapping[int, "DreameLawnMowerVectorMap"] = field(
        default_factory=dict,
        repr=False,
    )


def parse_batch_vector_map(
    batch_data: Mapping[str, Any] | None,
) -> DreameLawnMowerVectorMap | None:
    """Parse cloud batch-map response data into a normalized vector map."""
    if not isinstance(batch_data, Mapping) or not batch_data:
        return None

    raw_map = _reassemble_batch_chunks(batch_data, "MAP")
    if not raw_map:
        return None

    entries: list[str] = []
    for part in _split_map_parts(raw_map, batch_data.get("MAP.info")):
        try:
            decoded = json.loads(part)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, list):
            entries.extend(item for item in decoded if isinstance(item, str))

    parsed_maps: list[DreameLawnMowerVectorMap] = []
    for entry in entries:
        try:
            vector_map = _parse_vector_map_json(entry)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        parsed_maps.append(vector_map)

    if not parsed_maps:
        return None

    primary = next(
        (vector_map for vector_map in parsed_maps if vector_map.map_index == 0),
        parsed_maps[0],
    )

    available_maps = tuple(
        DreameLawnMowerAvailableMap(
            map_id=vector_map.map_id,
            map_index=vector_map.map_index,
            name=vector_map.name,
            total_area=vector_map.total_area,
        )
        for vector_map in sorted(parsed_maps, key=lambda item: item.map_id)
    )

    mow_paths = _parse_mow_paths(batch_data)
    parsed_maps_by_id: dict[int, DreameLawnMowerVectorMap] = {
        vector_map.map_id: vector_map for vector_map in parsed_maps
    }
    for vector_map in parsed_maps_by_id.values():
        vector_map.available_maps = available_maps
        vector_map.mow_paths = mow_paths

    primary.available_maps = available_maps
    primary.mow_paths = mow_paths
    primary.maps = parsed_maps_by_id
    return primary


def render_vector_map_png(
    vector_map: DreameLawnMowerVectorMap | None,
    *,
    runtime_track_segments: Sequence[Sequence[tuple[int, int]]] | None = None,
    runtime_position: tuple[int, int] | None = None,
) -> bytes | None:
    """Render a mower vector map to PNG bytes."""
    if vector_map is None or vector_map.boundary is None:
        return None

    boundary = vector_map.boundary
    map_width = max(boundary.width, 1)
    map_height = max(boundary.height, 1)
    scale = min(
        (_MAX_IMAGE_SIDE - (2 * _PADDING)) / map_width,
        (_MAX_IMAGE_SIDE - (2 * _PADDING)) / map_height,
    )
    scale = max(scale, _MIN_IMAGE_SIDE / max(map_width, map_height, 1))

    image_width = int(map_width * scale) + (2 * _PADDING)
    image_height = int(map_height * scale) + (2 * _PADDING)
    image = Image.new("RGBA", (image_width, image_height), _BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    def to_pixel(x: int, y: int) -> tuple[int, int]:
        px = image_width - (int((x - boundary.x1) * scale) + _PADDING)
        py = int((y - boundary.y1) * scale) + _PADDING
        return px, py

    for index, zone in enumerate(vector_map.zones):
        if len(zone.points) < 3:
            continue
        fill_color, outline_color = _ZONE_COLORS[index % len(_ZONE_COLORS)]
        polygon = [to_pixel(x, y) for x, y in zone.points]
        draw.polygon(polygon, fill=fill_color, outline=outline_color, width=2)

    for area in vector_map.forbidden_areas:
        if len(area.points) < 3:
            continue
        polygon = [to_pixel(x, y) for x, y in area.points]
        draw.polygon(
            polygon,
            fill=_FORBIDDEN_COLOR,
            outline=_FORBIDDEN_OUTLINE_COLOR,
            width=2,
        )

    for area in vector_map.spot_areas:
        if len(area.points) < 3:
            continue
        polygon = [to_pixel(x, y) for x, y in area.points]
        draw.polygon(
            polygon,
            fill=_SPOT_COLOR,
            outline=_SPOT_OUTLINE_COLOR,
            width=2,
        )

    for mow_path in vector_map.mow_paths:
        for segment in mow_path.segments:
            if len(segment) < 2:
                continue
            draw.line(
                [to_pixel(x, y) for x, y in segment],
                fill=_MOW_PATH_COLOR,
                width=_MOW_PATH_WIDTH,
            )

    for segment in runtime_track_segments or ():
        if len(segment) < 2:
            continue
        draw.line(
            [to_pixel(x, y) for x, y in segment],
            fill=_RUNTIME_PATH_COLOR,
            width=_RUNTIME_PATH_WIDTH,
        )

    for path in vector_map.paths:
        if len(path.points) < 2:
            continue
        draw.line(
            [to_pixel(x, y) for x, y in path.points],
            fill=_PATH_COLOR,
            width=_PATH_WIDTH,
        )

    for point in (*vector_map.clean_points, *vector_map.cruise_points):
        px, py = to_pixel(point[0], point[1])
        draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill=_POINT_COLOR)

    if runtime_position is not None:
        px, py = to_pixel(runtime_position[0], runtime_position[1])
        draw.ellipse((px - 6, py - 6, px + 6, py + 6), fill=_RUNTIME_POSITION_COLOR)

    for zone in vector_map.zones:
        if len(zone.points) < 3 or not zone.name:
            continue
        center_x = sum(point[0] for point in zone.points) // len(zone.points)
        center_y = sum(point[1] for point in zone.points) // len(zone.points)
        px, py = to_pixel(center_x, center_y)
        for dx, dy in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            draw.text(
                (px + dx, py + dy),
                zone.name,
                fill=(255, 255, 255, 150),
                font=font,
                anchor="mm",
            )
        draw.text((px, py), zone.name, fill=_LABEL_COLOR, font=font, anchor="mm")

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def vector_map_to_summary(
    vector_map: DreameLawnMowerVectorMap | None,
) -> DreameLawnMowerMapSummary | None:
    """Convert a normalized vector map to the shared map summary shape."""
    if vector_map is None:
        return None

    boundary = vector_map.boundary
    path_point_count = sum(len(path.points) for path in vector_map.paths)
    path_point_count += sum(
        len(segment)
        for mow_path in vector_map.mow_paths
        for segment in mow_path.segments
    )
    return DreameLawnMowerMapSummary(
        available=bool(
            boundary is not None
            and (
                vector_map.zones
                or vector_map.paths
                or vector_map.mow_paths
                or vector_map.spot_areas
            )
        ),
        map_id=vector_map.map_index,
        width=boundary.width if boundary is not None else None,
        height=boundary.height if boundary is not None else None,
        saved_map=bool(vector_map.zones),
        segment_count=len(vector_map.zones),
        active_area_count=len(vector_map.zones),
        active_point_count=len(vector_map.clean_points) + len(vector_map.cruise_points),
        path_point_count=path_point_count,
        no_go_area_count=len(vector_map.forbidden_areas),
        spot_area_count=len(vector_map.spot_areas),
        pathway_count=len(vector_map.paths),
        obstacle_count=len(vector_map.obstacles),
    )


def vector_map_to_details(
    vector_map: DreameLawnMowerVectorMap | None,
) -> dict[str, Any]:
    """Return JSON-safe details for live/vector mower maps."""
    if vector_map is None:
        return {}

    mow_path_segment_count = sum(
        len(mow_path.segments) for mow_path in vector_map.mow_paths
    )
    mow_path_point_count = sum(
        len(segment)
        for mow_path in vector_map.mow_paths
        for segment in mow_path.segments
    )
    mow_path_length_m = sum(
        _coordinate_path_length_m(segment)
        for mow_path in vector_map.mow_paths
        for segment in mow_path.segments
    )
    zone_names = [
        zone.name
        for zone in vector_map.zones
        if zone.name
    ]
    contour_ids = [list(contour.contour_id) for contour in vector_map.contours]
    available_maps = [
        {
            "map_id": map_entry.map_id,
            "map_index": map_entry.map_index,
            "name": map_entry.name or None,
            "total_area": map_entry.total_area or None,
        }
        for map_entry in vector_map.available_maps
    ]
    parsed_maps = (
        vector_map.maps.values()
        if isinstance(vector_map.maps, Mapping) and vector_map.maps
        else (vector_map,)
    )
    maps = [
        {
            "map_id": parsed_map.map_id,
            "map_index": parsed_map.map_index,
            "map_name": parsed_map.name or None,
            "total_area": parsed_map.total_area or None,
            "zone_ids": [zone.zone_id for zone in parsed_map.zones],
            "zone_names": [zone.name for zone in parsed_map.zones if zone.name],
            "spot_ids": [spot.zone_id for spot in parsed_map.spot_areas],
            "contour_ids": [list(contour.contour_id) for contour in parsed_map.contours],
            "contour_count": len(parsed_map.contours),
            "clean_point_count": len(parsed_map.clean_points),
            "cruise_point_count": len(parsed_map.cruise_points),
            "mow_path_count": len(parsed_map.mow_paths),
            "mow_path_segment_count": sum(
                len(mow_path.segments) for mow_path in parsed_map.mow_paths
            ),
            "mow_path_point_count": sum(
                len(segment)
                for mow_path in parsed_map.mow_paths
                for segment in mow_path.segments
            ),
            "mow_path_length_m": round(
                sum(
                    _coordinate_path_length_m(segment)
                    for mow_path in parsed_map.mow_paths
                    for segment in mow_path.segments
                ),
                2,
            ),
            "has_live_path": any(mow_path.segments for mow_path in parsed_map.mow_paths),
        }
        for parsed_map in sorted(parsed_maps, key=lambda item: item.map_index)
    ]
    return {
        "map_name": vector_map.name or None,
        "map_id": vector_map.map_id,
        "map_index": vector_map.map_index,
        "current_map_id": vector_map.current_map_id,
        "total_area": vector_map.total_area or None,
        "zone_count": len(vector_map.zones),
        "zone_names": zone_names,
        "forbidden_area_count": len(vector_map.forbidden_areas),
        "spot_area_count": len(vector_map.spot_areas),
        "pathway_count": len(vector_map.paths),
        "contour_count": len(vector_map.contours),
        "contour_ids": contour_ids,
        "clean_point_count": len(vector_map.clean_points),
        "cruise_point_count": len(vector_map.cruise_points),
        "mow_path_count": len(vector_map.mow_paths),
        "mow_path_segment_count": mow_path_segment_count,
        "mow_path_point_count": mow_path_point_count,
        "mow_path_length_m": round(mow_path_length_m, 2),
        "has_live_path": mow_path_point_count > 0,
        "obstacle_count": len(vector_map.obstacles),
        "available_map_count": len(available_maps),
        "available_maps": available_maps,
        "maps": maps,
        "last_updated": vector_map.last_updated,
    }


def _coordinate_path_length_m(points: Sequence[tuple[int, int]]) -> float:
    """Return an approximate path length in meters for centimeter coordinates."""
    if len(points) < 2:
        return 0.0

    total = 0.0
    previous = points[0]
    for current in points[1:]:
        total += math.hypot(current[0] - previous[0], current[1] - previous[1])
        previous = current
    return total / 100.0


def _reassemble_batch_chunks(
    batch_data: Mapping[str, Any],
    prefix: str,
) -> str | None:
    pattern = re.compile(rf"^{re.escape(prefix)}\.(\d+)$")
    chunks: list[tuple[int, str]] = []
    for key, value in batch_data.items():
        match = pattern.match(str(key))
        if match is None:
            continue
        text = _coerce_text(value)
        if text is None:
            continue
        chunks.append((int(match.group(1)), text))

    if not chunks:
        return None

    chunks.sort(key=lambda item: item[0])
    return "".join(text for _, text in chunks)


def _split_map_parts(raw_map: str, map_info: Any) -> tuple[str, ...]:
    split_point = _coerce_int(map_info)
    if split_point is not None and 0 < split_point < len(raw_map):
        return raw_map[:split_point].strip(), raw_map[split_point:].strip()
    return (raw_map.strip(),)


def _parse_vector_map_json(value: str) -> DreameLawnMowerVectorMap:
    data = json.loads(value)
    if not isinstance(data, Mapping):
        raise ValueError("Vector map JSON payload must decode to an object.")

    boundary_data = data.get("boundary")
    boundary = None
    if isinstance(boundary_data, Mapping):
        boundary = DreameLawnMowerVectorBoundary(
            x1=int(boundary_data["x1"]),
            y1=int(boundary_data["y1"]),
            x2=int(boundary_data["x2"]),
            y2=int(boundary_data["y2"]),
        )

    return DreameLawnMowerVectorMap(
        zones=_parse_zone_collection(data.get("mowingAreas")),
        forbidden_areas=_parse_zone_collection(data.get("forbiddenAreas")),
        spot_areas=_parse_zone_collection(data.get("spotAreas")),
        paths=_parse_path_collection(data.get("paths")),
        contours=_parse_contour_collection(data.get("contours")),
        clean_points=_parse_point_collection(data.get("cleanPoints")),
        cruise_points=_parse_point_collection(data.get("cruisePoints")),
        obstacles=_parse_object_collection(data.get("obstacles")),
        boundary=boundary,
        total_area=float(data.get("totalArea") or 0),
        name=str(data.get("name") or ""),
        map_id=_map_id_from_index(int(data.get("mapIndex") or 0)),
        map_index=int(data.get("mapIndex") or 0),
        current_map_id=_coerce_int(data.get("currentMapId")),
        last_updated=time.time(),
    )


def _parse_zone_collection(value: Any) -> tuple[DreameLawnMowerVectorZone, ...]:
    result: list[DreameLawnMowerVectorZone] = []
    for zone_id, zone_data in _parse_map_entries(value):
        if not isinstance(zone_data, Mapping):
            continue
        result.append(
            DreameLawnMowerVectorZone(
                zone_id=zone_id,
                points=_extract_path_coords(zone_data.get("path")),
                name=str(zone_data.get("name") or ""),
                zone_type=int(zone_data.get("type") or 0),
                shape_type=int(zone_data.get("shapeType") or 0),
                area=float(zone_data.get("area") or 0),
                time_minutes=int(zone_data.get("time") or 0),
                estimated_minutes=int(zone_data.get("etime") or 0),
            )
        )
    return tuple(result)


def _parse_path_collection(value: Any) -> tuple[DreameLawnMowerVectorPath, ...]:
    result: list[DreameLawnMowerVectorPath] = []
    for path_id, path_data in _parse_map_entries(value):
        if not isinstance(path_data, Mapping):
            continue
        result.append(
            DreameLawnMowerVectorPath(
                path_id=path_id,
                points=_extract_path_coords(path_data.get("path")),
                path_type=int(path_data.get("type") or 0),
            )
        )
    return tuple(result)


def _parse_contour_collection(
    value: Any,
) -> tuple[DreameLawnMowerVectorContour, ...]:
    result: list[DreameLawnMowerVectorContour] = []
    for raw_contour_id, contour_data in _parse_map_entries_with_mode(
        value,
        coerce_key=False,
    ):
        if not isinstance(contour_data, Mapping):
            continue
        contour_id = _extract_contour_id(raw_contour_id)
        if contour_id is None:
            continue
        result.append(
            DreameLawnMowerVectorContour(
                contour_id=contour_id,
                points=_extract_path_coords(contour_data.get("path")),
                contour_type=int(contour_data.get("type") or 0),
                shape_type=int(contour_data.get("shapeType") or 0),
            )
        )
    return tuple(result)


def _parse_point_collection(value: Any) -> tuple[tuple[int, int], ...]:
    points: list[tuple[int, int]] = []
    for _, point_data in _parse_map_entries(value):
        if isinstance(point_data, Mapping):
            point = _extract_single_point(point_data)
            if point is not None:
                points.append(point)
    return tuple(points)


def _parse_object_collection(value: Any) -> tuple[Mapping[str, Any], ...]:
    objects: list[Mapping[str, Any]] = []
    for _, entry in _parse_map_entries(value):
        if isinstance(entry, Mapping):
            objects.append(entry)
    return tuple(objects)


def _parse_map_entries(value: Any) -> tuple[tuple[int, Any], ...]:
    return _parse_map_entries_with_mode(value, coerce_key=True)


def _parse_map_entries_with_mode(
    value: Any,
    *,
    coerce_key: bool,
) -> tuple[tuple[Any, Any], ...]:
    if not isinstance(value, Mapping) or value.get("dataType") != "Map":
        return ()
    entries = value.get("value")
    if not isinstance(entries, Sequence) or isinstance(
        entries, (str, bytes, bytearray)
    ):
        return ()

    result: list[tuple[Any, Any]] = []
    for entry in entries:
        if (
            isinstance(entry, Sequence)
            and not isinstance(entry, (str, bytes, bytearray))
            and len(entry) >= 2
        ):
            key = entry[0]
            if coerce_key:
                try:
                    key = int(key)
                except (TypeError, ValueError):
                    continue
            result.append((key, entry[1]))
    return tuple(result)


def _extract_path_coords(value: Any) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()

    points: list[tuple[int, int]] = []
    for point in value:
        if isinstance(point, Mapping):
            x = _coerce_int(point.get("x"))
            y = _coerce_int(point.get("y"))
            if x is not None and y is not None:
                points.append((x, y))
    return tuple(points)


def _extract_single_point(value: Mapping[str, Any]) -> tuple[int, int] | None:
    x = _coerce_int(value.get("x"))
    y = _coerce_int(value.get("y"))
    if x is None or y is None:
        return None
    return x, y


def _parse_mow_paths(
    batch_data: Mapping[str, Any],
) -> tuple[DreameLawnMowerVectorMowPath, ...]:
    raw_path = _reassemble_batch_chunks(batch_data, "M_PATH")
    if not raw_path:
        return ()

    split_point = _coerce_int(batch_data.get("M_PATH.info"))
    if split_point is not None and 0 < split_point < len(raw_path):
        raw_path = raw_path[split_point:]

    if not raw_path.strip() or raw_path.strip() == "[]":
        return ()

    pairs = [
        (int(match.group(1)), int(match.group(2)))
        for match in re.finditer(r"\[(-?\d+),(-?\d+)\]", raw_path)
    ]
    if not pairs:
        return ()

    segments: list[tuple[tuple[int, int], ...]] = []
    current_segment: list[tuple[int, int]] = []
    for pair in pairs:
        if pair == _PATH_SENTINEL:
            if current_segment:
                segments.append(tuple(current_segment))
                current_segment = []
            continue
        current_segment.append((pair[0] * 10, pair[1] * 10))

    if current_segment:
        segments.append(tuple(current_segment))

    if not segments:
        return ()

    return (DreameLawnMowerVectorMowPath(zone_id=0, segments=tuple(segments)),)


def _extract_contour_id(value: Any) -> tuple[int, int] | None:
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
        if len(parts) != 2:
            return None
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return None

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) != 2:
            return None
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None

    return None


def _map_id_from_index(map_index: int) -> int:
    return map_index + 1


def _coerce_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return None


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
