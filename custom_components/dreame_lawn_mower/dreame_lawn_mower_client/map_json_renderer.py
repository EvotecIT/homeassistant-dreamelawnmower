"""Legacy map-data JSON serialization with one focused implementation owner."""

from __future__ import annotations

import base64
import io
import json
import logging
import time
from typing import NotRequired, TypedDict

import numpy as np
from numpy.typing import NDArray
from PIL import Image, PngImagePlugin

from .const import MAP_DATA_JSON_CLASS
from .device_types import PathType
from .map_types import Area, MapData, MapImageDimensions, MapPixelType, Point
from .resources import DEFAULT_MAP_DATA, DEFAULT_MAP_DATA_IMAGE

_LOGGER = logging.getLogger(__name__)


class _Entity(TypedDict):
    type: str
    points: list[int]
    metaData: NotRequired[dict[str, float]]


class _AxisDimensions(TypedDict):
    min: int
    max: int
    mid: int
    avg: int


class _LayerDimensions(TypedDict):
    x: _AxisDimensions
    y: _AxisDimensions
    pixelCount: float


class _SegmentMetadata(TypedDict):
    segmentId: int
    active: bool
    name: str | None


class _RasterLayer(TypedDict):
    type: str
    pixels: list[int]
    dimensions: _LayerDimensions
    compressedPixels: list[int]
    metaData: NotRequired[_SegmentMetadata]


class _Size(TypedDict):
    x: int
    y: int


class _MapMetadata(TypedDict):
    version: int
    rotation: int | None


_MapJson = TypedDict(
    "_MapJson",
    {
        "__class": str,
        "size": _Size,
        "pixelSize": int,
        "layers": list[_RasterLayer],
        "entities": list[_Entity],
        "metaData": _MapMetadata,
    },
)
_RasterKey = tuple[
    float,
    float,
    int,
    int,
    int,
    bytes,
    tuple[int, ...],
    tuple[tuple[int, str | None], ...],
]


