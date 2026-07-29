"""Legacy PIL map rendering and resource composition."""

from __future__ import annotations
import io
import math
import time
import base64
import json
import zlib
import re
import logging
import traceback
import copy
import numpy as np
import hashlib
import textwrap
from datetime import datetime
from py_mini_racer import MiniRacer
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from Crypto.Util.Padding import unpad
from PIL import (
    Image,
    ImageDraw,
    ImageOps,
    ImageFont,
    ImageEnhance,
    PngImagePlugin,
    ImageFilter,
)
from typing import Any, Mapping
from time import sleep
from io import BytesIO
from typing import Optional, Tuple
from functools import cmp_to_key
from threading import Timer
from .resources import (
    DEFAULT_MAP_IMAGE,
    FURNITURE_TYPE_TO_ICON,
    FURNITURE_TYPE_TO_IMAGE,
    FURNITURE_V2_TYPE_TO_ICON,
    FURNITURE_V2_TYPE_TO_IMAGE,
    MAP_CHARGER_IMAGE_DREAME,
    MAP_CHARGER_IMAGE_MATERIAL,
    MAP_CHARGER_IMAGE_MIJIA,
    MAP_CHARGER_VSLAM_IMAGE_DREAME,
    MAP_FONT,
    MAP_FONT_LIGHT,
    MAP_ICON_CLEANING_MODE_DREAME,
    MAP_ICON_CLEANING_MODE_MATERIAL,
    MAP_ICON_CLEANING_MODE_MIJIA,
    MAP_ICON_CLEANING_ROUTE_DREAME,
    MAP_ICON_CLEANING_ROUTE_MATERIAL,
    MAP_ICON_CRUISE_POINT_BG_DREAME,
    MAP_ICON_CRUISE_POINT_DREAME,
    MAP_ICON_DELETE,
    MAP_ICON_MOVE,
    MAP_ICON_OBSTACLE_BG_DREAME,
    MAP_ICON_OBSTACLE_HIDDEN_BG_DREAME,
    MAP_ICON_PROBLEM,
    MAP_ICON_REPEATS_DREAME,
    MAP_ICON_REPEATS_MATERIAL,
    MAP_ICON_REPEATS_MIJIA,
    MAP_ICON_RESIZE,
    MAP_ICON_ROTATE,
    MAP_ICON_SELECTED_SEGMENT,
    MAP_ROBOT_CHARGING_IMAGE,
    MAP_ROBOT_CLEANING_DIRECTION_IMAGE,
    MAP_ROBOT_CLEANING_IMAGE,
    MAP_ROBOT_LIDAR_IMAGE_DREAME_DARK,
    MAP_ROBOT_LIDAR_IMAGE_DREAME_LIGHT,
    MAP_ROBOT_LIDAR_IMAGE_MIJIA,
    MAP_ROBOT_OBSTACLE_BOTTOM_LEFT_IMAGE,
    MAP_ROBOT_OBSTACLE_BOTTOM_RIGHT_IMAGE,
    MAP_ROBOT_OBSTACLE_TOP_LEFT_IMAGE,
    MAP_ROBOT_OBSTACLE_TOP_RIGHT_IMAGE,
    MAP_ROBOT_SLEEPING_IMAGE,
    MAP_ROBOT_VSLAM_IMAGE_DREAME_DARK,
    MAP_ROBOT_VSLAM_IMAGE_DREAME_LIGHT,
    MAP_ROBOT_VSLAM_IMAGE_MIJIA,
    MAP_ROBOT_WARNING_IMAGE,
    MAP_WIFI_IMAGE_DREAME,
    OBSTACLE_TYPE_TO_HIDDEN_ICON,
    OBSTACLE_TYPE_TO_ICON,
    SEGMENT_ICONS_DREAME,
    SEGMENT_ICONS_DREAME_OLD,
    SEGMENT_ICONS_MATERIAL,
    SEGMENT_ICONS_MIJIA,
)
from .protocol import DreameMowerProtocol
from .exceptions import DeviceUpdateFailedException
from .map_decoder import DreameMowerMapDecoder
from .map_json_renderer import DreameMowerMapDataJsonRenderer
from .map_optimizer import DreameMowerMapOptimizer
from .device_types import (
    PIID,
    DIID,
    DreameMowerProperty,
    DreameMowerAction,
    DreameMowerActionMapping,
    DreameMowerDeviceCapability,
    RobotType,
    CleansetType,
    ObstacleType,
    FurnitureType,
    PathType,
    ObstacleIgnoreStatus,
    SEGMENT_TYPE_CODE_TO_NAME,
    SEGMENT_TYPE_CODE_TO_HA_ICON,
    FURNITURE_TYPE_TO_DIMENSIONS,
    FURNITURE_V2_TYPE_TO_DIMENSIONS,
)
from .map_renderer_types import (
    ALine,
    MAP_COLOR_SCHEME_LIST,
    MAP_ICON_SET_LIST,
    Angle,
    CLine,
    MapRendererColorScheme,
    MapRendererConfig,
    MapRendererData,
    MapRendererLayer,
    MapRendererResources,
    Paths,
)
from .map_types import (
    Area,
    CleanupMethod,
    Coordinate,
    Furniture,
    MapData,
    MapDataPartial,
    MapFrameType,
    MapImageDimensions,
    MapPixelType,
    Obstacle,
    Path,
    Point,
    RecoveryMapInfo,
    RecoveryMapType,
    Segment,
    StartupMethod,
    TaskEndType,
    Wall,
)
from .const import (
    MAP_PARAMETER_NAME,
    MAP_PARAMETER_VALUE,
    MAP_PARAMETER_TIME,
    MAP_PARAMETER_CODE,
    MAP_PARAMETER_OUT,
    MAP_PARAMETER_MAP,
    MAP_PARAMETER_ANGLE,
    MAP_PARAMETER_MAPSTR,
    MAP_PARAMETER_CURR_ID,
    MAP_PARAMETER_MOWER,
    MAP_PARAMETER_EXPIRES_TIME,
    MAP_PARAMETER_URL,
    MAP_REQUEST_PARAMETER_MAP_ID,
    MAP_REQUEST_PARAMETER_FRAME_ID,
    MAP_REQUEST_PARAMETER_FRAME_TYPE,
    MAP_REQUEST_PARAMETER_REQ_TYPE,
    MAP_REQUEST_PARAMETER_FORCE_TYPE,
    MAP_REQUEST_PARAMETER_TYPE,
    MAP_REQUEST_PARAMETER_INDEX,
    MAP_REQUEST_PARAMETER_ZONE_ID,
    MAP_DATA_JSON_CLASS,
    MAP_DATA_JSON_PARAMETER_CLASS,
    MAP_DATA_JSON_PARAMETER_SIZE,
    MAP_DATA_JSON_PARAMETER_X,
    MAP_DATA_JSON_PARAMETER_Y,
    MAP_DATA_JSON_PARAMETER_PIXEL_SIZE,
    MAP_DATA_JSON_PARAMETER_LAYERS,
    MAP_DATA_JSON_PARAMETER_ENTITIES,
    MAP_DATA_JSON_PARAMETER_META_DATA,
    MAP_DATA_JSON_PARAMETER_VERSION,
    MAP_DATA_JSON_PARAMETER_ROTATION,
    MAP_DATA_JSON_PARAMETER_TYPE,
    MAP_DATA_JSON_PARAMETER_POINTS,
    MAP_DATA_JSON_PARAMETER_PIXELS,
    MAP_DATA_JSON_PARAMETER_SEGMENT_ID,
    MAP_DATA_JSON_PARAMETER_ACTIVE,
    MAP_DATA_JSON_PARAMETER_NAME,
    MAP_DATA_JSON_PARAMETER_DIMENSIONS,
    MAP_DATA_JSON_PARAMETER_MIN,
    MAP_DATA_JSON_PARAMETER_MAX,
    MAP_DATA_JSON_PARAMETER_MID,
    MAP_DATA_JSON_PARAMETER_AVG,
    MAP_DATA_JSON_PARAMETER_PIXEL_COUNT,
    MAP_DATA_JSON_PARAMETER_COMPRESSED_PIXELS,
    MAP_DATA_JSON_PARAMETER_ROBOT_POSITION,
    MAP_DATA_JSON_PARAMETER_CHARGER_POSITION,
    MAP_DATA_JSON_PARAMETER_NO_GO_AREA,
    MAP_DATA_JSON_PARAMETER_ACTIVE_ZONE,
    MAP_DATA_JSON_PARAMETER_VIRTUAL_WALL,
    MAP_DATA_JSON_PARAMETER_PATH,
    MAP_DATA_JSON_PARAMETER_FLOOR,
    MAP_DATA_JSON_PARAMETER_WALL,
    MAP_DATA_JSON_PARAMETER_SEGMENT,
)

_LOGGER = logging.getLogger(__name__)


