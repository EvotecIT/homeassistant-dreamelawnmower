"""Read-only mower vector-map parsing and rendering helpers."""

from __future__ import annotations

import json
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
    clean_points: tuple[tuple[int, int], ...] = field(default_factory=tuple)
    cruise_points: tuple[tuple[int, int], ...] = field(default_factory=tuple)
    obstacles: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    boundary: DreameLawnMowerVectorBoundary | None = None
    total_area: float = 0
    name: str = ""
    map_index: int = 0
    last_updated: float | None = None
    mow_paths: tuple[DreameLawnMowerVectorMowPath, ...] = field(default_factory=tuple)


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

    primary: DreameLawnMowerVectorMap | None = None
    for entry in entries:
        try:
            vector_map = _parse_vector_map_json(entry)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        if primary is None:
            primary = vector_map
        if vector_map.map_index == 0:
            primary = vector_map
            break

    if primary is None:
        return None

    primary.mow_paths = _parse_mow_paths(batch_data)
    return primary


def render_vector_map_png(vector_map: DreameLawnMowerVectorMap | None) -> bytes | None:
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
        clean_points=_parse_point_collection(data.get("cleanPoints")),
        cruise_points=_parse_point_collection(data.get("cruisePoints")),
        obstacles=_parse_object_collection(data.get("obstacles")),
        boundary=boundary,
        total_area=float(data.get("totalArea") or 0),
        name=str(data.get("name") or ""),
        map_index=int(data.get("mapIndex") or 0),
        last_updated=time.time(),
    )


def _parse_zone_collection(value: Any) -> tuple[DreameLawnMowerVectorZone, ...]:
    result: list[DreameLawnMowerVectorZone] = []
    for zone_id, zone_data in _parse_map_value_entries(value):
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
    for path_id, path_data in _parse_map_value_entries(value):
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


def _parse_point_collection(value: Any) -> tuple[tuple[int, int], ...]:
    points: list[tuple[int, int]] = []
    for _, point_data in _parse_map_value_entries(value):
        if isinstance(point_data, Mapping):
            point = _extract_single_point(point_data)
            if point is not None:
                points.append(point)
    return tuple(points)


def _parse_object_collection(value: Any) -> tuple[Mapping[str, Any], ...]:
    objects: list[Mapping[str, Any]] = []
    for _, entry in _parse_map_value_entries(value):
        if isinstance(entry, Mapping):
            objects.append(entry)
    return tuple(objects)


def _parse_map_value_entries(value: Any) -> tuple[tuple[int, Any], ...]:
    if not isinstance(value, Mapping) or value.get("dataType") != "Map":
        return ()
    entries = value.get("value")
    if not isinstance(entries, Sequence) or isinstance(
        entries, (str, bytes, bytearray)
    ):
        return ()

    result: list[tuple[int, Any]] = []
    for entry in entries:
        if (
            isinstance(entry, Sequence)
            and not isinstance(entry, (str, bytes, bytearray))
            and len(entry) >= 2
        ):
            try:
                key = int(entry[0])
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
