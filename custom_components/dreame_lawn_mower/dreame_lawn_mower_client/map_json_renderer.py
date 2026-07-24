"""Legacy map-data JSON serialization with one focused implementation owner."""

from __future__ import annotations

import base64
import io
import json
import logging
import time
from functools import cmp_to_key
from io import BytesIO
from typing import Any

from PIL import (
    Image,
    PngImagePlugin,
)

from .const import (
    MAP_DATA_JSON_CLASS,
    MAP_DATA_JSON_PARAMETER_ACTIVE,
    MAP_DATA_JSON_PARAMETER_ACTIVE_ZONE,
    MAP_DATA_JSON_PARAMETER_AVG,
    MAP_DATA_JSON_PARAMETER_CHARGER_POSITION,
    MAP_DATA_JSON_PARAMETER_CLASS,
    MAP_DATA_JSON_PARAMETER_COMPRESSED_PIXELS,
    MAP_DATA_JSON_PARAMETER_DIMENSIONS,
    MAP_DATA_JSON_PARAMETER_ENTITIES,
    MAP_DATA_JSON_PARAMETER_FLOOR,
    MAP_DATA_JSON_PARAMETER_LAYERS,
    MAP_DATA_JSON_PARAMETER_MAX,
    MAP_DATA_JSON_PARAMETER_META_DATA,
    MAP_DATA_JSON_PARAMETER_MID,
    MAP_DATA_JSON_PARAMETER_MIN,
    MAP_DATA_JSON_PARAMETER_NAME,
    MAP_DATA_JSON_PARAMETER_NO_GO_AREA,
    MAP_DATA_JSON_PARAMETER_PATH,
    MAP_DATA_JSON_PARAMETER_PIXEL_COUNT,
    MAP_DATA_JSON_PARAMETER_PIXEL_SIZE,
    MAP_DATA_JSON_PARAMETER_PIXELS,
    MAP_DATA_JSON_PARAMETER_POINTS,
    MAP_DATA_JSON_PARAMETER_ROBOT_POSITION,
    MAP_DATA_JSON_PARAMETER_ROTATION,
    MAP_DATA_JSON_PARAMETER_SEGMENT,
    MAP_DATA_JSON_PARAMETER_SEGMENT_ID,
    MAP_DATA_JSON_PARAMETER_SIZE,
    MAP_DATA_JSON_PARAMETER_TYPE,
    MAP_DATA_JSON_PARAMETER_VERSION,
    MAP_DATA_JSON_PARAMETER_VIRTUAL_WALL,
    MAP_DATA_JSON_PARAMETER_WALL,
    MAP_DATA_JSON_PARAMETER_X,
    MAP_DATA_JSON_PARAMETER_Y,
    MAP_PARAMETER_ANGLE,
)
from .device_types import (
    PathType,
)
from .map_renderer_types import (
    MapRendererLayer,
)
from .map_types import (
    Area,
    MapData,
    MapPixelType,
)
from .resources import DEFAULT_MAP_DATA, DEFAULT_MAP_DATA_IMAGE

_LOGGER = logging.getLogger(__name__)