class DreameMowerMapDataJsonRenderer:
    HALF_INT16 = 32768
    HALF_INT16_UPPER_HALF = 32767
    MAX = round((HALF_INT16 + HALF_INT16_UPPER_HALF) / 10)

    def __init__(self) -> None:
        self._map_data_json: str | None = None
        self._map_image: bytes | None = None
        self._raster_key: _RasterKey | None = None
        self._raster_layers: list[_RasterLayer] = []
        self.render_complete = True
        self._default_map_data = base64.b64decode(DEFAULT_MAP_DATA).decode("utf-8")
        with Image.open(io.BytesIO(base64.b64decode(DEFAULT_MAP_DATA_IMAGE))) as image:
            self._default_map_image = image.convert("RGBA")

    @staticmethod
    def _convert_coordinates(x: float, y: float) -> list[int]:
        return [
            round((x + DreameMowerMapDataJsonRenderer.HALF_INT16) / 10),
            DreameMowerMapDataJsonRenderer.MAX
            - round((y + DreameMowerMapDataJsonRenderer.HALF_INT16) / 10),
        ]

    @staticmethod
    def _convert_angle(angle: float) -> float:
        return (((180 - angle) if (angle < 180) else (360 - angle + 180)) + 270) % 360

    @staticmethod
    def _angle_metadata(angle: float | None) -> dict[str, float]:
        """Keep an unknown heading absent instead of inventing an orientation."""
        return (
            {"angle": DreameMowerMapDataJsonRenderer._convert_angle(angle)}
            if angle is not None
            else {}
        )

    @staticmethod
    def _to_buffer(image: Image.Image, extra_data: str) -> bytes:
        buffer = io.BytesIO()
        info = PngImagePlugin.PngInfo()
        info.add_text(MAP_DATA_JSON_CLASS, extra_data, zip=True)
        image.save(buffer, format="PNG", pnginfo=info)
        return buffer.getvalue()

    @classmethod
    def _area_entity(cls, area: Area, kind: str) -> _Entity:
        return {
            "type": kind,
            "points": [
                *cls._convert_coordinates(area.x0, area.y0),
                *cls._convert_coordinates(area.x1, area.y1),
                *cls._convert_coordinates(area.x2, area.y2),
                *cls._convert_coordinates(area.x3, area.y3),
            ],
        }

    @classmethod
    def _position_entity(cls, point: Point, kind: str) -> _Entity:
        return {
            "type": kind,
            "points": cls._convert_coordinates(point.x, point.y),
            "metaData": cls._angle_metadata(point.a),
        }

    @classmethod
    def _entities(cls, map_data: MapData, grid_size: int) -> list[_Entity]:
        entities: list[_Entity] = []
        if map_data.robot_position is not None:
            entities.append(
                cls._position_entity(map_data.robot_position, "robot_position")
            )
        if map_data.charger_position is not None:
            entities.append(
                cls._position_entity(map_data.charger_position, "charger_location")
            )
        for area in map_data.no_go_areas or ():
            entities.append(cls._area_entity(area, "no_go_area"))
        for area in map_data.active_areas or ():
            entities.append(cls._area_entity(area, "active_zone"))
        size = 15 * grid_size
        for point in map_data.active_points or ():
            entities.append(
                cls._area_entity(
                    Area(
                        point.x - size,
                        point.y - size,
                        point.x + size,
                        point.y - size,
                        point.x + size,
                        point.y + size,
                        point.x - size,
                        point.y + size,
                    ),
                    "active_zone",
                )
            )
        for wall in map_data.virtual_walls or ():
            entities.append(
                {
                    "type": "virtual_wall",
                    "points": [
                        *cls._convert_coordinates(wall.x0, wall.y0),
                        *cls._convert_coordinates(wall.x1, wall.y1),
                    ],
                }
            )
        if map_data.path:
            points: list[int] = []
            previous = map_data.path[0]
            for point in map_data.path[1:]:
                if point.path_type == PathType.LINE:
                    points.extend(cls._convert_coordinates(previous.x, previous.y))
                    points.extend(cls._convert_coordinates(point.x, point.y))
                else:
                    if points:
                        entities.append({"type": "path", "points": points})
                    points = []
                previous = point
            if points:
                entities.append({"type": "path", "points": points})
        return entities

    @staticmethod
    def _axis_dimensions(values: list[int]) -> _AxisDimensions:
        minimum, maximum = min(values), max(values)
        return {
            "min": minimum,
            "max": maximum,
            "mid": round((minimum + maximum) / 2),
            "avg": round(sum(values) / len(values)),
        }

    @classmethod
    def _raster_layer(cls, kind: str, pixels: list[tuple[int, int]]) -> _RasterLayer:
        """Encode sorted horizontal runs, retaining gaps and numeric zero averages."""
        ordered = sorted(pixels, key=lambda point: (point[1], point[0]))
        runs: list[int] = []
        start_x, row = ordered[0]
        count = 1
        for x, y in ordered[1:]:
            if y == row and x == start_x + count:
                count += 1
            else:
                runs.extend((start_x, row, count))
                start_x, row, count = x, y, 1
        runs.extend((start_x, row, count))
        return {
            "type": kind,
            "pixels": [],
            "compressedPixels": runs,
            "dimensions": {
                "x": cls._axis_dimensions([point[0] for point in pixels]),
                "y": cls._axis_dimensions([point[1] for point in pixels]),
                "pixelCount": float(len(pixels)),
            },
        }

    @classmethod
    def _render_raster(
        cls,
        map_data: MapData,
        dimensions: MapImageDimensions,
        pixel_type: NDArray[np.uint8],
        grid_size: int,
    ) -> list[_RasterLayer]:
        left = round((dimensions.left + cls.HALF_INT16) / 10)
        top = round((dimensions.top + cls.HALF_INT16) / 10)
        floor: list[tuple[int, int]] = []
        walls: list[tuple[int, int]] = []
        segments: dict[int, list[tuple[int, int]]] = {}
        for y in range(dimensions.height):
            for x in range(dimensions.width):
                segment_id = int(pixel_type[x, y])
                coordinate = (
                    round(x + left / grid_size),
                    round(cls.MAX / grid_size - (y + top / grid_size)),
                )
                if segment_id == MapPixelType.WALL:
                    walls.append(coordinate)
                elif segment_id in (MapPixelType.FLOOR, MapPixelType.UNKNOWN):
                    floor.append(coordinate)
                elif 0 < segment_id < 61:
                    if (
                        map_data.active_segments
                        and segment_id not in map_data.active_segments
                    ):
                        floor.append(coordinate)
                    else:
                        if not map_data.segments:
                            segment_id = 1
                        segments.setdefault(segment_id, []).append(coordinate)
        layers: list[_RasterLayer] = []
        if floor:
            layers.append(cls._raster_layer("floor", floor))
        if walls:
            layers.append(cls._raster_layer("wall", walls))
        for segment_id, pixels in segments.items():
            name = None
            if map_data.segments:
                segment = map_data.segments.get(segment_id)
                name = segment.name if segment is not None else f"Room {segment_id}"
            layer = cls._raster_layer("segment", pixels)
            layer["metaData"] = {
                "segmentId": segment_id,
                "active": bool(
                    map_data.active_segments and segment_id in map_data.active_segments
                ),
                "name": name,
            }
            layers.append(layer)
        return layers

    def _use_default_map(self) -> bytes:
        image_png = self.default_map_image
        self._map_data_json, self._map_image = self._default_map_data, image_png
        self._raster_key, self._raster_layers = None, []
        return image_png

    def render_map(
        self,
        map_data: MapData | None,
        robot_status: int = 0,
        station_status: int = 0,
    ) -> bytes:
        now = time.monotonic()
        self.render_complete = False
        try:
            if map_data is None or map_data.empty_map:
                return self._use_default_map()
            dimensions, pixels = map_data.dimensions, map_data.pixel_type
            if dimensions is None or pixels is None:
                return self._use_default_map()
            grid_size = round(dimensions.grid_size / 10)
            if (
                grid_size <= 0
                or dimensions.width <= 0
                or dimensions.height <= 0
                or pixels.ndim != 2
                or pixels.shape != (dimensions.width, dimensions.height)
            ):
                return self._use_default_map()
            # MapData is mutable. Cache exported content instead of its identity.
            raster_key: _RasterKey = (
                dimensions.left,
                dimensions.top,
                dimensions.width,
                dimensions.height,
                dimensions.grid_size,
                pixels.tobytes(),
                tuple(sorted(map_data.active_segments or ())),
                tuple(
                    sorted(
                        (key, segment.name)
                        for key, segment in (map_data.segments or {}).items()
                    )
                ),
            )
            layers = (
                self._raster_layers
                if raster_key == self._raster_key
                else self._render_raster(
                    map_data,
                    dimensions,
                    pixels,
                    grid_size,
                )
            )
            metadata: _MapJson = {
                "__class": MAP_DATA_JSON_CLASS,
                "size": {"x": self.MAX, "y": self.MAX},
                "pixelSize": grid_size,
                "layers": layers,
                "entities": self._entities(map_data, dimensions.grid_size),
                "metaData": {"version": 2, "rotation": map_data.rotation},
            }
            encoded = json.dumps(metadata, separators=(",", ":"))
            if encoded == self._map_data_json and self._map_image is not None:
                return self._map_image
            image_png = self._to_buffer(self._default_map_image, encoded)
            # Publish cache state only after JSON and PNG encoding both succeed.
            self._raster_key, self._raster_layers = raster_key, layers
            self._map_data_json, self._map_image = encoded, image_png
            _LOGGER.debug(
                "Render Map Data: %s:%s took: %.2f",
                map_data.map_id,
                map_data.frame_id,
                time.monotonic() - now,
            )
            return image_png
        finally:
            self.render_complete = True

    def embed_map_data(self, image_png: bytes) -> bytes:
        """Embed the last successfully rendered map metadata into a presentation PNG."""
        if self._map_data_json is None:
            raise ValueError("Map metadata must be rendered before it can be embedded.")
        with Image.open(io.BytesIO(image_png)) as image:
            image.load()
            return self._to_buffer(image.convert("RGBA"), self._map_data_json)

    @property
    def default_map_image(self) -> bytes:
        return self._to_buffer(self._default_map_image, self._default_map_data)

    @property
    def disconnected_map_image(self) -> bytes:
        return self.default_map_image