class DreameMowerMapRenderer:
    def __init__(
        self,
        color_scheme: str = None,
        icon_set: str = None,
        map_objects: list[str] = None,
        robot_type: int = 0,
        low_resolution: bool = False,
        square: bool = False,
        cache: bool = True,
    ) -> None:
        self.color_scheme: MapRendererColorScheme = MAP_COLOR_SCHEME_LIST.get(color_scheme, MapRendererColorScheme())
        self.icon_set: int = MAP_ICON_SET_LIST.get(icon_set, 0)
        self.config: MapRendererConfig = MapRendererConfig()
        if map_objects is not None:
            for attr in self.config.__dict__.keys():
                if attr not in map_objects:
                    setattr(self.config, attr, False)

        self._map_data: MapData = None
        self.render_complete: bool = True
        self._layers: dict[MapRendererLayer, Any] = {}
        self._robot_status: int = None
        self._station_status: int = None
        self._robot_type: int = robot_type
        self._low_resolution: bool = low_resolution
        self._low_memory: bool = low_resolution
        self.presentation_stroke_scale: float = 1.0
        self.presentation_marker_scale: float = 1.0
        self.presentation_label_scale: float = 1.0
        self.presentation_marker_image: Image.Image | None = None
        self._square: bool = square
        self._cache: bool = cache
        self._has_mask: bool = False
        self._calibration_points: dict[str, int] = None
        self._default_calibration_points: dict[str, int] = [
            {
                MAP_PARAMETER_MOWER: {
                    MAP_DATA_JSON_PARAMETER_X: 0,
                    MAP_DATA_JSON_PARAMETER_Y: 0,
                },
                MAP_PARAMETER_MAP: {
                    MAP_DATA_JSON_PARAMETER_X: 0,
                    MAP_DATA_JSON_PARAMETER_Y: 0,
                },
            },
            {
                MAP_PARAMETER_MOWER: {
                    MAP_DATA_JSON_PARAMETER_X: 1000,
                    MAP_DATA_JSON_PARAMETER_Y: 0,
                },
                MAP_PARAMETER_MAP: {
                    MAP_DATA_JSON_PARAMETER_X: 0,
                    MAP_DATA_JSON_PARAMETER_Y: 0,
                },
            },
            {
                MAP_PARAMETER_MOWER: {
                    MAP_DATA_JSON_PARAMETER_X: 0,
                    MAP_DATA_JSON_PARAMETER_Y: 1000,
                },
                MAP_PARAMETER_MAP: {
                    MAP_DATA_JSON_PARAMETER_X: 0,
                    MAP_DATA_JSON_PARAMETER_Y: 0,
                },
            },
        ]

        self._image = None
        self._charger_icon = None
        self._robot_icon = None
        self._robot_charging_icon = None
        self._robot_cleaning_icon = None
        self._robot_warning_icon = None
        self._robot_sleeping_icon = None
        self._robot_emptying_icon = None
        self._robot_cleaning_direction_icon = None
        self._obstacle_background = None
        self._obstacle_hidden_background = None
        self._cruise_path_point_background = None
        self._cruise_point_background = None
        self._furniture_background = None
        self._wifi_icon = None
        self._font_file = None
        self._light_font_file = None
        self._default_map_image = None
        self._obstacle_bottom_left_icon = None
        self._obstacle_top_left_icon = None
        self._obstacle_bottom_right_icon = None
        self._obstacle_top_right_icon = None
        self._map_problem_icon = None

        self._segment_icons = {}
        self._obstacle_icons = {}
        self._obstacle_hidden_icons = {}
        self._furniture_icons = {}
        self._furniture_images = {}

        if self._low_memory:
            self.config.obstacle = False
            self.config.pet = False
            self.config.furniture = False

        if self.icon_set == 2:
            repeats = MAP_ICON_REPEATS_MIJIA
            cleaning_mode = MAP_ICON_CLEANING_MODE_MIJIA
        elif self.icon_set == 3:
            repeats = MAP_ICON_REPEATS_MATERIAL
            cleaning_mode = MAP_ICON_CLEANING_MODE_MATERIAL
        else:
            repeats = MAP_ICON_REPEATS_DREAME
            cleaning_mode = MAP_ICON_CLEANING_MODE_DREAME

        if self.config.cleaning_times:
            self._cleaning_times_icon = [
                Image.open(BytesIO(base64.b64decode(icon))).convert("RGBA") for icon in repeats
            ]
        if self.config.cleaning_mode:
            self._cleaning_mode_icon = [
                Image.open(BytesIO(base64.b64decode(icon))).convert("RGBA") for icon in cleaning_mode
            ]

    @staticmethod
    def _to_buffer(image) -> bytes:
        if image:
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()

    @staticmethod
    def _set_icon_color(image, size, color):
        ico = image.resize((int(size), int(size)))
        pixdata = ico.load()
        for yy in range(ico.size[1]):
            for xx in range(ico.size[0]):
                if (
                    pixdata[xx, yy][0] > 80
                    and pixdata[xx, yy][1] > 80
                    and pixdata[xx, yy][2] > 80
                    and pixdata[xx, yy][3] > 80
                ):
                    pixdata[xx, yy] = color

        return ico

    @staticmethod
    def _calculate_bounds(dimensions, segments) -> list[int]:
        if segments:
            min_x = dimensions.width - 1
            min_y = dimensions.height - 1
            max_x = 0
            max_y = 0
            for segment in segments.values():
                p = segment.to_coord(dimensions, False)
                x_coords = [int(p.x0), int(p.x1)]
                y_coords = [int(p.y0), int(p.y1)]
                min_x = min(min(x_coords), min_x)
                max_x = max(max(x_coords), max_x)
                min_y = min(min(y_coords), min_y)
                max_y = max(max(y_coords), max_y)

            return [min_x, min_y, max_x, max_y]

    @staticmethod
    def _calculate_padding(
        dimensions,
        active_areas,
        no_go_areas,
        walls,
        pathways,
        furnitures,
        furniture_version,
        segments,
        padding,
        min_width,
        min_height,
        scale,
    ) -> list[int]:
        min_x = 0
        min_y = 0
        max_x = dimensions.width
        max_y = dimensions.height

        if segments:
            for segment in segments.values():
                p = segment.to_coord(dimensions, False)
                x_coords = sorted([int(p.x0), int(p.x1)])
                y_coords = sorted([int(p.y0), int(p.y1)])
                min_x = min(x_coords[0], min_x)
                max_x = max(x_coords[1], max_x)
                min_y = min(y_coords[0], min_y)
                max_y = max(y_coords[1], max_y)

        if active_areas:
            for area in active_areas:
                p = area.to_coord(dimensions)
                x_coords = [p.x0, p.x1, p.x2, p.x3]
                y_coords = [p.y0, p.y1, p.y2, p.y3]
                min_x = min(min(x_coords), min_x)
                max_x = max(max(x_coords), max_x)
                min_y = min(min(y_coords), min_y)
                max_y = max(max(y_coords), max_y)

        if no_go_areas:
            for area in no_go_areas:
                p = area.to_coord(dimensions)
                x_coords = [p.x0, p.x1, p.x2, p.x3]
                y_coords = [p.y0, p.y1, p.y2, p.y3]
                min_x = min(min(x_coords), min_x)
                max_x = max(max(x_coords), max_x)
                min_y = min(min(y_coords), min_y)
                max_y = max(max(y_coords), max_y)

        if walls:
            for wall in walls:
                p = wall.to_coord(dimensions)
                x_coords = [p.x0, p.x1]
                y_coords = [p.y0, p.y1]
                min_x = min(min(x_coords), min_x)
                max_x = max(max(x_coords), max_x)
                min_y = min(min(y_coords), min_y)
                max_y = max(max(y_coords), max_y)

        if pathways:
            for line in pathways:
                p = line.to_coord(dimensions)
                x_coords = [p.x0, p.x1]
                y_coords = [p.y0, p.y1]
                min_x = min(min(x_coords), min_x)
                max_x = max(max(x_coords), max_x)
                min_y = min(min(y_coords), min_y)
                max_y = max(max(y_coords), max_y)

        if furnitures:
            for k, v in furnitures.items():
                p = Point(v.x, v.y).to_coord(dimensions)
                w = 0
                h = 0
                if v.width and v.height:
                    if v.type.value not in (
                        FURNITURE_V2_TYPE_TO_IMAGE if furniture_version == 2 else FURNITURE_TYPE_TO_IMAGE
                    ):
                        continue
                    w = int((v.width / dimensions.grid_size) / 2)
                    h = int((v.height / dimensions.grid_size) / 2)
                elif v.type.value not in (
                    FURNITURE_V2_TYPE_TO_ICON if furniture_version == 2 else FURNITURE_TYPE_TO_ICON
                ):
                    continue
                min_x = min(p.x - w, min_x)
                max_x = max(p.x + w, max_x)
                min_y = min(p.y - h, min_y)
                max_y = max(p.y + h, max_y)

        if min_x < 0:
            padding[0] = padding[0] + int(-min_x)
        if max_x > dimensions.width:
            padding[2] = padding[2] + int(max_x - dimensions.width)
        if min_y < 0:
            padding[1] = padding[1] + int(-min_y)
        if max_y > dimensions.height:
            padding[3] = padding[3] + int(max_y - dimensions.height)

        if dimensions.width + padding[0] + padding[2] < min_width:
            size = int((min_width - dimensions.width + padding[0] + padding[2]) / 2)
            padding[0] = padding[0] + size
            padding[2] = padding[2] + size

        if dimensions.height + padding[1] + padding[3] < min_height:
            size = int((min_height - dimensions.height + padding[1] + padding[3]) / 2)
            padding[1] = padding[1] + size
            padding[3] = padding[3] + size

        for k in range(4):
            padding[k] = padding[k] * scale

        return padding

    @staticmethod
    def _calculate_calibration_points(map_data: MapData) -> dict[str, int] | None:
        if (map_data.dimensions.width * map_data.dimensions.height) > 0:
            calibration_points = []
            for point in [Point(0, 0), Point(1000, 0), Point(0, 1000)]:
                img_point = point.to_img(map_data.dimensions).rotated(map_data.dimensions, map_data.rotation)
                calibration_points.append(
                    {
                        MAP_PARAMETER_MOWER: {
                            MAP_DATA_JSON_PARAMETER_X: point.x,
                            MAP_DATA_JSON_PARAMETER_Y: point.y,
                        },
                        MAP_PARAMETER_MAP: {
                            MAP_DATA_JSON_PARAMETER_X: int(img_point.x),
                            MAP_DATA_JSON_PARAMETER_Y: int(img_point.y),
                        },
                    }
                )
            return calibration_points

    @staticmethod
    def _alpha_composite(source, destination):
        srcA = source[3] / 255.0
        dstA = destination[3] / 255.0
        outA = srcA + dstA * (1 - srcA)
        if outA:
            outRGB = []
            for i in range(3):
                outRGB.append((float(source[i]) * srcA + float(destination[i]) * dstA * (1 - srcA)) / outA)
            return (int(outRGB[0]), int(outRGB[1]), int(outRGB[2]), int(outA * 255))
        return source

    def _combine_layers(self, cached_layers, layer_size, parent, sub):
        cached_layers[parent] = Image.new("RGBA", layer_size, (255, 255, 255, 0))
        if sub in cached_layers:
            for k, v in sorted(cached_layers[sub].items()):
                if v is not None:
                    cached_layers[parent] = Image.alpha_composite(cached_layers[parent], v)

    def get_data_string(
        self,
        map_data: MapData,
        resources: MapRendererResources = None,
        robot_status: int = 0,
        station_status: int = 0,
    ) -> str:
        if not map_data or map_data.empty_map or (map_data.dimensions.width * map_data.dimensions.height) < 2:
            return (
                json.dumps(
                    {"resources": resources},
                    separators=(",", ":"),
                )
                if resources
                else "{}"
            )
        now = time.time()

        pixels = {}
        min_x = map_data.dimensions.width - 1
        min_y = map_data.dimensions.height - 1
        max_x = 0
        max_y = 0
        for y in range(map_data.dimensions.height):
            for x in range(map_data.dimensions.width):
                px_type = int(map_data.pixel_type[x, y])
                if px_type:
                    # if map_data.segments and map_data.saved_map and px_type == 255:
                    #    pixel = map_data.data[(map_data.dimensions.width * y) + x]
                    #    if pixel > 0:
                    #        px_type = px_type + (pixel & 0x3F)

                    if px_type in pixels:
                        pixels[px_type].extend([x, y])
                    else:
                        pixels[px_type] = [x, y]
                    max_x = max(x, max_x)
                    min_x = min(x, min_x)
                    max_y = max(y, max_y)
                    min_y = min(y, min_y)

        crop = [0, 0, 0, 0]

        if not map_data.saved_map:
            map_data.dimensions.bounds = DreameMowerMapRenderer._calculate_bounds(
                map_data.dimensions, map_data.segments
            )

        if map_data.dimensions.bounds:
            min_x = max(min(map_data.dimensions.bounds[0], min_x), min_x)
            max_x = min(max(map_data.dimensions.bounds[2], max_x), max_x)
            min_y = max(min(map_data.dimensions.bounds[1], min_y), min_y)
            max_y = min(max(map_data.dimensions.bounds[3], max_y), max_y)

        if (
            min_x != (map_data.dimensions.width - 1)
            and min_y != (map_data.dimensions.height - 1)
            and max_x != 0
            and max_y != 0
        ) and (
            min_x != 0
            or min_y != 0
            or max_x != (map_data.dimensions.width - 1)
            or max_y != (map_data.dimensions.height - 1)
        ):
            crop = [
                min_x,
                (map_data.dimensions.height - (max_y + 1)),
                (map_data.dimensions.width - (max_x + 1)),
                min_y,
            ]

        for layer in pixels.keys():
            current_x_start = -1
            current_y = -1
            current_count = 0
            compressed_pixels = []
            coords = pixels[layer]
            for i in range(0, len(coords), 2):
                x = coords[i]
                y = coords[i + 1]
                if y != current_y or x > (current_x_start + current_count):
                    compressed_pixels.extend([current_x_start, current_y, current_count])
                    current_x_start = x
                    current_y = y
                    current_count = 1
                elif x != current_x_start:
                    current_count = current_count + 1
            compressed_pixels.extend([current_x_start, current_y, current_count])
            pixels[layer] = compressed_pixels[3:]

        path_types = {"S": 1, "W": 2, "M": 3}
        paths = None
        if map_data.path:
            paths = []
            coords = [
                path_types.get(map_data.path[0].path_type),
                map_data.path[0].x,
                map_data.path[0].y,
            ]
            for path in map_data.path[1:]:
                if path.path_type.value != "L":
                    paths.append(coords)
                    coords = [path_types.get(path.path_type)]
                coords.extend([path.x, path.y])

            if len(coords) > 2:
                paths.append(coords)

        map_data_json = MapRendererData(
            data=pixels,
            size=[
                map_data.dimensions.left,
                map_data.dimensions.top,
                map_data.dimensions.width if not map_data.empty_map else 1,
                map_data.dimensions.height if not map_data.empty_map else 1,
                map_data.dimensions.grid_size,
                map_data.rotation,
                crop,
            ],
            frame_id=map_data.frame_id,
            active_segments=map_data.active_segments,
            cleanset=bool(map_data.cleanset) if not map_data.saved_map and not map_data.wifi_map else False,
            docked=map_data.docked,
            floor_material=map_data.floor_material,
            hidden_segments=map_data.hidden_segments,
            neglected_segments=map_data.neglected_segments,
            robot_status=robot_status if not map_data.saved_map and not map_data.wifi_map else 0,
            station_status=station_status if not map_data.saved_map and not map_data.wifi_map else 0,
            saved_map=map_data.saved_map,
            wifi_map=map_data.wifi_map,
            history_map=map_data.history_map,
            recovery_map=map_data.recovery_map,
            path=paths if not map_data.saved_map and not map_data.wifi_map else [],
            robot_position=(
                [
                    map_data.robot_position.x,
                    map_data.robot_position.y,
                    map_data.robot_position.a,
                ]
                if map_data.robot_position
                else None
            ),
            charger_position=(
                [
                    map_data.charger_position.x,
                    map_data.charger_position.y,
                    map_data.charger_position.a,
                ]
                if map_data.charger_position
                else None
            ),
            router_position=(
                [
                    map_data.router_position.x,
                    map_data.router_position.y,
                ]
                if map_data.router_position
                else None
            ),
            # ai_outborders_user=map_data.ai_outborders_user,
            # ai_outborders=map_data.ai_outborders,
            # ai_outborders_new=map_data.ai_outborders_new,
            # ai_outborders_2d=map_data.ai_outborders_2d,
            # ai_furniture_warning=map_data.ai_furniture_warning,
            # walls_info=map_data.walls_info,
            # walls_info_new=map_data.walls_info_new,
            startup_method=map_data.startup_method.name.lower() if map_data.startup_method is not None else None,
            cleanup_method=map_data.cleanup_method.name.lower() if map_data.cleanup_method is not None else None,
            second_cleaning=map_data.second_cleaning,
            multiple_cleaning_time=map_data.multiple_cleaning_time,
            dos=map_data.dos,
            cleaned_area=map_data.cleaned_area,
            cleaning_time=map_data.cleaning_time,
            work_status=map_data.work_status,
            completed=map_data.completed,
            remaining_battery=map_data.remaining_battery,
            segments=(
                [
                    [
                        k,
                        v.x,
                        v.y,
                        v.type,
                        base64.b64encode(v.custom_name.encode("utf-8")).decode("utf-8") if v.custom_name else None,
                        v.index,
                        v.color_index,
                        v.order,
                        v.cleaning_times,
                        v.cleaning_mode if v.cleanset_type != CleansetType.DEFAULT else None,
                        v.floor_material,
                        v.floor_material_direction,
                        v.visibility,
                        [v.x0, v.y0, v.x1, v.y1],
                    ]
                    for (k, v) in map_data.segments.items()
                ]
                if map_data.segments
                else None
            ),
            active_areas=(
                [
                    [
                        area.x0,
                        area.y0,
                        area.x1,
                        area.y1,
                        area.x2,
                        area.y2,
                        area.x3,
                        area.y3,
                    ]
                    for area in map_data.active_areas
                ]
                if map_data.active_areas
                else []
            ),
            active_points=[[point.x0, point.y0] for point in map_data.active_points] if map_data.active_points else [],
            active_cruise_points=(
                [[point.x, point.y, point.type, point.completed] for point in map_data.active_cruise_points.values()]
                if map_data.active_cruise_points
                else []
            ),
            task_cruise_points=bool(map_data.task_cruise_points),
            virtual_walls=(
                [
                    [virtual_wall.x0, virtual_wall.y0, virtual_wall.x1, virtual_wall.y1]
                    for virtual_wall in map_data.virtual_walls
                ]
                if map_data.virtual_walls
                else []
            ),
            no_go=(
                [
                    [
                        area.x0,
                        area.y0,
                        area.x1,
                        area.y1,
                        area.x2,
                        area.y2,
                        area.x3,
                        area.y3,
                    ]
                    for area in map_data.no_go_areas
                ]
                if map_data.no_go_areas
                else []
            ),
            obstacles=(
                [
                    [
                        v.x,
                        v.y,
                        v.type.value,
                        v.possibility,
                        v.ignore_status,
                        v.picture_status,
                        v.id,
                        v.pos_x,
                        v.pos_y,
                        v.width,
                        v.height,
                        v.segment,
                    ]
                    for k, v in map_data.obstacles.items()
                ]
                if map_data.obstacles
                else []
            ),
            predefined_points=(
                [[point.x0, point.y0] for point in map_data.predefined_points]
                if map_data.predefined_points is not None
                else None
            ),
            pathways=(
                [[wall.x0, wall.y0, wall.x1, wall.y1] for wall in map_data.pathways]
                if map_data.pathways is not None
                else None
            ),
            furnitures=(
                [
                    [
                        area.x0,
                        area.y0,
                        area.x1,
                        area.y1,
                        area.x2,
                        area.y2,
                        area.x3,
                        area.y3,
                        area.x,
                        area.y,
                        area.width,
                        area.height,
                        area.type.value,
                        area.size_type,
                        area.angle,
                        area.scale,
                    ]
                    for key, area in map_data.furnitures.items()
                ]
                if map_data.furnitures is not None
                else None
            ),
            furniture_version=map_data.furniture_version,
            resources=resources,
        )

        map_data_json = json.dumps(
            map_data_json,
            default=lambda o: dict((key, value) for key, value in o.__dict__.items() if value is not None),
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        _LOGGER.debug(
            "Convert Map Data: %s:%s took: %.2f",
            map_data.map_id,
            map_data.frame_id,
            time.time() - now,
        )
        return map_data_json

    def render_obstacle_image(
        self,
        image_bytes,
        obstacle: Obstacle,
        ai_image_crop: bool,
        render_box: bool = True,
        crop_image: bool = False,
    ):
        if image_bytes:
            if not obstacle or not (
                obstacle.width and obstacle.height and obstacle.pos_x != None and obstacle.pos_y != None
            ):
                return image_bytes

            image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
            w = image.size[0]
            h = image.size[1]
            crop = (int((h * 105) / 100.0) - h) * 2
            x0_offset = 0
            x1_offset = 0
            if ai_image_crop:
                if crop_image:
                    image = image.crop((crop, 0, image.size[0] - crop, image.size[1] - int(crop / 2)))
                    w = image.size[0]
                    h = image.size[1]
                else:
                    x0_offset = crop
                    w = w - (crop * 2)
                    h = h - int(crop / 2)
            else:
                crop = int(round(crop * 0.55))
                if crop_image:
                    image = image.crop((crop, 0, image.size[0] - crop, image.size[1]))
                    w = image.size[0]
                    h = image.size[1]
                else:
                    x0_offset = crop
                    w = w - (crop * 2)

            if render_box:
                if self._obstacle_bottom_left_icon is None:
                    self._obstacle_bottom_left_icon = Image.open(
                        BytesIO(base64.b64decode(MAP_ROBOT_OBSTACLE_BOTTOM_LEFT_IMAGE))
                    ).convert("RGBA")
                    self._obstacle_top_left_icon = Image.open(
                        BytesIO(base64.b64decode(MAP_ROBOT_OBSTACLE_TOP_LEFT_IMAGE))
                    ).convert("RGBA")
                    self._obstacle_bottom_right_icon = Image.open(
                        BytesIO(base64.b64decode(MAP_ROBOT_OBSTACLE_BOTTOM_RIGHT_IMAGE))
                    ).convert("RGBA")
                    self._obstacle_top_right_icon = Image.open(
                        BytesIO(base64.b64decode(MAP_ROBOT_OBSTACLE_TOP_RIGHT_IMAGE))
                    ).convert("RGBA")

                icon_size = int(round(5 * h / 100.0))
                obstacle_bottom_left_icon = self._obstacle_bottom_left_icon.resize((icon_size, icon_size))
                obstacle_top_left_icon = self._obstacle_top_left_icon.resize((icon_size, icon_size))
                obstacle_bottom_right_icon = self._obstacle_bottom_right_icon.resize((icon_size, icon_size))
                obstacle_top_right_icon = self._obstacle_top_right_icon.resize((icon_size, icon_size))

                x = obstacle.pos_x - 4
                y = obstacle.pos_y - 4
                width = obstacle.width + 8
                height = obstacle.height + 8

                stroke = 3
                offset = 6
                x0 = ((x * w) / 100.0) - stroke + x0_offset
                y0 = ((y * h) / 100.0) - stroke
                x1 = (x0 + ((width * w) / 100.0)) + stroke + x1_offset
                y1 = (y0 + ((height * h) / 100.0)) + stroke

                if x0 <= 0:
                    new_x = int(w * 0.5 / 100.0)
                    x1 = x1 + new_x - x0
                    x0 = new_x
                if y0 <= 0:
                    new_y = int(h * 0.5 / 100.0)
                    y1 = y1 + new_y - y0
                    y0 = new_y

                if x1 >= w:
                    x1 = w - int(w * 0.5 / 100.0)
                if y1 >= h:
                    x1 = h - int(h * 0.5 / 100.0)

                new_layer = Image.new("RGBA", image.size, (255, 255, 255, 0))
                draw = ImageDraw.Draw(new_layer, "RGBA")
                draw.polygon(
                    [
                        int(round(x0)),
                        int(round(y0)),
                        int(round(x0)),
                        int(round(y1)),
                        int(round(x1)),
                        int(round(y1)),
                        int(round(x1)),
                        int(round(y0)),
                    ],
                    (49, 85, 225, 30),
                    (49, 85, 225, 255),
                    width=stroke,
                )
                image = Image.alpha_composite(image, new_layer)

                new_layer = Image.new("RGBA", image.size, (255, 255, 255, 0))
                draw = ImageDraw.Draw(new_layer, "RGBA")
                new_layer.paste(
                    obstacle_top_left_icon,
                    (int(round(x0 + offset)), int(round(y0 + offset))),
                )
                new_layer.paste(
                    obstacle_bottom_left_icon,
                    (
                        int(round(x0 + offset)),
                        int(round(y1 - obstacle_bottom_left_icon.size[1] - offset)),
                    ),
                )
                new_layer.paste(
                    obstacle_bottom_right_icon,
                    (
                        int(round(x1 - obstacle_top_right_icon.size[0] - offset)),
                        int(round(y1 - obstacle_bottom_right_icon.size[1] - offset)),
                    ),
                )
                new_layer.paste(
                    obstacle_top_right_icon,
                    (
                        int(round(x1 - obstacle_top_right_icon.size[0] - offset)),
                        int(round(y0 + offset)),
                    ),
                )
                image = Image.alpha_composite(image, new_layer)

            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG")
            return buffer.getvalue()

    def render_map(
        self,
        map_data: MapData,
        robot_status: int = 0,
        station_status: int = 0,
        info_text: bool = False,
    ) -> bytes:
        if map_data is None or map_data.empty_map or (map_data.dimensions.width * map_data.dimensions.height) < 2:
            return self.default_map_image

        self.render_complete = False
        now = time.time()

        if map_data.saved_map:
            robot_status = 0
            station_status = 0
        try:
            if self._cache:
                if (
                    self._map_data is None
                    or self._map_data.dimensions != map_data.dimensions
                    or self._map_data.map_id != map_data.map_id
                    or self._map_data.saved_map_status != map_data.saved_map_status
                ):
                    self._map_data = None

                if (
                    self._map_data
                    and self._map_data == map_data
                    and self._robot_status == robot_status
                    and self._station_status == station_status
                    and self._map_data.segments == map_data.segments
                    and self._map_data.frame_id == map_data.frame_id
                    and self._image
                ):
                    self.render_complete = True
                    _LOGGER.info("Skip render frame, map data not changed")
                    return self._to_buffer(self._image)

            scale = (
                2
                if self._low_resolution
                else (
                    4
                    if (map_data.saved_map_status == 2 or map_data.restored_map)
                    and not map_data.recovery_map
                    and not map_data.history_map
                    else 2 if (map_data.wifi_map or map_data.history_map) and self._cache else 3
                )
            )
            object_scale = 2

            render_material = False
            if (map_data.saved_map_status == 2 or map_data.saved_map) and not map_data.wifi_map:
                render_material = self.config.material and map_data.floor_material

            if scale == 3 and (render_material):
                scale = 2 if info_text else 4

            if not map_data.saved_map:
                if (
                    self._map_data is None
                    or self._map_data.segments != map_data.segments
                    or self._map_data.dimensions != map_data.dimensions
                    or self._map_data.saved_map_id != map_data.saved_map_id
                ):
                    map_data.dimensions.bounds = DreameMowerMapRenderer._calculate_bounds(
                        map_data.dimensions, map_data.segments
                    )

                    if self._map_data and (
                        self._map_data.dimensions.bounds != map_data.dimensions.bounds
                        or self._map_data.saved_map_id != map_data.saved_map_id
                    ):
                        self._map_data = None
                else:
                    map_data.dimensions.bounds = self._map_data.dimensions.bounds

            if (
                not self._cache
                or self._map_data is None
                or self._map_data.active_areas != map_data.active_areas
                or self._map_data.no_go_areas != map_data.no_go_areas
                or self._map_data.virtual_walls != map_data.virtual_walls
                or self._map_data.pathways != map_data.pathways
                or self._map_data.segments != map_data.segments
                or self._map_data.dimensions != map_data.dimensions
                or self._map_data.restored_map != map_data.restored_map
            ):
                map_data.dimensions.padding = DreameMowerMapRenderer._calculate_padding(
                    map_data.dimensions,
                    map_data.active_areas if self.config.active_area else None,
                    map_data.no_go_areas if self.config.no_go else None,
                    map_data.virtual_walls if self.config.virtual_wall else None,
                    map_data.pathways if self.config.pathway else None,
                    map_data.furnitures if self.config.furniture else None,
                    map_data.furniture_version,
                    map_data.segments,
                    [14, 14, 14, 14],
                    120,
                    80,
                    scale,
                )

                if self._cache and self._map_data and self._map_data.dimensions.padding != map_data.dimensions.padding:
                    self._map_data = None
            else:
                map_data.dimensions.padding = self._map_data.dimensions.padding

            map_data.dimensions.scale = scale
            segment_mask = None

            if not self._low_memory and self.config.path and map_data.path and self._robot_type != RobotType.VSLAM:
                if not self._cache or self._map_data is None or self._map_data.path != map_data.path:
                    self._has_mask = False
            else:
                self._has_mask = False

            cached_layers = self._layers if self._cache else {}
            if self._cache and not self._has_mask and cached_layers.get(MapRendererLayer.PATH_MASK):
                del cached_layers[MapRendererLayer.PATH_MASK]

            if self._cache and self._map_data and self._map_data.dimensions.scale != scale:
                self._map_data = None

            if not self._cache or (self._map_data is None or self._map_data.rotation != map_data.rotation):
                self._robot_sleeping_icon = None
                self._obstacle_background = None
                self._obstacle_hidden_background = None
                self._cruise_path_point_background = None
                self._cruise_point_background = None
                self._furniture_background = None

                if self._map_data is None:
                    self._robot_charging_icon = None
                    self._robot_cleaning_icon = None
                    self._robot_warning_icon = None
                    self._robot_cleaning_direction_icon = None

            bg_color = (
                (0, 0, 0, 255)
                if self.color_scheme.dark or self.color_scheme.invert
                else (
                    (255, 255, 255, 255)
                    if info_text
                    else (255, 255, 255, 0) if map_data.wifi_map else self.color_scheme.outside
                )
            )
            if (
                not self._cache
                or self._map_data is None
                or not cached_layers.get(MapRendererLayer.IMAGE)
                or self._map_data.active_segments != map_data.active_segments
                or self._map_data.active_areas != map_data.active_areas
                or self._map_data.segments != map_data.segments
                or self._map_data.data != map_data.data
                or (self._has_mask and not cached_layers.get(MapRendererLayer.PATH_MASK))
                or (render_material and self._map_data.floor_material != map_data.floor_material)
            ):
                area_colors = {}
                # as implemented on the app
                if map_data.cleaning_map:
                    area_colors[MapPixelType.OUTSIDE.value] = bg_color
                    area_colors[MapPixelType.WALL.value] = self.color_scheme.wall
                    if map_data.second_cleaning:
                        area_colors[MapPixelType.DIRTY_AREA.value] = self.color_scheme.second_clean_area
                        area_colors[MapPixelType.CLEAN_AREA.value] = self.color_scheme.cleaned_area
                    else:
                        area_colors[MapPixelType.DIRTY_AREA.value] = self.color_scheme.dirty_area
                        area_colors[MapPixelType.CLEAN_AREA.value] = self.color_scheme.clean_area
                    area_colors[MapPixelType.NEW_SEGMENT.value] = self.color_scheme.passive_segment
                elif map_data.wifi_map:
                    area_colors[MapPixelType.OUTSIDE.value] = bg_color
                    area_colors[MapPixelType.WIFI_EXCELLENT.value] = (
                        129,
                        168,
                        245,
                        255,
                    )
                    area_colors[MapPixelType.WIFI_HIGH.value] = (161, 189, 242, 255)
                    area_colors[MapPixelType.WIFI_LOW.value] = (205, 218, 239, 255)
                    area_colors[MapPixelType.WIFI_POOR.value] = (217, 226, 239, 255)
                    area_colors[MapPixelType.WIFI_UNREACHED.value] = (
                        229,
                        234,
                        238,
                        255,
                    )
                    area_colors[MapPixelType.WIFI_WALL.value] = (160, 160, 160, 255)
                    area_colors[MapPixelType.NEW_SEGMENT.value] = area_colors[MapPixelType.OUTSIDE.value]
                else:
                    area_colors[MapPixelType.OUTSIDE.value] = bg_color
                    area_colors[MapPixelType.WALL.value] = self.color_scheme.wall
                    area_colors[MapPixelType.HIDDEN_WALL.value] = self.color_scheme.hidden_segment
                    area_colors[MapPixelType.FLOOR.value] = self.color_scheme.floor
                    area_colors[MapPixelType.NEW_SEGMENT.value] = self.color_scheme.new_segment
                    area_colors[MapPixelType.UNKNOWN.value] = self.color_scheme.floor
                    area_colors[MapPixelType.OBSTACLE_WALL.value] = self.color_scheme.wall
                    area_colors[MapPixelType.NEW_SEGMENT_UNKNOWN.value] = self.color_scheme.new_segment

                if map_data.cleaning_map:
                    if map_data.neglected_segments:
                        for k in map_data.neglected_segments.keys():
                            area_colors[k] = (255, 255, 255, 255)
                elif map_data.segments is not None and not map_data.cleaning_map:
                    for k, v in map_data.segments.items():
                        if self.config.color:
                            if map_data.hidden_segments and k in map_data.hidden_segments:
                                area_colors[k] = self.color_scheme.hidden_segment
                            elif map_data.active_segments and k not in map_data.active_segments:
                                area_colors[k] = self.color_scheme.passive_segment
                            elif v.color_index is not None:
                                area_colors[k] = self.color_scheme.segment[v.color_index][0]
                        else:
                            area_colors[k] = area_colors[MapPixelType.FLOOR.value]

                pixels = np.full(
                    (
                        map_data.dimensions.height,
                        map_data.dimensions.width,
                        4,
                    ),
                    area_colors[MapPixelType.OUTSIDE.value],
                    dtype=np.uint8,
                )

                if self._has_mask:
                    mask_color = (255, 255, 255, 255)
                    mask = np.full(
                        (
                            map_data.dimensions.height,
                            map_data.dimensions.width,
                            4,
                        ),
                        (255, 255, 255, 0),
                        dtype=np.uint8,
                    )

                if map_data.history_map and map_data.neglected_segments:
                    segment_mask = np.full(
                        (
                            map_data.dimensions.height,
                            map_data.dimensions.width,
                            4,
                        ),
                        (255, 255, 255, 0),
                        dtype=np.uint8,
                    )

                min_x = map_data.dimensions.width - 1
                min_y = map_data.dimensions.height - 1
                max_x = 0
                max_y = 0

                for y in range(map_data.dimensions.height):
                    for x in range(map_data.dimensions.width):
                        px_type = int(map_data.pixel_type[x, map_data.dimensions.height - y - 1])

                        if px_type != 0:
                            pixels[y, x] = area_colors.get(px_type, area_colors[253])

                            max_x = max(x, max_x)
                            min_x = min(x, min_x)
                            max_y = max(y, max_y)
                            min_y = min(y, min_y)

                            if self._has_mask and px_type != 255:
                                mask[y, x] = mask_color

                            if segment_mask is not None:
                                if px_type in map_data.neglected_segments:
                                    segment_mask[y, x] = self.color_scheme.neglected_segment

                if render_material:
                    floor_scale = 2
                    pixels = pixels.repeat(floor_scale, axis=0).repeat(floor_scale, axis=1)
                    if render_material:
                        floor_material = self.render_floor_material(
                            pixels,
                            map_data.floor_material,
                            map_data.pixel_type,
                            self.color_scheme.material_color,
                            map_data.dimensions,
                            floor_scale,
                        )
                        if floor_material is not None:
                            pixels = floor_material
                            _LOGGER.debug("Render MATERIAL")

                    if scale != floor_scale:
                        pixels = pixels.repeat(scale / floor_scale, axis=0).repeat(scale / floor_scale, axis=1)
                else:
                    pixels = pixels.repeat(scale, axis=0).repeat(scale, axis=1)

                if self._has_mask:
                    mask = mask.repeat(scale, axis=0).repeat(scale, axis=1)

                if segment_mask is not None:
                    segment_mask = segment_mask.repeat(scale, axis=0).repeat(scale, axis=1)

                if map_data.dimensions.bounds:
                    # min_x = max(0, min(map_data.dimensions.bounds[0], min_x))
                    # max_x = min((map_data.dimensions.width - 1), max(map_data.dimensions.bounds[2], max_x))
                    # min_y = max(0, min(map_data.dimensions.bounds[1], min_y))
                    # max_y = min((map_data.dimensions.height - 1), max(map_data.dimensions.bounds[3], max_y))
                    min_x = max(min(map_data.dimensions.bounds[0], min_x), min_x)
                    max_x = min(max(map_data.dimensions.bounds[2], max_x), max_x)
                    min_y = max(min(map_data.dimensions.bounds[1], min_y), min_y)
                    max_y = min(max(map_data.dimensions.bounds[3], max_y), max_y)

                if (
                    min_x != (map_data.dimensions.width - 1)
                    and min_y != (map_data.dimensions.height - 1)
                    and max_x != 0
                    and max_y != 0
                ) and (
                    min_x != 0
                    or min_y != 0
                    or max_x != (map_data.dimensions.width - 1)
                    or max_y != (map_data.dimensions.height - 1)
                ):
                    from_y = min_y * scale
                    to_y = (max_y + 1) * scale
                    from_x = min_x * scale
                    to_x = (max_x + 1) * scale
                    pixels = pixels[from_y:to_y, from_x:to_x]
                    if self._has_mask:
                        mask = mask[from_y:to_y, from_x:to_x]
                    if segment_mask is not None:
                        segment_mask = segment_mask[from_y:to_y, from_x:to_x]
                    map_data.dimensions.crop = [
                        from_x,
                        from_y,
                        (map_data.dimensions.width - (max_x + 1)) * scale,
                        (map_data.dimensions.height - (max_y + 1)) * scale,
                    ]

                if self._map_data and self._map_data.dimensions.crop != map_data.dimensions.crop:
                    self._map_data = None

                image = Image.fromarray(pixels)
                if self._square and not map_data.wifi_map:  # and not map_data.saved_map:
                    height = image.size[0] + map_data.dimensions.padding[0] + map_data.dimensions.padding[2]
                    width = image.size[1] + map_data.dimensions.padding[1] + map_data.dimensions.padding[3]
                    if height != width:
                        dif = int(abs(height - width) / 2)
                        if height < width:
                            map_data.dimensions.padding[0] = map_data.dimensions.padding[0] + dif
                            map_data.dimensions.padding[2] = map_data.dimensions.padding[2] + dif
                        else:
                            map_data.dimensions.padding[1] = map_data.dimensions.padding[1] + dif
                            map_data.dimensions.padding[3] = map_data.dimensions.padding[3] + dif

                cached_layers[MapRendererLayer.IMAGE] = ImageOps.expand(
                    Image.fromarray(pixels),
                    border=tuple(map_data.dimensions.padding),
                    fill=bg_color,
                )

                if self._has_mask:
                    if self._cache and self._map_data:
                        self._map_data.path = None

                    cached_layers[MapRendererLayer.PATH_MASK] = ImageOps.expand(
                        Image.fromarray(mask.repeat(object_scale, axis=0).repeat(object_scale, axis=1)),
                        border=(
                            map_data.dimensions.padding[0] * object_scale,
                            map_data.dimensions.padding[1] * object_scale,
                            map_data.dimensions.padding[2] * object_scale,
                            map_data.dimensions.padding[3] * object_scale,
                        ),
                        fill=(255, 255, 255, 0),
                    )

                if segment_mask is not None:
                    segment_mask = ImageOps.expand(
                        Image.fromarray(segment_mask),
                        border=(
                            map_data.dimensions.padding[0],
                            map_data.dimensions.padding[1],
                            map_data.dimensions.padding[2],
                            map_data.dimensions.padding[3],
                        ),
                        fill=(255, 255, 255, 0),
                    )
            else:
                map_data.dimensions.crop = self._map_data.dimensions.crop

            self._calibration_points = self._calculate_calibration_points(map_data)

            image = cached_layers[MapRendererLayer.IMAGE]

            if not map_data.saved_map and map_data.path and self.config.path:
                if (
                    not self._cache
                    or self._map_data is None
                    or self._map_data.path != map_data.path
                    or not cached_layers.get(MapRendererLayer.PATH)
                ):
                    cached_layers[MapRendererLayer.PATH] = self.render_path(
                        map_data.path,
                        self.color_scheme.path,
                        (
                            int(image.size[0] * object_scale),
                            int(image.size[1] * object_scale),
                        ),
                        cached_layers.get(MapRendererLayer.PATH_MASK),
                        map_data.dimensions,
                        0.375 * scale * object_scale,
                        object_scale,
                    )
                    cached_layers[MapRendererLayer.PATH].thumbnail(image.size, Image.Resampling.BOX, reducing_gap=1.5)
                    _LOGGER.debug("Render PATH")
                image = Image.alpha_composite(image, cached_layers[MapRendererLayer.PATH])
            elif self._cache and cached_layers.get(MapRendererLayer.PATH):
                del cached_layers[MapRendererLayer.PATH]

            image = self.render_objects(cached_layers, map_data, robot_status, station_status, image, object_scale)

            if segment_mask is not None:
                image = Image.alpha_composite(
                    image,
                    self.render_neglected_segments(
                        map_data.neglected_segments,
                        map_data.segments,
                        image.size,
                        segment_mask,
                        map_data.dimensions,
                        map_data.rotation,
                        map_data.cleaning_map,
                    ),
                )

            if map_data.rotation == 90:
                image = image.transpose(Image.ROTATE_90)
            elif map_data.rotation == 180:
                image = image.transpose(Image.ROTATE_180)
            elif map_data.rotation == 270:
                image = image.transpose(Image.ROTATE_270)

            if info_text:
                base_width = 490  # int(round(image.size[0] / 4 * 3))
                if image.size[0] > base_width:
                    image = image.resize(
                        (
                            base_width,
                            int((float(image.size[1]) * float((base_width / float(image.size[0]))))),
                        ),
                        Image.Resampling.LANCZOS,
                    )

                header_text = f"{time.strftime(('%Y.%m.%d %H:%M:%S' if bool(map_data.saved_map or map_data.recovery_map or map_data.wifi_map) else '%m/%d %H:%M'), time.localtime(map_data.last_updated))}"
                if map_data.history_map:
                    if map_data.task_cruise_points is None:
                        if map_data.startup_method is not None:
                            header_text = f"{header_text} | {map_data.startup_method.name.replace('_', ' ').title().replace('App', 'APP')}"

                        if map_data.second_cleaning:
                            header_text = f"{header_text} | Second Cleaning"
                        elif map_data.cleanup_method is not None:
                            header_text = f"{header_text} | {map_data.cleanup_method.name.replace('_', ' ').title()}"
                elif map_data.recovery_map and map_data.recovery_map_type is not RecoveryMapType.UNKNOWN:
                    header_text = f"{header_text} | {map_data.recovery_map_type.name.replace('_', ' ').title()}"

                image_width = image.size[0]
                min_width = base_width  # int(160 * scale)
                if image_width < min_width:
                    image_width = min_width

                text_draw = ImageDraw.Draw(image, "RGBA")
                text_size = int(image_width * 0.035)
                if self._light_font_file is None:
                    self._light_font_file = zlib.decompress(base64.b64decode(MAP_FONT_LIGHT), zlib.MAX_WBITS | 32)

                text_font = ImageFont.truetype(BytesIO(self._light_font_file), text_size)
                if map_data.history_map:
                    value_font = ImageFont.truetype(BytesIO(self._light_font_file), int(text_size * 1.8))
                    name_font = ImageFont.truetype(BytesIO(self._light_font_file), int(text_size * 0.8))
                left, top, width, height = text_draw.textbbox((0, 0), header_text, font=text_font)
                max_width = image_width * 0.9
                if width > max_width:
                    lines = textwrap.wrap(header_text, width=int(max_width / (text_size / 2)))
                else:
                    lines = [header_text]

                if map_data.history_map and not map_data.task_cruise_points:
                    header_text = ""

                    if len(header_text):
                        lines.append(header_text)

                max_width = 0
                header_height = int(text_size * 5) if map_data.history_map else text_size
                total_height = header_height

                line_sizes = []
                for line in lines:
                    left, top, width, height = text_draw.textbbox((0, 0), line, font=text_font)
                    line_sizes.append((width, height))
                    max_width = max(max_width, width)
                    total_height = total_height + height

                padding = int((min_width - image.size[0]) / 2)
                if padding < 0:
                    padding = 0
                image = ImageOps.expand(
                    image,
                    border=(
                        padding,
                        int(total_height) + int(padding / 2),
                        padding,
                        int(padding / 2),
                    ),
                    fill=bg_color,
                )
                image_width = image.size[0]
                text_draw = ImageDraw.Draw(image, "RGBA")

                text_color = (120, 120, 120, 255)
                value_color = (0, 0, 0, 255)
                if self.color_scheme.dark or self.color_scheme.invert:
                    text_color = (135, 135, 135, 255)
                    value_color = (255, 255, 255, 255)

                if map_data.history_map:
                    cruising_map = bool(map_data.task_cruise_points is not None)
                    map_type = "Cruising" if cruising_map else "Cleaning"
                    header_lines = [
                        (str(map_data.cleaning_time), f"{map_type} Time", "min"),
                        (
                            "Interrupted" if map_data.completed == False else "Completed",
                            f"{map_type} Status",
                            "",
                        ),
                    ]

                    if not cruising_map:
                        header_lines.append((str(map_data.cleaned_area), f"{map_type} Area", "m²"))

                    for i in range(len(header_lines)):
                        value = header_lines[i][0]
                        name = header_lines[i][1]
                        unit = header_lines[i][2]
                        left, top, value_width, value_height = text_draw.textbbox((0, 0), value, font=value_font)
                        left, top, unit_width, unit_height = text_draw.textbbox((0, 0), unit, font=name_font)
                        left, top, name_width, name_height = text_draw.textbbox((0, 0), name, font=name_font)
                        y = text_size
                        x = int(image_width * 0.06)
                        pos = []
                        if len(header_lines) == 3:
                            if i == 0:
                                value_x = x + name_width / 2
                                t1 = value_width / 2
                                t2 = unit_width / 2
                                t3 = text_size / 4
                                pos.extend(
                                    [
                                        (value_x - t1 - t2 - t3, y),
                                        (x, y + (text_size * 2)),
                                        (
                                            value_x - t2 + t1 + t3,
                                            y + value_height - unit_height,
                                        ),
                                    ]
                                )
                            elif i == 1:
                                pos.extend(
                                    [
                                        (image_width - x - value_width, text_size),
                                        (
                                            image_width - x - name_width - ((value_width - name_width) / 2),
                                            y + (text_size * 2),
                                        ),
                                    ]
                                )
                            elif i == 2:
                                t1 = text_size / 2
                                pos.extend(
                                    [
                                        (
                                            ((image_width - value_width - unit_width - t1) / 2),
                                            y,
                                        ),
                                        (
                                            (image_width - name_width) / 2,
                                            y + (text_size * 2),
                                        ),
                                        (
                                            ((image_width - unit_width + value_width + t1) / 2),
                                            y + value_height - unit_height,
                                        ),
                                    ]
                                )
                        elif len(header_lines) == 2:
                            if i == 0:
                                t1 = text_size / 2
                                pos.extend(
                                    [
                                        (
                                            ((image_width - value_width - unit_width - t1) / 2) - (image_width / 4),
                                            y,
                                        ),
                                        (
                                            ((image_width - name_width) / 2) - (image_width / 4),
                                            y + (text_size * 2),
                                        ),
                                        (
                                            ((image_width - unit_width + value_width + t1) / 2) - (image_width / 4),
                                            y + value_height - unit_height,
                                        ),
                                    ]
                                )
                            elif i == 1:
                                pos.extend(
                                    [
                                        (
                                            ((image_width - value_width) / 2) + (image_width / 4),
                                            y,
                                        ),
                                        (
                                            ((image_width - name_width) / 2) + (image_width / 4),
                                            y + (text_size * 2),
                                        ),
                                    ]
                                )

                        for k in range(len(pos)):
                            style = (value_color, value_font) if k == 0 else (text_color, name_font)
                            text_draw.text(pos[k], header_lines[i][k], fill=style[0], font=style[1])

                x = (image_width - max_width) / 2
                line_y = header_height
                for i in range(len(lines)):
                    line_x = x + (max_width - line_sizes[i][0]) / 2
                    text_draw.text((line_x, line_y), lines[i], fill=text_color, font=text_font)
                    line_y = line_y + line_sizes[i][1]

            _LOGGER.info(
                "Render frame: %s:%s took: %.2f",
                map_data.map_id,
                map_data.frame_id,
                time.time() - now,
            )

            if self._cache:
                self._map_data = map_data
                self._robot_status = robot_status
                self._station_status = station_status
                self._image = image
        except Exception:
            _LOGGER.error("Map render Failed: %s", traceback.format_exc())

        self.render_complete = True
        return self._to_buffer(self._image if self._cache else image)

    @staticmethod
    def _segments_layer_needs_update(
        *,
        cache_enabled,
        previous_map,
        map_data,
        has_cached_layer,
    ):
        return (
            not cache_enabled
            or previous_map is None
            or previous_map.segments != map_data.segments
            or previous_map.rotation != map_data.rotation
            or previous_map.cleaning_map != map_data.cleaning_map
            or (
                not previous_map.cleaning_map
                and previous_map.active_segments != map_data.active_segments
            )
            or (
                not previous_map.cleaning_map
                and previous_map.hidden_segments != map_data.hidden_segments
            )
            or (
                previous_map.cleaning_map
                and previous_map.neglected_segments != map_data.neglected_segments
            )
            or bool(
                (not map_data.saved_map or map_data.recovery_map)
                and previous_map.cleanset
            )
            != bool(
                (not map_data.saved_map or map_data.recovery_map)
                and map_data.cleanset
            )
            or not has_cached_layer
        )

    @staticmethod
    def _segment_needs_render(
        *,
        cache_enabled,
        previous_map,
        cached_segments,
        map_data,
        segment_id,
        segment,
    ):
        if not cache_enabled or previous_map is None:
            return True

        previous_segments = previous_map.segments or {}
        if segment_id not in cached_segments or segment_id not in previous_segments:
            return True
        if (
            previous_segments[segment_id] != segment
            or previous_map.rotation != map_data.rotation
        ):
            return True

        map_has_cleanset = bool(
            (not map_data.saved_map or map_data.recovery_map) and map_data.cleanset
        )
        previous_map_has_cleanset = bool(
            (not map_data.saved_map or map_data.recovery_map)
            and previous_map.cleanset
        )
        segment_is_visible = bool(
            (not map_data.active_segments or segment_id in map_data.active_segments)
            and (
                not map_data.hidden_segments
                or segment_id not in map_data.hidden_segments
            )
            and not map_data.cleaning_map
        )
        previous_segment_was_visible = bool(
            (
                not previous_map.active_segments
                or segment_id in previous_map.active_segments
            )
            and (
                not previous_map.hidden_segments
                or segment_id not in previous_map.hidden_segments
            )
            and not previous_map.cleaning_map
        )
        segment_is_neglected = bool(
            map_data.cleaning_map
            and map_data.neglected_segments
            and segment_id in map_data.neglected_segments
        )
        previous_segment_was_neglected = bool(
            previous_map.cleaning_map
            and previous_map.neglected_segments
            and segment_id in previous_map.neglected_segments
        )
        return (
            map_has_cleanset != previous_map_has_cleanset
            or segment_is_visible != previous_segment_was_visible
            or segment_is_neglected != previous_segment_was_neglected
        )

    def render_objects(self, cached_layers, map_data, robot_status, station_status, map_image, scale):
        layer_size = (int(map_image.size[0] * scale), int(map_image.size[1] * scale))
        line_width = max(
            1,
            int(
                round(
                    (3 if map_data.dimensions.scale > 2 else 1)
                    * self.presentation_stroke_scale
                )
            ),
        )
        border_width = max(
            1,
            int(
                round(
                    (2 if map_data.dimensions.scale > 2 else 1)
                    * self.presentation_stroke_scale
                )
            ),
        )
        changes = []
        layers = []

        if map_data.rotation == 0 or map_data.rotation == 180 or self._square:
            width = (map_data.dimensions.width) + (
                (
                    map_data.dimensions.padding[0]
                    + map_data.dimensions.padding[2]
                    - map_data.dimensions.crop[0]
                    - map_data.dimensions.crop[2]
                )
                / map_data.dimensions.scale
            )
            robot_icon_size = width * 0.037
            icon_size = width * (0.022 if self._square else 0.027)
        else:
            height = (map_data.dimensions.height) + (
                (
                    map_data.dimensions.padding[1]
                    + map_data.dimensions.padding[3]
                    - map_data.dimensions.crop[1]
                    - map_data.dimensions.crop[3]
                )
                / map_data.dimensions.scale
            )
            robot_icon_size = height * 0.037
            icon_size = height * 0.027

        robot_icon_size = max(7, min(14, robot_icon_size))
        icon_size = max(3, min(12, icon_size))
        segment_icon_size = icon_size * self.presentation_label_scale
        robot_icon_size *= self.presentation_marker_scale
        icon_size *= self.presentation_marker_scale

        if map_data.dimensions.scale <= 2:
            robot_icon_size = robot_icon_size * 0.7
            icon_size = icon_size * 1.3

        layer = MapRendererLayer.NO_GO
        if (not map_data.saved_map or map_data.recovery_map) and map_data.no_go_areas and self.config.no_go:
            layers.append(layer)
            if (
                not self._cache
                or self._map_data is None
                or self._map_data.no_go_areas != map_data.no_go_areas
                or not cached_layers.get(layer)
            ):
                changes.append(layer)
                cached_layers[layer] = self.render_areas(
                    map_data.no_go_areas,
                    self.color_scheme.no_go_outline,
                    self.color_scheme.no_go,
                    layer_size,
                    map_data.dimensions,
                    border_width,
                    scale,
                )
        elif self._cache and cached_layers.get(layer):
            changes.append(layer)
            del cached_layers[layer]

        layer = MapRendererLayer.WALL
        if (not map_data.saved_map or map_data.recovery_map) and map_data.virtual_walls and self.config.virtual_wall:
            layers.append(layer)
            if (
                not self._cache
                or self._map_data is None
                or self._map_data.virtual_walls != map_data.virtual_walls
                or not cached_layers.get(layer)
            ):
                changes.append(layer)
                cached_layers[layer] = self.render_walls(
                    map_data.virtual_walls,
                    self.color_scheme.virtual_wall,
                    layer_size,
                    map_data.dimensions,
                    line_width,
                    scale,
                )
        elif self._cache and cached_layers.get(layer):
            changes.append(layer)
            del cached_layers[layer]

        layer = MapRendererLayer.PATHWAY
        if (not map_data.saved_map or map_data.recovery_map) and map_data.pathways and self.config.pathway:
            layers.append(layer)
            if (
                not self._cache
                or self._map_data is None
                or self._map_data.pathways != map_data.pathways
                or not cached_layers.get(layer)
            ):
                changes.append(MapRendererLayer.PATH)
                cached_layers[layer] = self.render_walls(
                    map_data.pathways,
                    self.color_scheme.pathway,
                    layer_size,
                    map_data.dimensions,
                    line_width,
                    scale,
                )
        elif self._cache and cached_layers.get(layer):
            changes.append(layer)
            del cached_layers[layer]

        layer = MapRendererLayer.ACTIVE_AREA
        if not map_data.saved_map and map_data.active_areas and self.config.active_area:
            layers.append(layer)
            if (
                not self._cache
                or self._map_data is None
                or self._map_data.active_areas != map_data.active_areas
                or not cached_layers.get(layer)
            ):
                changes.append(layer)
                cached_layers[layer] = self.render_areas(
                    map_data.active_areas,
                    self.color_scheme.active_area_outline,
                    self.color_scheme.active_area,
                    layer_size,
                    map_data.dimensions,
                    border_width,
                    scale,
                )
        elif self._cache and cached_layers.get(layer):
            changes.append(layer)
            del cached_layers[layer]

        layer = MapRendererLayer.ACTIVE_POINT
        if not map_data.saved_map and map_data.active_points and self.config.active_point:
            layers.append(layer)
            if (
                not self._cache
                or self._map_data is None
                or self._map_data.active_points != map_data.active_points
                or not cached_layers.get(layer)
            ):
                changes.append(layer)
                cached_layers[layer] = self.render_points(
                    map_data.active_points,
                    self.color_scheme.active_point_outline,
                    self.color_scheme.active_point,
                    layer_size,
                    map_data.dimensions,
                    border_width,
                    scale,
                )
        elif self._cache and cached_layers.get(layer):
            changes.append(layer)
            del cached_layers[layer]

        layer = MapRendererLayer.FURNITURES
        if map_data.furnitures and self.config.furniture:
            layers.append(layer)
            if (
                not self._cache
                or self._map_data is None
                or self._map_data.furnitures != map_data.furnitures
                or self._map_data.rotation != map_data.rotation
                or not cached_layers.get(layer)
            ):
                if layer not in cached_layers:
                    cached_layers[MapRendererLayer.FURNITURE] = {}
                else:
                    for k in list(cached_layers[MapRendererLayer.FURNITURE].keys()).copy():
                        if k not in map_data.furnitures:
                            del cached_layers[MapRendererLayer.FURNITURE][k]

                changed = False
                for k, v in map_data.furnitures.items():
                    if (
                        not self._cache
                        or self._map_data is None
                        or k not in cached_layers[MapRendererLayer.FURNITURE]
                        or not self._map_data.furnitures
                        or k not in self._map_data.furnitures
                        or self._map_data.furnitures[k] != v
                        or self._map_data.rotation != map_data.rotation
                    ):
                        changed = True
                        cached_layers[MapRendererLayer.FURNITURE][k] = self.render_furniture(
                            v,
                            map_data.furniture_version,
                            layer_size,
                            map_data.dimensions,
                            int((icon_size * 1.2) * map_data.dimensions.scale),
                            map_data.rotation,
                            scale,
                        )

                if changed:
                    changes.append(layer)
                    self._combine_layers(cached_layers, layer_size, layer, MapRendererLayer.FURNITURE)
        elif self._cache and cached_layers.get(layer):
            changes.append(layer)
            del cached_layers[layer]

        layer = MapRendererLayer.SEGMENTS
        if (
            map_data.segments
            and not (map_data.history_map and map_data.task_cruise_points)
            and (
                self.config.icon
                or self.config.name
                or self.config.order
                or self.config.cleaning_times
                or self.config.cleaning_mode
            )
        ):
            layers.append(layer)
            if self._segments_layer_needs_update(
                cache_enabled=self._cache,
                previous_map=self._map_data,
                map_data=map_data,
                has_cached_layer=bool(cached_layers.get(layer)),
            ):
                if MapRendererLayer.SEGMENT not in cached_layers:
                    cached_layers[MapRendererLayer.SEGMENT] = {}
                else:
                    for k in list(cached_layers[MapRendererLayer.SEGMENT].keys()).copy():
                        if k not in map_data.segments:
                            del cached_layers[MapRendererLayer.SEGMENT][k]

                changed = False
                for k in sorted(map_data.segments.keys()):
                    v = map_data.segments[k]
                    if self._segment_needs_render(
                        cache_enabled=self._cache,
                        previous_map=self._map_data,
                        cached_segments=cached_layers[MapRendererLayer.SEGMENT],
                        map_data=map_data,
                        segment_id=k,
                        segment=v,
                    ):
                        changed = True
                        cached_layers[MapRendererLayer.SEGMENT][k] = self.render_segment(
                            v,
                            bool((not map_data.saved_map or map_data.recovery_map) and map_data.cleanset),
                            layer_size,
                            map_data.dimensions,
                            int(segment_icon_size * map_data.dimensions.scale),
                            map_data.rotation,
                            scale,
                            (
                                (not map_data.active_segments or k in map_data.active_segments)
                                and (not map_data.hidden_segments or k not in map_data.hidden_segments)
                                and not map_data.cleaning_map
                            ),
                            (
                                map_data.cleaning_map
                                and map_data.neglected_segments
                                and k in map_data.neglected_segments
                            ),
                        )

                if changed:
                    changes.append(layer)
                    self._combine_layers(cached_layers, layer_size, layer, MapRendererLayer.SEGMENT)
        elif self._cache and cached_layers.get(layer):
            changes.append(layer)
            del cached_layers[layer]

        layer = MapRendererLayer.CHARGER
        if map_data.charger_position and self.config.charger:
            layers.append(layer)
            if (
                not self._cache
                or self._map_data is None
                or self._map_data.charger_position != map_data.charger_position
                or self._map_data.rotation != map_data.rotation
                or self._station_status != station_status
                or not cached_layers.get(layer)
            ):
                # def correct_charger_position(chargerPos, pixel_type, width, height, x, y, gridWidth, borderValue):
                #    newChargerPos = copy.deepcopy(chargerPos)
                #    tmpAngle = newChargerPos.a % 360

                #    if tmpAngle < 0:
                #        tmpAngle += 360

                #    chargerX = int((newChargerPos.x - x) / gridWidth)
                #    chargerY = int((newChargerPos.y - y) / gridWidth)
                #    value = pixel_type[chargerX, chargerY]

                #    if value == borderValue or chargerX < 0 or chargerX >= width or chargerY < 0 or chargerY >= height:
                #        return chargerPos

                #    isChargerInMap = value != 0
                #    delta = 3

                #    for crossDelta in range(4):
                #        if tmpAngle > 45 and tmpAngle < 135 or tmpAngle > 225 and tmpAngle < 315:
                #            startY = 0 if ((chargerY - delta) < 0) else (chargerY - delta)
                #            endY = (height - 1) if ((chargerY + delta) > (height - 1)) else (chargerY + delta)

                #            if tmpAngle > 45 and tmpAngle < 135:
                #                if isChargerInMap:
                #                    endY = chargerY
                #                else:
                #                    startY = chargerY
                #            else:
                #                if isChargerInMap:
                #                    startY = chargerY
                #                else:
                #                    endY = chargerY

                #            findY = -1

                #            for j in range(startY, endY + 1):
                #                startX = -1

                #                for i in range(width):
                #                    leftIndex = (i - 1) if ((i - 1) >= 0) else -1
                #                    rightIndex = (i + 1) if ((i + 1) < width) else -1

                #                    if pixel_type[i, j] == borderValue and (i == 0 or leftIndex != -1 and pixel_type[leftIndex, j] != borderValue):
                #                        startX = i

                #                        if pixel_type[i + 1, j] != borderValue:
                #                            if (chargerX + crossDelta) >= startX and (chargerX - crossDelta) <= i:
                #                                if findY == -1:
                #                                    findY = j
                #                                elif abs(chargerY - j) < abs(findY - j):
                #                                    findY = j
                #                            startX = -1

                #                        continue

                #                    if pixel_type[i, j] == borderValue and startX != -1 and (i == (width - 1) or rightIndex != -1 and pixel_type[rightIndex, j] != borderValue):
                #                        if (chargerX + crossDelta) >= startX and (chargerX - crossDelta) <= i:
                #                            if findY == -1:
                #                                findY = j
                #                            elif abs(chargerY - j) < abs(findY - j):
                #                                findY = j

                #                        startX = -1
                #            if findY != -1:
                #                newChargerPos.y = y + findY * gridWidth
                #                break
                #        else:
                #            _startX = 0 if ((chargerX - delta) < 0) else (chargerX - delta)
                #            endX = (width - 1) if ((chargerX + delta) > (width - 1)) else (chargerX + delta)

                #            if tmpAngle >= 0 and tmpAngle <= 45 or tmpAngle >= 315 and tmpAngle < 360:
                #                if isChargerInMap:
                #                    endX = chargerX
                #                else:
                #                    _startX = chargerX
                #            else:
                #                if isChargerInMap:
                #                    _startX = chargerX
                #                else:
                #                    endX = chargerX

                #            findX = -1

                #            for _i in range(_startX, endX + 1):
                #                _startY = -1

                #                for _j in range(height):
                #                    topIndex = (_j - 1) if ((_j - 1) >= 0) else -1
                #                    bottomIndex = (_j + 1) if ((_j + 1) < height) else -1

                #                    if pixel_type[_i, _j] == borderValue and (_j == 0 or topIndex != -1 and pixel_type[_i, topIndex] != borderValue):
                #                        _startY = _j

                #                        if pixel_type[_i, (_j + 1)] != borderValue:
                #                            if ((chargerY + crossDelta) >= _startY) and ((chargerY - crossDelta) <= _j):
                #                                if findX == -1:
                #                                    findX = _i
                #                                elif abs(chargerX - _i) < abs(findX - _i):
                #                                    findX = _i
                #                            _startY = -1

                #                        continue

                #                    if pixel_type[_i, _j] == borderValue and _startY != -1 and (_j == height - 1 or bottomIndex != -1 and pixel_type[_i, bottomIndex] != borderValue):
                #                        if ((chargerY + crossDelta) >= _startY) and ((chargerY - crossDelta) <= _j):
                #                            if findX == -1:
                #                                findX = _i
                #                            elif abs(chargerX - _i) < abs(findX - _i):
                #                                findX = _i

                #                        _startY = -1

                #            if findX != -1:
                #                newChargerPos.x = x + findX * gridWidth
                #                break

                #    return newChargerPos

                charger_position = map_data.charger_position
                offset = 0
                if self._robot_type != RobotType.VSLAM and self.icon_set == 2:
                    offset = int(robot_icon_size * 21.42)
                elif self._robot_type == RobotType.VSLAM and self.icon_set == 3:
                    offset = int(-robot_icon_size * 18)

                if offset:
                    charger_position = Point(
                        charger_position.x - offset * math.cos(charger_position.a * math.pi / 180),
                        charger_position.y - offset * math.sin(charger_position.a * math.pi / 180),
                        charger_position.a,
                    )

                changes.append(layer)
                cached_layers[layer] = self.render_charger(
                    charger_position,
                    station_status,
                    layer_size,
                    map_data.dimensions,
                    int((robot_icon_size * (map_data.dimensions.scale if map_data.dimensions.scale > 2 else 3)) * 1.2),
                    map_data.rotation,
                    scale,
                )
        elif self._cache and cached_layers.get(layer):
            changes.append(layer)
            del cached_layers[layer]

        layer = MapRendererLayer.ROBOT
        if not map_data.saved_map and map_data.robot_position and self.config.robot:
            layers.append(layer)
            if (
                not self._cache
                or self._map_data is None
                or self._map_data.robot_position != map_data.robot_position
                or self._map_data.charger_position != map_data.charger_position
                or self._map_data.rotation != map_data.rotation
                or self._robot_status != robot_status
                or self._station_status != station_status
                or self._map_data.docked != map_data.docked
                or not cached_layers.get(layer)
            ):
                robot_position = map_data.robot_position

                if map_data.docked:
                    # Calculate charger angle
                    charger_angle = map_data.charger_position.a
                    if self._robot_type != RobotType.VSLAM:
                        offset = int(
                            robot_icon_size * (21.42)
                        )

                        if self.icon_set != 2:
                            if charger_angle > -45 and charger_angle < 45:
                                charger_angle = 0
                            elif (
                                charger_angle > -45
                                and charger_angle <= 45
                                or charger_angle > 315
                                and charger_angle <= 405
                            ):
                                charger_angle = 0
                            elif (
                                charger_angle > 45
                                and charger_angle <= 135
                                or charger_angle > -315
                                and charger_angle <= -225
                            ):
                                charger_angle = 90
                            elif (
                                charger_angle > 135
                                and charger_angle <= 225
                                or charger_angle > -225
                                and charger_angle <= -135
                            ):
                                charger_angle = 180
                            elif (
                                charger_angle > 225
                                and charger_angle <= 315
                                or charger_angle > -135
                                and charger_angle <= -45
                            ):
                                charger_angle = 270
                    else:
                        offset = int(robot_icon_size * 35.71)

                    robot_position = Point(
                        map_data.charger_position.x + offset * math.cos(charger_angle * math.pi / 180),
                        map_data.charger_position.y + offset * math.sin(charger_angle * math.pi / 180),
                        (
                            charger_angle
                        ),
                    )

                changes.append(layer)
                cached_layers[layer] = self.render_mower(
                    robot_position,
                    robot_status,
                    layer_size,
                    map_data.dimensions,
                    int(robot_icon_size * (map_data.dimensions.scale if map_data.dimensions.scale > 2 else 3)),
                    map_data.rotation,
                    scale,
                )
        elif self._cache and cached_layers.get(layer):
            changes.append(layer)
            del cached_layers[layer]

        layer = MapRendererLayer.ROUTER
        if map_data.router_position and map_data.wifi_map:
            layers.append(layer)
            if (
                not self._cache
                or self._map_data is None
                or self._map_data.router_position != map_data.router_position
                or self._map_data.rotation != map_data.rotation
                or not cached_layers.get(layer)
            ):
                changes.append(layer)
                cached_layers[layer] = self.render_router(
                    map_data.router_position,
                    layer_size,
                    map_data.dimensions,
                    int((robot_icon_size * 1.25) * map_data.dimensions.scale),
                    map_data.rotation,
                    scale,
                )
        elif self._cache and cached_layers.get(layer):
            changes.append(layer)
            del cached_layers[layer]

        layer = MapRendererLayer.OBSTACLES
        if not map_data.saved_map and map_data.obstacles and (self.config.obstacle or self.config.pet):
            layers.append(layer)
            if (
                not self._cache
                or self._map_data is None
                or self._map_data.obstacles != map_data.obstacles
                or self._map_data.rotation != map_data.rotation
                or not cached_layers.get(layer)
            ):
                if MapRendererLayer.OBSTACLE not in cached_layers:
                    cached_layers[MapRendererLayer.OBSTACLE] = {}
                else:
                    for k in list(cached_layers[MapRendererLayer.OBSTACLE].keys()).copy():
                        if k not in map_data.obstacles:
                            del cached_layers[MapRendererLayer.OBSTACLE][k]

                changed = False
                for k, v in map_data.obstacles.items():
                    if not self.config.obstacle and v.type != ObstacleType.PET:
                        continue
                    elif not self.config.pet and v.type == ObstacleType.PET:
                        continue

                    if (
                        not self._cache
                        or self._map_data is None
                        or k not in cached_layers[MapRendererLayer.OBSTACLE]
                        or not self._map_data.obstacles
                        or k not in self._map_data.obstacles
                        or self._map_data.obstacles[k] != v
                        or self._map_data.rotation != map_data.rotation
                    ):
                        obstacle_image = self.render_obstacle(
                            v,
                            layer_size,
                            map_data.dimensions,
                            int((icon_size * 1.2) * map_data.dimensions.scale),
                            map_data.rotation,
                            scale,
                        )
                        if obstacle_image:
                            changed = True
                            cached_layers[MapRendererLayer.OBSTACLE][k] = obstacle_image
                        elif k in cached_layers[MapRendererLayer.OBSTACLE]:
                            del cached_layers[MapRendererLayer.OBSTACLE][k]

                if changed:
                    changes.append(layer)
                    self._combine_layers(cached_layers, layer_size, layer, MapRendererLayer.OBSTACLE)
        elif self._cache and cached_layers.get(layer):
            changes.append(layer)
            del cached_layers[layer]

        layer = MapRendererLayer.CRUISE_POINTS
        if not map_data.saved_map and map_data.active_cruise_points:  # and self.config.cruise_point:
            layers.append(layer)
            if (
                not self._cache
                or self._map_data is None
                or self._map_data.active_cruise_points != map_data.active_cruise_points
                or self._map_data.rotation != map_data.rotation
                or not cached_layers.get(layer)
            ):
                if MapRendererLayer.CRUISE_POINT not in cached_layers:
                    cached_layers[MapRendererLayer.CRUISE_POINT] = {}
                else:
                    for k in list(cached_layers[MapRendererLayer.CRUISE_POINT].keys()).copy():
                        if k not in map_data.active_cruise_points:
                            del cached_layers[MapRendererLayer.CRUISE_POINT][k]

                changed = False
                for k, v in map_data.active_cruise_points.items():
                    if (
                        self._map_data is None
                        or k not in cached_layers[MapRendererLayer.CRUISE_POINT]
                        or not self._map_data.active_cruise_points
                        or k not in self._map_data.active_cruise_points
                        or self._map_data.active_cruise_points[k] != v
                        or self._map_data.rotation != map_data.rotation
                    ):
                        changed = True
                        cached_layers[MapRendererLayer.CRUISE_POINT][k] = self.render_cruise_point(
                            k,
                            v,
                            layer_size,
                            map_data.dimensions,
                            int(round(icon_size * 1.25 * map_data.dimensions.scale)),
                            map_data.rotation,
                            scale,
                        )

                if changed:
                    changes.append(layer)
                    self._combine_layers(cached_layers, layer_size, layer, MapRendererLayer.CRUISE_POINT)
        elif self._cache and cached_layers.get(layer):
            changes.append(layer)
            del cached_layers[layer]

        if changes or not self._cache:
            cached_layers[MapRendererLayer.OBJECTS] = Image.new(
                "RGBA",
                [layer_size[0], layer_size[1]],
                (255, 255, 255, 0),
            )
            for l in layers:
                if cached_layers.get(l):
                    if l in changes:
                        _LOGGER.debug("Render %s", l.name)
                    cached_layers[MapRendererLayer.OBJECTS] = Image.alpha_composite(
                        cached_layers[MapRendererLayer.OBJECTS], cached_layers[l]
                    )

            if layer_size != map_image.size:
                cached_layers[MapRendererLayer.OBJECTS].thumbnail(
                    map_image.size, Image.Resampling.BOX, reducing_gap=1.5
                )
        else:
            if not cached_layers.get(MapRendererLayer.OBJECTS):
                return map_image

        return Image.alpha_composite(
            map_image,
            cached_layers[MapRendererLayer.OBJECTS],
        )

    def render_areas(self, areas, color, fill, layer_size, dimensions, width, scale):
        new_layer = Image.new("RGBA", layer_size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(new_layer, "RGBA")
        for area in areas:
            p = area.to_img(dimensions)
            coords = [
                p.x0 * scale,
                p.y0 * scale,
                p.x1 * scale,
                p.y1 * scale,
                p.x2 * scale,
                p.y2 * scale,
                p.x3 * scale,
                p.y3 * scale,
            ]
            draw.polygon(coords, fill, color, width=(width * scale))
        return new_layer

    def render_points(self, points, color, fill, layer_size, dimensions, width, scale):
        new_layer = Image.new("RGBA", layer_size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(new_layer, "RGBA")
        size = 15 * dimensions.grid_size
        for point in points:
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

            p = area.to_img(dimensions)
            coords = [
                p.x0 * scale,
                p.y0 * scale,
                p.x1 * scale,
                p.y1 * scale,
                p.x2 * scale,
                p.y2 * scale,
                p.x3 * scale,
                p.y3 * scale,
            ]
            draw.polygon(coords, fill, color, width=(width * scale))
        return new_layer

    def render_walls(self, walls, color, layer_size, dimensions, width, scale):
        new_layer = Image.new("RGBA", layer_size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(new_layer, "RGBA")
        for wall in walls:
            p = wall.to_img(dimensions)
            draw.line(
                [p.x0 * scale, p.y0 * scale, p.x1 * scale, p.y1 * scale],
                color,
                width=(width * scale),
            )
        return new_layer

    def render_path(self, path, color, layer_size, mask, dimensions, width, scale):
        new_layer = Image.new("RGBA", layer_size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(new_layer, "RGBA")
        sweep = []
        mop = []
        sweep_path = []
        path_type = ""

        for point in path:
            p = point.to_img(dimensions)
            if point.path_type == PathType.LINE:
                l = [p.x * scale, p.y * scale]
            else:
                if sweep_path:
                    sweep.append(sweep_path)

                path_type = point.path_type
                sweep_path = []

        if sweep_path:
            sweep.append(sweep_path)

        for path in sweep:
            size = width * scale
            draw.line(
                path,
                width=int(round(size)),
                fill=color,
                joint="curve",
            )
            size = int(math.floor(size / 2))
            draw.ellipse(
                [
                    path[-2] - size,
                    path[-1] - size,
                    path[-2] + size,
                    path[-1] + size,
                ],
                fill=color,
            )
            draw.ellipse(
                [
                    path[0] - size,
                    path[1] - size,
                    path[0] + size,
                    path[1] + size,
                ],
                fill=color,
            )

        return new_layer

    def render_charger(
        self,
        charger_position,
        station_status,
        layer_size,
        dimensions,
        size,
        map_rotation,
        scale,
    ):
        new_layer = Image.new("RGBA", layer_size, (255, 255, 255, 0))
        icon_size = int(size * scale)
        if self.icon_set == 3:
            icon_size = int(icon_size * 1.2)
        elif self.icon_set == 2:
            icon_size = int(icon_size * 1.5)
        elif self._robot_type == RobotType.VSLAM:
            icon_size = int(icon_size * 1.5)

        if self._charger_icon is None:
            if self.icon_set == 3:
                charger_image = MAP_CHARGER_IMAGE_MATERIAL
            elif self.icon_set == 2:
                charger_image = MAP_CHARGER_IMAGE_MIJIA
            else:
                if self._robot_type == RobotType.VSLAM:
                    charger_image = MAP_CHARGER_VSLAM_IMAGE_DREAME
                else:
                    charger_image = MAP_CHARGER_IMAGE_DREAME
            self._charger_icon = Image.open(BytesIO(base64.b64decode(charger_image))).convert("RGBA")

            if self.icon_set == 3:
                self._charger_icon = DreameMowerMapRenderer._set_icon_color(
                    self._charger_icon,
                    icon_size,
                    (0, 255, 126),
                )

            if self.color_scheme.dark:
                enhancer = ImageEnhance.Brightness(self._charger_icon)
                self._charger_icon = enhancer.enhance(0.7)

        charger_icon = self._charger_icon.resize((icon_size, icon_size), resample=Image.Resampling.NEAREST).rotate(
            (
                charger_position.a
                if self._robot_type == RobotType.VSLAM
                or self.icon_set == 0
                or self.icon_set == 2
                or self.icon_set == 3
                else (-map_rotation)
            ),
            expand=1,
        )

        point = charger_position.to_img(dimensions)
        new_layer.paste(
            charger_icon,
            (
                int((point.x * scale) - (charger_icon.size[0] / 2)),
                int((point.y * scale) - (charger_icon.size[1] / 2)),
            ),
            charger_icon,
        )

        return new_layer

    def render_mower(
        self,
        robot_position,
        robot_status,
        layer_size,
        dimensions,
        size,
        map_rotation,
        scale,
    ):
        new_layer = Image.new("RGBA", layer_size, (255, 255, 255, 0))
        icon_size = int(size * scale)
        robot_icon_size = (
            int(icon_size * 1.4)
            if self.icon_set == 2 or (self._robot_type == RobotType.VSLAM and self.icon_set == 3)
            else icon_size
        )
        if self.presentation_marker_image is not None:
            self._robot_icon = self.presentation_marker_image
        if self._robot_icon is None:
            if self.icon_set == 2:
                if self._robot_type == RobotType.VSLAM:
                    robot_image = MAP_ROBOT_VSLAM_IMAGE_MIJIA
                else:
                    robot_image = MAP_ROBOT_LIDAR_IMAGE_MIJIA
            else:
                if self._robot_type == RobotType.VSLAM:
                    if self.icon_set == 3:
                        robot_image = MAP_ROBOT_VSLAM_IMAGE_DREAME_LIGHT
                    else:
                        robot_image = MAP_ROBOT_VSLAM_IMAGE_DREAME_DARK
                else:
                    if self.icon_set == 3:
                        robot_image = MAP_ROBOT_LIDAR_IMAGE_DREAME_LIGHT
                    else:
                        robot_image = MAP_ROBOT_LIDAR_IMAGE_DREAME_DARK

            self._robot_icon = Image.open(BytesIO(base64.b64decode(robot_image))).convert("RGBA")

            if (
                self.icon_set != 2
                and self.icon_set != 3
            ):
                enhancer = ImageEnhance.Brightness(self._robot_icon)
                if self.color_scheme.dark:
                    self._robot_icon = enhancer.enhance(1.5)
                else:
                    self._robot_icon = enhancer.enhance(0.9)

        icon = self._robot_icon.resize(
            (robot_icon_size, robot_icon_size),
            resample=Image.Resampling.NEAREST,
        ).rotate(robot_position.a, expand=1)
        point = robot_position.to_img(dimensions)

        if not self._low_memory:
            status_icon = None
            has_warning = False
            if robot_status >= 10:
                has_warning = True
                robot_status = robot_status - 10

            if robot_status == 1:
                if self._robot_cleaning_icon is None:
                    self._robot_cleaning_icon = (
                        Image.open(BytesIO(base64.b64decode(MAP_ROBOT_CLEANING_IMAGE)))
                        .convert("RGBA")
                        .resize(
                            ((int(icon_size * 1.25), int(icon_size * 1.25))),
                            resample=Image.Resampling.NEAREST,
                        )
                    )
                status_icon = self._robot_cleaning_icon

                if self.config.cleaning_direction:
                    if self._robot_cleaning_direction_icon is None:
                        self._robot_cleaning_direction_icon = (
                            Image.open(BytesIO(base64.b64decode(MAP_ROBOT_CLEANING_DIRECTION_IMAGE)))
                            .convert("RGBA")
                            .resize(
                                ((int(icon_size * 1.5), int(icon_size * 1.5))),
                            )
                        )

                    ico = self._robot_cleaning_direction_icon.rotate(robot_position.a, expand=1)

                    offset = int(icon_size * 0.3)
                    x = point.x + offset * math.cos(-robot_position.a * math.pi / 180)
                    y = point.y + offset * math.sin(-robot_position.a * math.pi / 180)
                    new_layer.paste(
                        ico,
                        (
                            int(x * scale - (ico.size[0] / 2)),
                            int(y * scale - (ico.size[1] / 2)),
                        ),
                    )
            elif robot_status == 2:
                if self._robot_charging_icon is None:
                    self._robot_charging_icon = (
                        Image.open(BytesIO(base64.b64decode(MAP_ROBOT_CHARGING_IMAGE)))
                        .convert("RGBA")
                        .resize(
                            ((int(icon_size * 1.3), int(icon_size * 1.3))),
                            resample=Image.Resampling.NEAREST,
                        )
                    )
                status_icon = self._robot_charging_icon
            elif has_warning:
                if self._robot_warning_icon is None:
                    self._robot_warning_icon = (
                        Image.open(BytesIO(base64.b64decode(MAP_ROBOT_WARNING_IMAGE)))
                        .convert("RGBA")
                        .resize(
                            ((int(icon_size * 1.3), int(icon_size * 1.3))),
                            resample=Image.Resampling.NEAREST,
                        )
                    )
                status_icon = self._robot_warning_icon

            if status_icon:
                mask = Image.new("L", status_icon.size, 0)
                draw = ImageDraw.Draw(mask)
                draw.ellipse((0, 0, status_icon.size[0], status_icon.size[1]), fill=255)
                new_layer.paste(
                    status_icon,
                    (
                        int(point.x * scale - (status_icon.size[0] / 2)),
                        int(point.y * scale - (status_icon.size[1] / 2)),
                    ),
                    mask,
                )

        new_layer.paste(
            icon,
            (
                int(point.x * scale - (icon.size[0] / 2)),
                int(point.y * scale - (icon.size[1] / 2)),
            ),
            icon,
        )

        if not self._low_memory and robot_status == 3:
            if self._robot_sleeping_icon is None:
                sleeping_icon = (
                    Image.open(BytesIO(base64.b64decode(MAP_ROBOT_SLEEPING_IMAGE)))
                    .convert("RGBA")
                    .rotate(-map_rotation, expand=1)
                )
                enhancer = ImageEnhance.Brightness(sleeping_icon)
                if not self.color_scheme.dark:
                    sleeping_icon = enhancer.enhance(0.7)

                self._robot_sleeping_icon = [
                    sleeping_icon.resize(
                        ((int(icon_size * 0.3), int(icon_size * 0.3))),
                        resample=Image.Resampling.NEAREST,
                    ),
                    sleeping_icon.resize(
                        ((int(icon_size * 0.35), int(icon_size * 0.35))),
                        resample=Image.Resampling.NEAREST,
                    ),
                ]

            for k in [
                [int(icon_size * 0.34), int(icon_size * 0.18), 0],
                [int(icon_size * 0.43), int(icon_size * 0.43), 1],
            ]:
                status_icon = self._robot_sleeping_icon[k[2]]
                if map_rotation == 90:
                    x = point.x + k[1]
                    y = point.y + k[0]
                elif map_rotation == 180:
                    x = point.x - k[0]
                    y = point.y + k[1]
                elif map_rotation == 270:
                    x = point.x - k[1]
                    y = point.y - k[0]
                else:
                    x = point.x + k[0]
                    y = point.y - k[1]

                new_layer.paste(
                    status_icon,
                    (
                        int(x * scale - (status_icon.size[0] / 2)),
                        int(y * scale - (status_icon.size[1] / 2)),
                    ),
                    status_icon,
                )
        return new_layer

    def render_segment(
        self,
        segment,
        cleanset,
        layer_size,
        dimensions,
        size,
        rotation,
        scale,
        active,
        neglected,
    ):
        new_layer = Image.new("RGBA", layer_size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(new_layer, "RGBA")
        if segment.x is not None and segment.y is not None:
            active = active and not neglected
            text = None
            if segment.type not in self._segment_icons:
                icon_set = SEGMENT_ICONS_DREAME
                if self.icon_set == 1:
                    icon_set = SEGMENT_ICONS_DREAME_OLD
                elif self.icon_set == 2:
                    icon_set = SEGMENT_ICONS_MIJIA
                elif self.icon_set == 3:
                    icon_set = SEGMENT_ICONS_MATERIAL

                if segment.type in icon_set:
                    self._segment_icons[segment.type] = Image.open(
                        BytesIO(base64.b64decode(icon_set[segment.type]))
                    ).convert("RGBA")
                    if self.color_scheme.invert and not (self.config.name_background and self.icon_set != 2):
                        enhancer = ImageEnhance.Brightness(self._segment_icons[segment.type])
                        self._segment_icons[segment.type] = enhancer.enhance(0.1)

            icon = self._segment_icons.get(segment.type) if self.config.icon else None
            if segment.type == 0 or self.config.name or icon is None:
                text = (
                    segment.name
                    if (self._robot_type != RobotType.VSLAM or icon is not None)
                    or (segment.custom_name is not None and segment.type == 0)
                    or self.icon_set == 2
                    else segment.letter
                )
            elif segment.index > 0:
                text = str(segment.index)

            text_font = None
            order_font = None
            render_font = text and (self.config.name or segment.type == 0 or segment.index > 0)
            if self._font_file is None and (render_font or (segment.order and self.config.order)):
                self._font_file = zlib.decompress(base64.b64decode(MAP_FONT), zlib.MAX_WBITS | 32)

            if render_font and self._font_file:
                text_font = ImageFont.truetype(
                    BytesIO(self._font_file),
                    int((size * 1.9)) if segment.index or icon is None else int((size * 1.7)),
                )

            if active and segment.order and self.config.order:
                order_font = ImageFont.truetype(BytesIO(self._font_file), int((size * 2.1)))

            p = Point(segment.x, segment.y).to_img(dimensions, False)
            x = p.x
            y = p.y

            if neglected:
                offset = size * 1.5
                x_offset = 0
                y_offset = -offset
                if rotation == 90:
                    y_offset = 0
                    x_offset = offset
                elif rotation == 180:
                    y_offset = offset
                elif rotation == 270:
                    y_offset = 0
                    x_offset = -offset

                x = x + x_offset
                y = y + y_offset

            if self.config.name or self.config.icon:
                if segment.type or text_font or not self.config.name:
                    icon_size = size * (1.75 if self.icon_set == 1 else 1.3)
                    x0 = x - size
                    y0 = y - size
                    x1 = x + size
                    y1 = y + size

                    if text_font:
                        left, top, tw, th = draw.textbbox((0, 0), text, text_font)
                        ws = tw / 4

                        if segment.index or icon is None:
                            icon_size = size * 1.35
                            padding = icon_size / 2
                            text_offset = (icon_size / 2) + 2
                            icon_offset = 2
                            th = int(round(size * 2.3))
                        else:
                            icon_size = size * 1.15
                            padding = icon_size * 0.35
                            icon_offset = padding - 2
                            text_offset = icon_size / 2
                            th = int(round(size * 1.9))

                        if icon is None:
                            text_offset = 0
                            padding = -(icon_size / 4)

                        name_background = self.config.icon or (self.config.name_background and self.config.name)

                        stroke_width = dimensions.scale
                        if neglected:
                            stroke_color = self.color_scheme.neglected_segment
                            text_color = (
                                stroke_color[0],
                                stroke_color[1],
                                stroke_color[2],
                                255,
                            )
                        elif not name_background:
                            if self.color_scheme.dark:
                                text_color = (240, 240, 240, 255)
                                stroke_color = (0, 0, 0, 200)
                            else:
                                text_color = (15, 15, 15, 255)
                                stroke_color = (255, 255, 255, 200)
                        elif self.config.icon or self.config.name:
                            stroke_width = 1
                            if self.config.name_background and self.icon_set != 2 and self.color_scheme.invert:
                                text_color = (240, 240, 240, 255)
                                stroke_color = (240, 240, 240, 200)
                            else:
                                text_color = self.color_scheme.text
                                stroke_color = self.color_scheme.text_stroke

                        th = th + int(stroke_width * 2)

                        if rotation == 90 or rotation == 270:
                            y0 = y0 - ws - padding
                            y1 = y1 + ws + padding

                            if rotation == 90:
                                ty = (y - ws + text_offset) * scale
                                tx = (x - (th / 4)) * scale
                                y = y - ws - icon_offset
                            else:
                                ty = (y - ws - text_offset) * scale
                                tx = (x - (th / 4)) * scale
                                y = y + ws + icon_offset
                        else:
                            x0 = x0 - ws - padding
                            x1 = x1 + ws + padding

                            if rotation == 0:
                                tx = (x - ws + text_offset) * scale
                                ty = (y - (th / 4)) * scale
                                x = x - ws - icon_offset
                            else:
                                tx = (x - ws - text_offset) * scale
                                ty = (y - (th / 4)) * scale
                                x = x + ws + icon_offset

                        if (
                            name_background
                            # and not self.config.name_background
                            and active
                            and not neglected
                        ):
                            draw.rounded_rectangle(
                                [
                                    int(x0 * scale),
                                    int(y0 * scale),
                                    int(x1 * scale),
                                    int(y1 * scale),
                                ],
                                fill=(
                                    self.color_scheme.segment[segment.color_index][1]
                                    if name_background and self.config.name_background and self.icon_set != 2
                                    else self.color_scheme.icon_background
                                ),
                                radius=((size * scale)),
                            )

                        icon_text = Image.new("RGBA", (tw, th), (255, 255, 255, 0))
                        draw_text = ImageDraw.Draw(icon_text, "RGBA")

                        draw_text.text(
                            (0, 0),
                            text,
                            font=text_font,
                            fill=text_color,
                            stroke_width=stroke_width,
                            stroke_fill=stroke_color,
                        )
                        icon_text = icon_text.rotate(-rotation, expand=1)
                        new_layer.paste(icon_text, (int(tx), int(ty)), icon_text)
                        if self.icon_set == 1:
                            icon_size *= 1.3
                    elif active:  # and not self.config.name_background
                        draw.ellipse(
                            [x0 * scale, y0 * scale, x1 * scale, y1 * scale],
                            fill=(
                                self.color_scheme.segment[segment.color_index][1]
                                if self.config.name_background and self.icon_set != 2
                                else self.color_scheme.icon_background
                            ),
                        )

                    if icon is not None:
                        s = icon_size * scale
                        if neglected:
                            icon = DreameMowerMapRenderer._set_icon_color(
                                icon,
                                s,
                                text_color,
                            )
                        else:
                            icon = icon.resize((int(s), int(s)))
                        icon = icon.rotate(-rotation, expand=1)
                        new_layer.paste(
                            icon,
                            (
                                int(x * scale - (icon.size[0] / 2)),
                                int(y * scale - (icon.size[1] / 2)),
                            ),
                            icon,
                        )

            custom = (
                active
                and not neglected
                and cleanset
                and (
                    self.config.cleaning_times
                    or self.config.cleaning_mode
                )
            )
            if order_font or custom:
                offset = size * 2.7
                x_offset = 0
                y_offset = -offset

                if rotation == 90:
                    y_offset = 0
                    x_offset = offset
                elif rotation == 180:
                    y_offset = offset
                elif rotation == 270:
                    y_offset = 0
                    x_offset = -offset

                x = p.x + x_offset
                y = p.y + y_offset
                cleaning_mode = (
                    None
                    if segment.cleaning_mode is None or segment.cleaning_mode < 0 or segment.cleaning_mode > 3
                    else segment.cleaning_mode
                )
                if custom:
                    s = scale * 2
                    arrow = (s + 2) * scale
                    if order_font:
                        icon_count = 5
                    else:
                        icon_count = 4

                    if not self.config.cleaning_times or segment.cleaning_times is None:
                        icon_count = icon_count - 1
                    if not self.config.cleaning_mode or cleaning_mode is None:
                        icon_count = icon_count - 1
                    if cleaning_mode == 0 or cleaning_mode == 1:
                        icon_count = icon_count - 1
                    if (
                        segment.cleaning_route is not None
                        and cleaning_mode == 1
                    ):
                        icon_count = icon_count + 1
                else:
                    icon_count = 1

                if not icon and not self.config.icon:
                    arrow = 0

                radius = size
                arrow = int(round(radius * 0.6))
                margin = int(round(size * 0.3)) if icon_count > 1 else 0
                if custom:
                    radius = size - 2

                icon_w = ((radius * icon_count * 2) * scale) + (arrow * 2) + (margin * 2)
                icon_h = ((radius * 2) * scale) + (arrow * 2)
                icon = Image.new("RGBA", (icon_w, icon_h), (255, 255, 255, 0))
                icon_draw = ImageDraw.Draw(icon, "RGBA")

                if arrow and (segment.type != 0 or text_font):
                    xx = icon_w / 2
                    yy = icon_h - 2
                    icon_draw.polygon(
                        [
                            (xx, yy),
                            (xx - arrow, yy - arrow),
                            (xx + arrow, yy - arrow),
                        ],
                        fill=self.color_scheme.settings_background,
                    )

                icon_draw.rounded_rectangle(
                    [arrow, arrow, icon_w - arrow, icon_h - arrow],
                    fill=self.color_scheme.settings_background,
                    radius=((icon_h - (arrow * 2)) / 2),
                )

                padding = int(round((size * 0.3) + (size * 0.6)))
                r = icon_h - (padding * 2)
                ellipse_x1 = padding + margin
                ellipse_x2 = ellipse_x1 + r
                if order_font:
                    icon_draw.ellipse(
                        [ellipse_x1, padding, ellipse_x2, icon_h - padding],
                        fill=self.color_scheme.segment[segment.color_index][1],
                    )
                    text = str(segment.order)
                    left, top, tw, th = icon_draw.textbbox((0, 0), text, order_font)
                    icon_draw.text(
                        (
                            (icon_h - tw) / 2 + margin,
                            (icon_h - th - int(round(radius * 0.4))) / 2,
                        ),
                        text,
                        font=order_font,
                        fill=self.color_scheme.order,
                        stroke_width=1,
                        stroke_fill=self.color_scheme.text_stroke,
                    )

                    ellipse_x1 = ellipse_x2 + (margin * 2)
                    ellipse_x2 = ellipse_x1 + r

                if custom:
                    icon_size = size * 1.45

                    if self.config.cleaning_mode and cleaning_mode is not None:
                        if self.icon_set == 2:
                            s = icon_size * 1.2 * scale
                        else:
                            s = icon_size * 0.85 * scale

                        ico = DreameMowerMapRenderer._set_icon_color(
                            self._cleaning_mode_icon[segment.cleaning_mode],
                            s,
                            self.color_scheme.segment[segment.color_index][1],
                        )

                        icon_draw.ellipse(
                            [ellipse_x1, padding, ellipse_x2, (icon_h - padding)],
                            fill=self.color_scheme.settings_icon_background,
                        )
                        icon.paste(
                            ico,
                            (
                                int(2 + ellipse_x1 + ((ellipse_x2 - ellipse_x1) / 2) - ico.size[0] / 2),
                                int(((icon_h / 2) - ico.size[1] / 2)),
                            ),
                            ico,
                        )

                        ellipse_x1 = ellipse_x2 + (margin * 2)
                        ellipse_x2 = ellipse_x1 + r

                    if self.config.cleaning_times and segment.cleaning_times is not None:
                        if self.icon_set == 3 or self.icon_set == 2:
                            s = icon_size * 0.95 * scale
                        else:
                            s = icon_size * 0.85 * scale

                        ico = DreameMowerMapRenderer._set_icon_color(
                            self._cleaning_times_icon[segment.cleaning_times - 1],
                            s,
                            self.color_scheme.segment[segment.color_index][1],
                        )

                        icon_draw.ellipse(
                            [ellipse_x1, padding, ellipse_x2, (icon_h - padding)],
                            fill=self.color_scheme.settings_icon_background,
                        )
                        icon.paste(
                            ico,
                            (
                                int(2 + ellipse_x1 + ((ellipse_x2 - ellipse_x1) / 2) - ico.size[0] / 2),
                                int(((icon_h / 2) - ico.size[1] / 2)),
                            ),
                            ico,
                        )

                icon = icon.rotate(-rotation, expand=1)
                new_layer.paste(
                    icon,
                    (
                        int((x * scale) - ((icon.size[0]) / 2)),
                        int((y * scale) - ((icon.size[1]) / 2)),
                    ),
                    icon,
                )
        return new_layer

    def render_obstacle(self, obstacle, layer_size, dimensions, size, rotation, scale):
        if obstacle.ignore_status == 1:
            if (
                obstacle.type.value not in self._obstacle_hidden_icons
                and obstacle.type.value in OBSTACLE_TYPE_TO_HIDDEN_ICON
            ):
                self._obstacle_hidden_icons[obstacle.type.value] = Image.open(
                    BytesIO(base64.b64decode(OBSTACLE_TYPE_TO_HIDDEN_ICON[obstacle.type.value]))
                ).convert("RGBA")
            icon = self._obstacle_hidden_icons.get(obstacle.type.value)
        else:
            if obstacle.type.value not in self._obstacle_icons and obstacle.type.value in OBSTACLE_TYPE_TO_ICON:
                self._obstacle_icons[obstacle.type.value] = Image.open(
                    BytesIO(base64.b64decode(OBSTACLE_TYPE_TO_ICON[obstacle.type.value]))
                ).convert("RGBA")
            icon = self._obstacle_icons.get(obstacle.type.value)

        if icon:
            new_layer = Image.new("RGBA", layer_size, (255, 255, 255, 0))
            icon_size = size * scale * (1 if obstacle.ignore_status == 1 else 0.85)
            draw = ImageDraw.Draw(new_layer, "RGBA")

            if obstacle.ignore_status != 2 and self._obstacle_background is None:
                self._obstacle_background = Image.open(BytesIO(base64.b64decode(MAP_ICON_OBSTACLE_BG_DREAME))).convert(
                    "RGBA"
                )
                s = int(size * scale * 2)
                self._obstacle_background.thumbnail((s, s), Image.Resampling.LANCZOS)
                self._obstacle_background = self._obstacle_background.rotate(-rotation, expand=1)

            if obstacle.ignore_status == 2 and self._obstacle_hidden_background is None:
                self._obstacle_hidden_background = Image.open(
                    BytesIO(base64.b64decode(MAP_ICON_OBSTACLE_HIDDEN_BG_DREAME))
                ).convert("RGBA")
                s = int((size * 0.75) * scale * 2)
                self._obstacle_hidden_background.thumbnail((s, s), Image.Resampling.LANCZOS)
                self._obstacle_hidden_background = self._obstacle_hidden_background.rotate(-rotation, expand=1)

            background_image = (
                self._obstacle_hidden_background if obstacle.ignore_status == 2 else self._obstacle_background
            )
            bg_size = int((min(background_image.size[1], background_image.size[0]) / scale / 4) * 1.25)
            offset = int(-(size * (0.15 if obstacle.ignore_status == 2 else 0.2)) * scale)

            p = obstacle.to_img(dimensions)
            x = p.x
            y = p.y
            # if self.icon_set != 2:
            pos_offset = (
                max(background_image.size[1], background_image.size[0])
                * (1.35 if obstacle.ignore_status == 2 else 0.95)
                / scale
                / 2
            )
            # else:
            #    pos_offset = 0

            if rotation == 90:
                y_offset = 0
                x_offset = offset
                x = x + pos_offset
            elif rotation == 180:
                y_offset = offset
                x_offset = 0
                y = y + pos_offset
            elif rotation == 270:
                y_offset = 0
                x_offset = -offset
                x = x - pos_offset
            else:
                x_offset = 0
                y_offset = -offset
                y = y - pos_offset

            new_layer.paste(
                background_image,
                (
                    int(round(x * scale - (background_image.size[0] / 2) + x_offset)),
                    int(round(y * scale - (background_image.size[1] / 2) + y_offset)),
                ),
            )

            if obstacle.ignore_status == 2:
                icon = DreameMowerMapRenderer._set_icon_color(
                    icon,
                    icon_size,
                    (34, 109, 242, 240),
                ).rotate(-rotation, expand=1)
            else:
                draw.ellipse(
                    [
                        (x - bg_size) * scale,
                        (y - bg_size) * scale,
                        (x + bg_size) * scale,
                        (y + bg_size) * scale,
                    ],
                    fill=(
                        (212, 212, 212, 255)
                        if obstacle.ignore_status == 1
                        else (
                            (255, 140, 188, 255)
                            if self.icon_set != 2 and obstacle.type == ObstacleType.PET
                            else self.color_scheme.obstacle_bg
                        )
                    ),
                )
                icon = icon.resize((int(icon_size), int(icon_size))).rotate(-rotation, expand=1)

            new_layer.paste(
                icon,
                (
                    int(round(x * scale - (icon_size / 2))),
                    int(round(y * scale - (icon_size / 2))),
                ),
                icon,
            )

            return new_layer

    def render_cruise_point(self, index, cruise_point, layer_size, dimensions, size, rotation, scale):
        new_layer = Image.new("RGBA", layer_size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(new_layer, "RGBA")
        if cruise_point.type == 1 and self._cruise_path_point_background is None:
            self._cruise_path_point_background = Image.open(
                BytesIO(base64.b64decode(MAP_ICON_CRUISE_POINT_BG_DREAME))
            ).convert("RGBA")
            s = int(size * scale * 3)
            self._cruise_path_point_background.thumbnail((s, s), Image.Resampling.LANCZOS)
            self._cruise_path_point_background = self._cruise_path_point_background.rotate(-rotation, expand=1)

        if cruise_point.type != 1 and self._cruise_point_background is None:
            self._cruise_point_background = Image.open(
                BytesIO(base64.b64decode(MAP_ICON_CRUISE_POINT_DREAME))
            ).convert("RGBA")
            s = int(round(size * scale * 2))
            self._cruise_point_background.thumbnail((s, s), Image.Resampling.LANCZOS)
            self._cruise_point_background = self._cruise_point_background.rotate(-rotation, expand=1)

        background_image = (
            self._cruise_point_background if cruise_point.type != 1 else self._cruise_path_point_background
        )
        bg_size = int(min(background_image.size[1], background_image.size[0]) / scale / 4)
        offset = int(-bg_size * 1.25)

        p = cruise_point.to_img(dimensions)
        x = p.x
        y = p.y
        pos_offset = (
            max(background_image.size[1], background_image.size[0])
            * (1.75 if cruise_point.type != 1 else 1.20)
            / scale
            / 3
        )

        if rotation == 90:
            y_offset = 0
            x_offset = offset
            x = x + pos_offset
        elif rotation == 180:
            y_offset = offset
            x_offset = 0
            y = y + pos_offset
        elif rotation == 270:
            y_offset = 0
            x_offset = -offset
            x = x - pos_offset
        else:
            x_offset = 0
            y_offset = -offset
            y = y - pos_offset

        new_layer.paste(
            background_image,
            (
                int(round(x * scale - (background_image.size[0] / 2) + x_offset)),
                int(round(y * scale - (background_image.size[1] / 2) + y_offset)),
            ),
        )

        if cruise_point.type == 1:
            draw.ellipse(
                [
                    (x - bg_size) * scale,
                    (y - bg_size) * scale,
                    (x + bg_size) * scale,
                    (y + bg_size) * scale,
                ],
                fill=(212, 212, 212, 255) if cruise_point.completed else (34, 109, 242, 255),
            )

        if cruise_point.type == 1:
            text_box = Image.new("RGBA", (bg_size * 2 * scale, bg_size * 2 * scale), (255, 255, 255, 0))
            text_box_draw = ImageDraw.Draw(text_box, "RGBA")

            if self._font_file is None:
                self._font_file = zlib.decompress(base64.b64decode(MAP_FONT), zlib.MAX_WBITS | 32)

            font = ImageFont.truetype(BytesIO(self._font_file), int((bg_size * 1.5 * scale)))

            text = str(index)
            left, top, tw, th = text_box_draw.textbbox((0, 0), text, font)
            text_box_draw.text(
                (
                    (text_box.size[1] - tw) / 2,
                    (text_box.size[1] - th - int(round(size * 0.4))) / 2,
                ),
                text,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=1,
                stroke_fill=(255, 255, 255, 100),
            )
            text_box = text_box.rotate(-rotation, expand=1)
            new_layer.paste(
                text_box,
                (int(round((x - bg_size) * scale)), int(round((y - bg_size) * scale))),
                text_box,
            )

        return new_layer

    def render_furniture(self, furniture, furniture_version, layer_size, dimensions, size, rotation, scale):
        draw_image = furniture.width and furniture.height
        furniture_type = (
            FurnitureType.COFFEE_TABLE.value
            if furniture_version == 1 and furniture.type == FurnitureType.ROUND_COFFEE_TABLE
            else furniture.type.value
        )
        if draw_image:
            furniture_images = FURNITURE_V2_TYPE_TO_IMAGE if furniture_version == 2 else FURNITURE_TYPE_TO_IMAGE
            if furniture_type not in self._furniture_images and furniture_type in furniture_images:
                img = np.array(Image.open(BytesIO(base64.b64decode(furniture_images[furniture_type]))).convert("RGBA"))
                img[..., 3] = 235 * (img[..., 3] > 0)
                self._furniture_images[furniture_type] = Image.fromarray(img)
            icon = self._furniture_images.get(furniture_type)
        else:
            furniture_icons = FURNITURE_V2_TYPE_TO_ICON if furniture_version == 2 else FURNITURE_TYPE_TO_ICON
            if furniture_type not in self._furniture_icons and furniture_type in furniture_icons:
                self._furniture_icons[furniture_type] = Image.open(
                    BytesIO(base64.b64decode(furniture_icons[furniture_type]))
                ).convert("RGBA")
            icon = self._furniture_icons.get(furniture_type)
        if icon:
            new_layer = Image.new("RGBA", layer_size, (255, 255, 255, 0))
            if draw_image:
                w = (furniture.width / dimensions.grid_size) * dimensions.scale
                h = (furniture.height / dimensions.grid_size) * dimensions.scale
                p = Point(
                    furniture.x,
                    furniture.y,
                ).to_img(dimensions)
                x = p.x
                y = p.y

                img = icon.rotate(furniture.angle, expand=1)
                if furniture_version == 2:
                    img = img.resize(
                        (int(w * scale), int(h * scale)),
                        resample=Image.Resampling.LANCZOS,
                    )
                else:
                    img.thumbnail((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
                img = img.rotate(-(furniture.angle * 2), expand=1)

                new_layer.paste(
                    img,
                    (
                        int((x * scale) - ((img.size[0]) / 2)),
                        int((y * scale) - ((img.size[1]) / 2)),
                    ),
                    img,
                )
            else:
                icon_size = size * scale * 1.15
                if self._furniture_background is None:
                    self._furniture_background = Image.open(
                        BytesIO(base64.b64decode(MAP_ICON_OBSTACLE_BG_DREAME))
                    ).convert("RGBA")
                    s = int(size * scale * 2)
                    self._furniture_background.thumbnail((s, s), Image.Resampling.LANCZOS)
                    self._furniture_background = self._furniture_background.rotate(-rotation, expand=1)

                offset = int(-(size * 0.2) * scale)

                p = furniture.to_img(dimensions)
                x = p.x
                y = p.y
                pos_offset = (
                    (self._furniture_background.size[1] * (1.15 if rotation == 90 or rotation == 270 else 0.9))
                    / scale
                    / 2
                )

                if rotation == 90:
                    y_offset = 0
                    x_offset = offset
                    x = x + pos_offset
                elif rotation == 180:
                    y_offset = offset
                    x_offset = 0
                    y = y + pos_offset
                elif rotation == 270:
                    y_offset = 0
                    x_offset = -offset
                    x = x - pos_offset
                else:
                    x_offset = 0
                    y_offset = -offset
                    y = y - pos_offset

                new_layer.paste(
                    self._furniture_background,
                    (
                        int(round(x * scale - (self._furniture_background.size[0] / 2) + x_offset)),
                        int(round(y * scale - (self._furniture_background.size[1] / 2) + y_offset)),
                    ),
                )

                icon = icon.resize((int(icon_size), int(icon_size))).rotate(-rotation, expand=1)

                new_layer.paste(
                    icon,
                    (
                        int(round(x * scale - (icon_size / 2))),
                        int(round(y * scale - (icon_size / 2))),
                    ),
                    icon,
                )

            return new_layer

    def render_router(
        self,
        router_position,
        layer_size,
        dimensions,
        size,
        rotation,
        scale,
    ):
        new_layer = Image.new("RGBA", layer_size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(new_layer, "RGBA")
        icon_size = int(size * scale)
        if self._wifi_icon is None:
            self._wifi_icon = (
                Image.open(BytesIO(base64.b64decode(MAP_WIFI_IMAGE_DREAME)))
                .convert("RGBA")
                .resize((icon_size, icon_size), resample=Image.Resampling.NEAREST)
            )

        point = router_position.to_img(dimensions)
        bg_size = (size * 1.2) / 2
        draw.ellipse(
            [
                int((point.x - bg_size) * scale),
                int((point.y - bg_size) * scale),
                int((point.x + bg_size) * scale),
                int((point.y + bg_size) * scale),
            ],
            fill=(34, 98, 211, 255) if self.color_scheme.dark else (34, 109, 242, 255),
        )
        wifi_icon = self._wifi_icon.rotate(-rotation, expand=1)
        new_layer.paste(
            wifi_icon,
            (
                int((point.x * scale) - (wifi_icon.size[0] / 2)),
                int((point.y * scale) - (wifi_icon.size[1] / 2)),
            ),
            wifi_icon,
        )

        return new_layer

    def render_floor_material(self, image, floor_material, pixel_type, color, dimensions, scale):
        tile_w = 12
        floor_w = 4
        floor_h = 16

        height = dimensions.height * scale
        tiles = {}
        for k, v in floor_material.items():
            if v > 0 and v < 4:
                if v not in tiles:
                    tiles[v] = [k]
                else:
                    tiles[v].append(k)

        if tiles:
            color_map = {}
            for floor_type, tile in tiles.items():
                if tile:
                    if floor_type == 1:
                        w = math.floor(2 * dimensions.width / floor_h)
                        h = math.floor(dimensions.height / floor_w)
                        y_start = 1
                        x_start = 0
                        x_multiplier = floor_h / 2
                        y_multiplier = floor_w
                    elif floor_type == 2:
                        w = math.floor(dimensions.width / floor_w)
                        h = math.floor(2 * dimensions.height / floor_h)
                        y_start = 0
                        x_start = 1
                        x_multiplier = floor_w
                        y_multiplier = floor_h / 2
                    else:
                        w = math.floor(dimensions.width / tile_w)
                        h = math.floor(dimensions.height / tile_w)
                        y_start = 0
                        x_start = 0
                        x_multiplier = tile_w
                        y_multiplier = tile_w

                    for x in range(1, w + 1):
                        for y in range(y_start, dimensions.height):
                            xx = int(x * x_multiplier)
                            if xx < dimensions.width and (
                                floor_type != 1
                                or (
                                    (math.floor((y - 1) / floor_w) % 2 == 0 and x % 2 == 0)
                                    or (math.floor((y - 1) / floor_w) % 2 == 1 and x % 2 == 1)
                                )
                            ):
                                val = int(pixel_type[xx, y])
                                if val > 0 and val < 63 and val in tile:
                                    x_index = (xx * scale) + 1
                                    y_index = (height - 1) - (y * scale) - 1

                                    if val not in color_map:
                                        cc = DreameMowerMapRenderer._alpha_composite(color, image[y_index, x_index])
                                        color_map[val] = cc
                                    else:
                                        cc = color_map[val]
                                    image[y_index, x_index] = cc
                                    y_index = y_index + 1
                                    image[y_index, x_index] = cc

                    for x in range(x_start, dimensions.width):
                        for y in range(1, h + 1):
                            yy = int(y * y_multiplier)
                            if yy < dimensions.height and (
                                floor_type != 2
                                or (
                                    (math.floor((x - 1) / floor_w) % 2 == 0 and y % 2 == 0)
                                    or (math.floor((x - 1) / floor_w) % 2 == 1 and y % 2 == 1)
                                )
                            ):
                                val = int(pixel_type[x, yy])
                                if val > 0 and val < 63 and val in tile:
                                    x_index = x * scale
                                    y_index = (height - 1) - ((yy * scale) + 1)
                                    if val not in color_map:
                                        cc = DreameMowerMapRenderer._alpha_composite(color, image[y_index, x_index])
                                        color_map[val] = cc
                                    else:
                                        cc = color_map[val]
                                    image[y_index, x_index] = cc
                                    x_index = x_index + 1
                                    image[y_index, x_index] = cc
            return image

    def render_neglected_segments(
        self,
        neglected_segments,
        segments,
        layer_size,
        segment_mask,
        dimensions,
        rotation,
        cleaning_map,
    ):
        mask_layer = Image.new("RGBA", layer_size, (255, 255, 255, 0))
        mask_layer.paste(segment_mask, (0, 0))

        if self._map_problem_icon is None:
            self._map_problem_icon = Image.open(BytesIO(base64.b64decode(MAP_ICON_PROBLEM))).convert("RGBA")

        if rotation == 0 or rotation == 180 or self._square:
            width = (dimensions.width) + (
                (dimensions.padding[0] + dimensions.padding[2] - dimensions.crop[0] - dimensions.crop[2])
                / dimensions.scale
            )
            icon_size = width * (0.06 if self._square else 0.07) * dimensions.scale
        else:
            height = (dimensions.height) + (
                (dimensions.padding[1] + dimensions.padding[3] - dimensions.crop[1] - dimensions.crop[3])
                / dimensions.scale
            )
            icon_size = height * 0.07 * dimensions.scale

        if cleaning_map:
            icon_size = int(icon_size * 0.7)

        problem_icon = self._map_problem_icon.resize((int(icon_size), int(icon_size))).rotate(-rotation, expand=1)

        mask_layer.paste(segment_mask, (0, 0))
        for k in neglected_segments.keys():
            if k in segments:
                segment = segments[k]
                p = Point(segment.x, segment.y).to_img(dimensions, False)
                mask_layer.paste(
                    problem_icon,
                    (
                        int(p.x - (problem_icon.size[0] / 2)),
                        int(p.y - (problem_icon.size[1] / 2)),
                    ),
                    mask=problem_icon,
                )

        return mask_layer

    def get_resources(self, capability) -> MapRendererResources:
        if self.icon_set == 2:
            if self._robot_type == RobotType.VSLAM:
                robot_image = MAP_ROBOT_VSLAM_IMAGE_MIJIA
            else:
                robot_image = MAP_ROBOT_LIDAR_IMAGE_MIJIA
        else:
            if self._robot_type == RobotType.VSLAM:
                if self.icon_set == 3:
                    robot_image = MAP_ROBOT_VSLAM_IMAGE_DREAME_LIGHT
                else:
                    robot_image = MAP_ROBOT_VSLAM_IMAGE_DREAME_DARK
            else:
                if self.icon_set == 3:
                    robot_image = MAP_ROBOT_LIDAR_IMAGE_DREAME_LIGHT
                else:
                    robot_image = MAP_ROBOT_LIDAR_IMAGE_DREAME_DARK

        if self.icon_set == 3:
            charger_image = MAP_CHARGER_IMAGE_MATERIAL
        elif self.icon_set == 2:
            charger_image = MAP_CHARGER_IMAGE_MIJIA
        else:
            if self._robot_type == RobotType.VSLAM:
                charger_image = MAP_CHARGER_VSLAM_IMAGE_DREAME
            else:
                charger_image = MAP_CHARGER_IMAGE_DREAME

        icon_set = SEGMENT_ICONS_DREAME
        if self.icon_set == 1:
            icon_set = SEGMENT_ICONS_DREAME_OLD
        elif self.icon_set == 2:
            icon_set = SEGMENT_ICONS_MIJIA
        elif self.icon_set == 3:
            icon_set = SEGMENT_ICONS_MATERIAL

        if self.icon_set == 2:
            repeats = MAP_ICON_REPEATS_MIJIA
            cleaning_mode = MAP_ICON_CLEANING_MODE_MIJIA
        elif self.icon_set == 3:
            repeats = MAP_ICON_REPEATS_MATERIAL
            cleaning_mode = MAP_ICON_CLEANING_MODE_MATERIAL
        else:
            repeats = MAP_ICON_REPEATS_DREAME
            cleaning_mode = MAP_ICON_CLEANING_MODE_DREAME

        if self._light_font_file is None:
            self._light_font_file = zlib.decompress(base64.b64decode(MAP_FONT_LIGHT), zlib.MAX_WBITS | 32)

        resources = MapRendererResources(
            icon_set=self.icon_set,
            robot_type=self._robot_type.value,
            robot=robot_image,
            charger=charger_image,
            charging=MAP_ROBOT_CHARGING_IMAGE,
            cleaning=MAP_ROBOT_CLEANING_IMAGE,
            warning=MAP_ROBOT_WARNING_IMAGE,
            sleeping=MAP_ROBOT_SLEEPING_IMAGE,
            cleaning_direction=MAP_ROBOT_CLEANING_DIRECTION_IMAGE,
            selected_segment=MAP_ICON_SELECTED_SEGMENT,
            cruise_point_background=MAP_ICON_CRUISE_POINT_DREAME,
            segment={
                k: {
                    "name": SEGMENT_TYPE_CODE_TO_NAME.get(k),
                    "icon": v,
                    "mdi": SEGMENT_TYPE_CODE_TO_HA_ICON.get(k, "mdi:home-outline"),
                }
                for k, v in icon_set.items()
            },
            default_map_image=DEFAULT_MAP_IMAGE,
            font=base64.b64encode(self._light_font_file).decode("utf-8"),
            rotate=MAP_ICON_ROTATE,
            delete=MAP_ICON_DELETE,
            resize=MAP_ICON_RESIZE,
            move=MAP_ICON_MOVE,
            problem=MAP_ICON_PROBLEM,
        )

        if capability.customized_cleaning:
            resources.repeats = repeats
            if capability.custom_cleaning_mode:
                resources.cleaning_mode = cleaning_mode
                if capability.cleaning_route:
                    resources.cleaning_route = (
                        MAP_ICON_CLEANING_ROUTE_MATERIAL if self.icon_set == 3 else MAP_ICON_CLEANING_ROUTE_DREAME
                    )

        if capability.wifi_map:
            resources.wifi = MAP_WIFI_IMAGE_DREAME

        if capability.camera_streaming:
            resources.cruise_path_point_background = MAP_ICON_CRUISE_POINT_BG_DREAME
            resources.obstacle_background = MAP_ICON_OBSTACLE_BG_DREAME
            resources.obstacle_hidden_background = MAP_ICON_OBSTACLE_HIDDEN_BG_DREAME
            resources.obstacle = {
                i.value: {
                    "name": i.name.replace("_", " ").capitalize(),
                    "icon": OBSTACLE_TYPE_TO_ICON.get(i.value),
                    "hidden_icon": OBSTACLE_TYPE_TO_HIDDEN_ICON.get(i.value),
                }
                for i in ObstacleType
            }
            furniture_types = [i for i in FurnitureType]
            if not capability.pet_furniture:
                furniture_types = list(
                    set(furniture_types)
                    - set(
                        [
                            FurnitureType.LITTER_BOX,
                            FurnitureType.PET_BED,
                            FurnitureType.FOOD_BOWL,
                            FurnitureType.PET_TOILET,
                            FurnitureType.ENCLOSED_LITTER_BOX,
                        ]
                    )
                )

            if not capability.extended_furnitures:
                furniture_types = list(set(furniture_types) - set([i for i in FurnitureType if i.value > 13]))

            if capability.new_furnitures:
                resources.furniture = {
                    i.value: {
                        "name": i.name.replace("_", " ").capitalize(),
                        "icon": FURNITURE_V2_TYPE_TO_ICON.get(i.value),
                        "image": FURNITURE_V2_TYPE_TO_IMAGE.get(i.value),
                        "dimensions": FURNITURE_V2_TYPE_TO_DIMENSIONS.get(i.value),
                    }
                    for i in furniture_types
                }
            else:
                resources.furniture = {
                    i.value: {
                        "name": i.name.replace("_", " ").capitalize(),
                        "icon": FURNITURE_TYPE_TO_ICON.get(i.value),
                        "image": FURNITURE_TYPE_TO_IMAGE.get(i.value),
                        "dimensions": FURNITURE_TYPE_TO_DIMENSIONS.get(i.value),
                    }
                    for i in furniture_types
                }

        return resources

    @property
    def calibration_points(self) -> dict[str, int]:
        return self._calibration_points

    @property
    def default_map_image(self) -> bytes:
        if self._default_map_image is None:
            default_map_image = Image.open(BytesIO(base64.b64decode(DEFAULT_MAP_IMAGE))).convert("RGBA")
            self._default_map_image = ImageOps.expand(
                default_map_image.resize(
                    (
                        int(default_map_image.size[0] * 0.8),
                        int(default_map_image.size[1] * 0.8),
                    )
                ),
                border=(50, 75, 50, 75),
            )
        return self._to_buffer(self._default_map_image)

    @property
    def disconnected_map_image(self) -> bytes:
        if self._image:
            return self._to_buffer(self._image.filter(ImageFilter.GaussianBlur(7 if self._low_resolution else 13)))
        return self.default_map_image

    @property
    def default_calibration_points(self) -> dict[str, int]:
        return self._default_calibration_points