class DreameMowerMapDataJsonRenderer:
    HALF_INT16 = 32768
    HALF_INT16_UPPER_HALF = 32767
    MAX = round((HALF_INT16 + HALF_INT16_UPPER_HALF) / 10)

    def __init__(self) -> None:
        self._map_data: MapData = None
        self._map_data_json: dict[str, Any] = None
        self._left: int = 0
        self._top: int = 0
        self._grid_size: int = 0
        self.render_complete: bool = True
        self._layers: dict[MapRendererLayer, dict[str, Any]] = {}

        self._default_map_data: str = base64.b64decode(DEFAULT_MAP_DATA)
        self._default_map_image = Image.open(
            BytesIO(base64.b64decode(DEFAULT_MAP_DATA_IMAGE))
        ).convert("RGBA")

    @staticmethod
    def _coordinate_tuple_sort(a: list[int], b: list[int]) -> bool:
        xA = a[0]
        yA = a[1]
        xB = b[0]
        yB = b[1]

        if yB > yA:
            return -1
        if xB > xA:
            return 1
        return 0

    @staticmethod
    def _convert_coordinates(x: int, y: int) -> int:
        return [
            round((x + DreameMowerMapDataJsonRenderer.HALF_INT16) / 10),
            DreameMowerMapDataJsonRenderer.MAX
            - round((y + DreameMowerMapDataJsonRenderer.HALF_INT16) / 10),
        ]

    @staticmethod
    def _convert_angle(angle: int) -> int:
        return (((180 - angle) if (angle < 180) else (360 - angle + 180)) + 270) % 360

    @staticmethod
    def _to_buffer(image, extra_data: str) -> bytes:
        buffer = io.BytesIO()
        info = PngImagePlugin.PngInfo()
        info.add_text(MAP_DATA_JSON_CLASS, extra_data, zip=True)
        image.save(buffer, format="PNG", pnginfo=info)
        return buffer.getvalue()

    def render_map(
        self, map_data: MapData, robot_status: int = 0, station_status: int = 0
    ) -> bytes:
        if map_data is None or map_data.empty_map:
            return self.default_map_image

        if (
            self._map_data
            and self._map_data == map_data
            and self._map_data.frame_id == map_data.frame_id
            and self._map_data_json
        ):
            _LOGGER.debug("Skip render map data, not changed")
            return self._to_buffer(
                self._default_map_image,
                json.dumps(self._map_data_json, separators=(",", ":")),
            )

        now = time.time()
        self.render_complete = False
        if (
            self._map_data is None
            or self._map_data.dimensions != map_data.dimensions
            or self._map_data.map_id != map_data.map_id
            or self._map_data.saved_map_status != map_data.saved_map_status
        ):
            self._map_data = None
            self._left = round(
                (map_data.dimensions.left + DreameMowerMapDataJsonRenderer.HALF_INT16)
                / 10
            )
            self._top = round(
                (map_data.dimensions.top + DreameMowerMapDataJsonRenderer.HALF_INT16)
                / 10
            )
            self._grid_size = round(map_data.dimensions.grid_size / 10)

        map_data_json = {
            MAP_DATA_JSON_PARAMETER_CLASS: MAP_DATA_JSON_CLASS,
            MAP_DATA_JSON_PARAMETER_SIZE: {
                MAP_DATA_JSON_PARAMETER_X: DreameMowerMapDataJsonRenderer.MAX,
                MAP_DATA_JSON_PARAMETER_Y: DreameMowerMapDataJsonRenderer.MAX,
            },
            MAP_DATA_JSON_PARAMETER_PIXEL_SIZE: self._grid_size,
            MAP_DATA_JSON_PARAMETER_LAYERS: [],
            MAP_DATA_JSON_PARAMETER_ENTITIES: [],
            MAP_DATA_JSON_PARAMETER_META_DATA: {
                MAP_DATA_JSON_PARAMETER_VERSION: 2,
                MAP_DATA_JSON_PARAMETER_ROTATION: map_data.rotation,
            },
        }

        if map_data.robot_position:
            if (
                self._map_data is None
                or self._map_data.robot_position != map_data.robot_position
                or not self._layers.get(MapRendererLayer.ROBOT)
            ):
                self._layers[MapRendererLayer.ROBOT] = {
                    MAP_DATA_JSON_PARAMETER_TYPE: (
                        MAP_DATA_JSON_PARAMETER_ROBOT_POSITION
                    ),
                    MAP_DATA_JSON_PARAMETER_POINTS: (
                        DreameMowerMapDataJsonRenderer._convert_coordinates(
                            map_data.robot_position.x, map_data.robot_position.y
                        )
                    ),
                    MAP_DATA_JSON_PARAMETER_META_DATA: {
                        MAP_PARAMETER_ANGLE: (
                            DreameMowerMapDataJsonRenderer._convert_angle(
                                map_data.robot_position.a
                            )
                        )
                    },
                }
            map_data_json[MAP_DATA_JSON_PARAMETER_ENTITIES].append(
                self._layers[MapRendererLayer.ROBOT]
            )

        if map_data.charger_position:
            if (
                self._map_data is None
                or self._map_data.charger_position != map_data.charger_position
                or not self._layers.get(MapRendererLayer.CHARGER)
            ):
                self._layers[MapRendererLayer.CHARGER] = {
                    MAP_DATA_JSON_PARAMETER_TYPE: (
                        MAP_DATA_JSON_PARAMETER_CHARGER_POSITION
                    ),
                    MAP_DATA_JSON_PARAMETER_POINTS: (
                        DreameMowerMapDataJsonRenderer._convert_coordinates(
                            map_data.charger_position.x, map_data.charger_position.y
                        )
                    ),
                    MAP_DATA_JSON_PARAMETER_META_DATA: {
                        MAP_PARAMETER_ANGLE: (
                            DreameMowerMapDataJsonRenderer._convert_angle(
                                map_data.charger_position.a
                            )
                        )
                    },
                }
            map_data_json[MAP_DATA_JSON_PARAMETER_ENTITIES].append(
                self._layers[MapRendererLayer.CHARGER]
            )

        if map_data.no_go_areas:
            if (
                self._map_data is None
                or self._map_data.no_go_areas != map_data.no_go_areas
                or not self._layers.get(MapRendererLayer.NO_GO)
            ):
                self._layers[MapRendererLayer.NO_GO] = []
                for area in map_data.no_go_areas:
                    a = DreameMowerMapDataJsonRenderer._convert_coordinates(
                        area.x0, area.y0
                    )
                    b = DreameMowerMapDataJsonRenderer._convert_coordinates(
                        area.x1, area.y1
                    )
                    c = DreameMowerMapDataJsonRenderer._convert_coordinates(
                        area.x2, area.y2
                    )
                    d = DreameMowerMapDataJsonRenderer._convert_coordinates(
                        area.x3, area.y3
                    )

                    self._layers[MapRendererLayer.NO_GO].append(
                        {
                            MAP_DATA_JSON_PARAMETER_TYPE: (
                                MAP_DATA_JSON_PARAMETER_NO_GO_AREA
                            ),
                            MAP_DATA_JSON_PARAMETER_POINTS: [
                                a[0],
                                a[1],
                                b[0],
                                b[1],
                                c[0],
                                c[1],
                                d[0],
                                d[1],
                            ],
                        }
                    )
            map_data_json[MAP_DATA_JSON_PARAMETER_ENTITIES].extend(
                self._layers[MapRendererLayer.NO_GO]
            )

        if map_data.active_areas:
            if (
                self._map_data is None
                or self._map_data.active_areas != map_data.active_areas
                or not self._layers.get(MapRendererLayer.ACTIVE_AREA)
            ):
                self._layers[MapRendererLayer.ACTIVE_AREA] = []
                for area in map_data.active_areas:
                    a = DreameMowerMapDataJsonRenderer._convert_coordinates(
                        area.x0, area.y0
                    )
                    b = DreameMowerMapDataJsonRenderer._convert_coordinates(
                        area.x1, area.y1
                    )
                    c = DreameMowerMapDataJsonRenderer._convert_coordinates(
                        area.x2, area.y2
                    )
                    d = DreameMowerMapDataJsonRenderer._convert_coordinates(
                        area.x3, area.y3
                    )

                    self._layers[MapRendererLayer.ACTIVE_AREA].append(
                        {
                            MAP_DATA_JSON_PARAMETER_TYPE: (
                                MAP_DATA_JSON_PARAMETER_ACTIVE_ZONE
                            ),
                            MAP_DATA_JSON_PARAMETER_POINTS: [
                                a[0],
                                a[1],
                                b[0],
                                b[1],
                                c[0],
                                c[1],
                                d[0],
                                d[1],
                            ],
                        }
                    )
            map_data_json[MAP_DATA_JSON_PARAMETER_ENTITIES].extend(
                self._layers[MapRendererLayer.ACTIVE_AREA]
            )

        if map_data.active_points:
            if (
                self._map_data is None
                or self._map_data.active_points != map_data.active_points
                or not self._layers.get(MapRendererLayer.ACTIVE_POINT)
            ):
                self._layers[MapRendererLayer.ACTIVE_POINT] = []
                size = 15 * map_data.dimensions.grid_size
                for point in map_data.active_points:
                    area = Area(
                        point.x - size,
                        point.y - size,
                        point.x + size,
                        point.y - size,
                        point.x + size,
                        point.y + size,
                        point.x - size,
                        point.y + size,
                    )

                    a = DreameMowerMapDataJsonRenderer._convert_coordinates(
                        area.x0, area.y0
                    )
                    b = DreameMowerMapDataJsonRenderer._convert_coordinates(
                        area.x1, area.y1
                    )
                    c = DreameMowerMapDataJsonRenderer._convert_coordinates(
                        area.x2, area.y2
                    )
                    d = DreameMowerMapDataJsonRenderer._convert_coordinates(
                        area.x3, area.y3
                    )

                    self._layers[MapRendererLayer.ACTIVE_POINT].append(
                        {
                            MAP_DATA_JSON_PARAMETER_TYPE: (
                                MAP_DATA_JSON_PARAMETER_ACTIVE_ZONE
                            ),
                            MAP_DATA_JSON_PARAMETER_POINTS: [
                                a[0],
                                a[1],
                                b[0],
                                b[1],
                                c[0],
                                c[1],
                                d[0],
                                d[1],
                            ],
                        }
                    )
            map_data_json[MAP_DATA_JSON_PARAMETER_ENTITIES].extend(
                self._layers[MapRendererLayer.ACTIVE_POINT]
            )

        if map_data.virtual_walls:
            if (
                self._map_data is None
                or self._map_data.virtual_walls != map_data.virtual_walls
                or not self._layers.get(MapRendererLayer.WALL)
            ):
                self._layers[MapRendererLayer.WALL] = []
                for wall in map_data.virtual_walls:
                    a = DreameMowerMapDataJsonRenderer._convert_coordinates(
                        wall.x0, wall.y0
                    )
                    b = DreameMowerMapDataJsonRenderer._convert_coordinates(
                        wall.x1, wall.y1
                    )

                    self._layers[MapRendererLayer.WALL].append(
                        {
                            MAP_DATA_JSON_PARAMETER_TYPE: (
                                MAP_DATA_JSON_PARAMETER_VIRTUAL_WALL
                            ),
                            MAP_DATA_JSON_PARAMETER_POINTS: [a[0], a[1], b[0], b[1]],
                        }
                    )
            map_data_json[MAP_DATA_JSON_PARAMETER_ENTITIES].extend(
                self._layers[MapRendererLayer.WALL]
            )

        if map_data.path and (
            self._map_data is None
            or self._map_data.path is None
            or len(self._map_data.path) != len(map_data.path)
            or not self._layers.get(MapRendererLayer.PATH)
        ):
            points = []
            self._layers[MapRendererLayer.PATH] = []
            if map_data.path and len(map_data.path) > 1:
                s = map_data.path[0]
                for point in map_data.path[1:]:
                    if point.path_type == PathType.LINE:
                        point = point
                        a = DreameMowerMapDataJsonRenderer._convert_coordinates(
                            s.x, s.y
                        )
                        b = DreameMowerMapDataJsonRenderer._convert_coordinates(
                            point.x, point.y
                        )

                        points.extend([a[0], a[1], b[0], b[1]])
                    else:
                        self._layers[MapRendererLayer.PATH].append(
                            {
                                MAP_DATA_JSON_PARAMETER_TYPE: (
                                    MAP_DATA_JSON_PARAMETER_PATH
                                ),
                                MAP_DATA_JSON_PARAMETER_POINTS: points,
                            }
                        )
                        points = []
                    s = point
            self._layers[MapRendererLayer.PATH].append(
                {
                    MAP_DATA_JSON_PARAMETER_TYPE: MAP_DATA_JSON_PARAMETER_PATH,
                    MAP_DATA_JSON_PARAMETER_POINTS: points,
                }
            )
            map_data_json[MAP_DATA_JSON_PARAMETER_ENTITIES].extend(
                self._layers[MapRendererLayer.PATH]
            )

        floor_pixels = []
        wall_pixels = []
        segments = {}

        if (
            self._map_data is None
            or self._map_data.active_segments != map_data.active_segments
            or self._map_data.active_areas != map_data.active_areas
            or self._map_data.segments != map_data.segments
            or self._map_data.data != map_data.data
            or not self._layers.get(MapRendererLayer.IMAGE)
        ):
            self._layers[MapRendererLayer.IMAGE] = []
            for y in range(map_data.dimensions.height):
                for x in range(map_data.dimensions.width):
                    segment_id = int(map_data.pixel_type[x, y])
                    coords = [
                        (x + (self._left / self._grid_size)),
                        (y + (self._top / self._grid_size)),
                    ]

                    coords[1] = (
                        DreameMowerMapDataJsonRenderer.MAX / self._grid_size
                    ) - coords[1]

                    coords[0] = round(coords[0])
                    coords[1] = round(coords[1])

                    if segment_id == MapPixelType.WALL.value:
                        wall_pixels.append(coords)
                    elif (
                        segment_id == MapPixelType.FLOOR.value
                        or segment_id == MapPixelType.UNKNOWN.value
                    ):
                        floor_pixels.append(coords)
                    elif segment_id > 0 and segment_id < 61:
                        if (
                            map_data.active_segments
                            and segment_id not in map_data.active_segments
                        ):
                            floor_pixels.append(coords)
                        else:
                            if not map_data.segments:
                                segment_id = 1

                            if segment_id not in segments:
                                segments[segment_id] = []
                            segments[segment_id].append(coords)

            if floor_pixels:
                self._layers[MapRendererLayer.IMAGE].append(
                    {
                        MAP_DATA_JSON_PARAMETER_TYPE: MAP_DATA_JSON_PARAMETER_FLOOR,
                        MAP_DATA_JSON_PARAMETER_PIXELS: [
                            val
                            for sublist in sorted(
                                floor_pixels,
                                key=cmp_to_key(
                                    DreameMowerMapDataJsonRenderer._coordinate_tuple_sort
                                ),
                            )
                            for val in sublist
                        ],
                    }
                )

            if wall_pixels:
                self._layers[MapRendererLayer.IMAGE].append(
                    {
                        MAP_DATA_JSON_PARAMETER_TYPE: MAP_DATA_JSON_PARAMETER_WALL,
                        MAP_DATA_JSON_PARAMETER_PIXELS: [
                            val
                            for sublist in sorted(
                                wall_pixels,
                                key=cmp_to_key(
                                    DreameMowerMapDataJsonRenderer._coordinate_tuple_sort
                                ),
                            )
                            for val in sublist
                        ],
                    }
                )

            if segments:
                for k, v in segments.items():
                    name = None
                    if map_data.segments:
                        name = f"Room {k}"
                        if k in map_data.segments:
                            name = map_data.segments[k].name
                    self._layers[MapRendererLayer.IMAGE].append(
                        {
                            MAP_DATA_JSON_PARAMETER_TYPE: (
                                MAP_DATA_JSON_PARAMETER_SEGMENT
                            ),
                            MAP_DATA_JSON_PARAMETER_PIXELS: [
                                val
                                for sublist in sorted(
                                    v,
                                    key=cmp_to_key(
                                        DreameMowerMapDataJsonRenderer._coordinate_tuple_sort
                                    ),
                                )
                                for val in sublist
                            ],
                            MAP_DATA_JSON_PARAMETER_META_DATA: {
                                MAP_DATA_JSON_PARAMETER_SEGMENT_ID: k,
                                MAP_DATA_JSON_PARAMETER_ACTIVE: (
                                    True
                                    if map_data.active_segments
                                    and k in map_data.active_segments
                                    else False
                                ),
                                MAP_DATA_JSON_PARAMETER_NAME: name,
                            },
                        }
                    )

            for layers in self._layers[MapRendererLayer.IMAGE]:
                pixels = layers[MAP_DATA_JSON_PARAMETER_PIXELS]
                layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS] = {
                    MAP_DATA_JSON_PARAMETER_X: {
                        MAP_DATA_JSON_PARAMETER_MIN: 65535,
                        MAP_DATA_JSON_PARAMETER_MAX: -65535,
                        MAP_DATA_JSON_PARAMETER_MID: None,
                        MAP_DATA_JSON_PARAMETER_AVG: None,
                    },
                    MAP_DATA_JSON_PARAMETER_Y: {
                        MAP_DATA_JSON_PARAMETER_MIN: 65535,
                        MAP_DATA_JSON_PARAMETER_MAX: -65535,
                        MAP_DATA_JSON_PARAMETER_MID: None,
                        MAP_DATA_JSON_PARAMETER_AVG: None,
                    },
                    MAP_DATA_JSON_PARAMETER_PIXEL_COUNT: len(pixels) / 2,
                }

                sum_x = 0
                sum_y = 0
                for i in range(0, len(pixels), 2):
                    sum_x = sum_x + pixels[i]
                    sum_y = sum_y + pixels[i + 1]

                    if (
                        pixels[i]
                        < layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][
                            MAP_DATA_JSON_PARAMETER_X
                        ][MAP_DATA_JSON_PARAMETER_MIN]
                    ):
                        layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][
                            MAP_DATA_JSON_PARAMETER_X
                        ][MAP_DATA_JSON_PARAMETER_MIN] = pixels[i]

                    if (
                        pixels[i]
                        > layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][
                            MAP_DATA_JSON_PARAMETER_X
                        ][MAP_DATA_JSON_PARAMETER_MAX]
                    ):
                        layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][
                            MAP_DATA_JSON_PARAMETER_X
                        ][MAP_DATA_JSON_PARAMETER_MAX] = pixels[i]

                    if (
                        pixels[i + 1]
                        < layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][
                            MAP_DATA_JSON_PARAMETER_Y
                        ][MAP_DATA_JSON_PARAMETER_MIN]
                    ):
                        layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][
                            MAP_DATA_JSON_PARAMETER_Y
                        ][MAP_DATA_JSON_PARAMETER_MIN] = pixels[i + 1]

                    if (
                        pixels[i + 1]
                        > layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][
                            MAP_DATA_JSON_PARAMETER_Y
                        ][MAP_DATA_JSON_PARAMETER_MAX]
                    ):
                        layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][
                            MAP_DATA_JSON_PARAMETER_Y
                        ][MAP_DATA_JSON_PARAMETER_MAX] = pixels[i + 1]

                layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][MAP_DATA_JSON_PARAMETER_X][
                    MAP_DATA_JSON_PARAMETER_MID
                ] = round(
                    (
                        layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][
                            MAP_DATA_JSON_PARAMETER_X
                        ][MAP_DATA_JSON_PARAMETER_MAX]
                        + layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][
                            MAP_DATA_JSON_PARAMETER_X
                        ][MAP_DATA_JSON_PARAMETER_MIN]
                    )
                    / 2
                )
                layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][MAP_DATA_JSON_PARAMETER_Y][
                    MAP_DATA_JSON_PARAMETER_MID
                ] = round(
                    (
                        layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][
                            MAP_DATA_JSON_PARAMETER_Y
                        ][MAP_DATA_JSON_PARAMETER_MAX]
                        + layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][
                            MAP_DATA_JSON_PARAMETER_Y
                        ][MAP_DATA_JSON_PARAMETER_MIN]
                    )
                    / 2
                )

                if sum_x:
                    layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][
                        MAP_DATA_JSON_PARAMETER_X
                    ][MAP_DATA_JSON_PARAMETER_AVG] = round(sum_x / (len(pixels) / 2))
                if sum_y:
                    layers[MAP_DATA_JSON_PARAMETER_DIMENSIONS][
                        MAP_DATA_JSON_PARAMETER_Y
                    ][MAP_DATA_JSON_PARAMETER_AVG] = round(sum_y / (len(pixels) / 2))

                current_x_start = -65535
                current_y = -65535
                current_count = 0
                compressed_pixels = []

                for i in range(0, len(pixels), 2):
                    x = pixels[i]
                    y = pixels[i + 1]

                    if y != current_y or x > (current_x_start + current_count):
                        compressed_pixels.extend(
                            [current_x_start, current_y, current_count]
                        )
                        current_x_start = x
                        current_y = y
                        current_count = 1
                    elif x != current_x_start:
                        current_count = current_count + 1

                compressed_pixels.extend([current_x_start, current_y, current_count])
                layers[MAP_DATA_JSON_PARAMETER_COMPRESSED_PIXELS] = compressed_pixels[
                    3:
                ]
                layers[MAP_DATA_JSON_PARAMETER_PIXELS] = []

        map_data_json[MAP_DATA_JSON_PARAMETER_LAYERS].extend(
            self._layers[MapRendererLayer.IMAGE]
        )

        self._map_data = map_data
        self._map_data_json = map_data_json
        _LOGGER.debug(
            "Render Map Data: %s:%s took: %.2f",
            map_data.map_id,
            map_data.frame_id,
            time.time() - now,
        )
        self.render_complete = True
        return self._to_buffer(
            self._default_map_image,
            json.dumps(self._map_data_json, separators=(",", ":")),
        )

    def embed_map_data(self, image_png: bytes) -> bytes:
        """Embed the last rendered map metadata into a presentation PNG."""
        if self._map_data_json is None:
            raise ValueError("Map metadata must be rendered before it can be embedded.")
        with Image.open(BytesIO(image_png)) as image:
            image.load()
            return self._to_buffer(
                image.convert("RGBA"),
                json.dumps(self._map_data_json, separators=(",", ":")),
            )

    @property
    def default_map_image(self) -> bytes:
        return self._to_buffer(self._default_map_image, self._default_map_data)

    @property
    def disconnected_map_image(self) -> bytes:
        return self.default_map_image
