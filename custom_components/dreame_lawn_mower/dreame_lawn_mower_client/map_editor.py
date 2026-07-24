"""Legacy map editing operations and map mutation coordination."""

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
from .resources import *
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


class DreameMapMowerMapEditor:
    """Every map change must be handled on memory before actually requesting it to the device because it takes too much time to get the updated map from the cloud.
    This class handles user edits on stored map data like updating customized cleaning settings or setting active segments on segment cleaning.
    Original app has a similar class to handle the same issue (Works optimistically)"""

    def __init__(self, map_manager) -> None:
        self.map_manager = map_manager

    def _set_updated_frame_id(self, frame_id) -> None:
        self.map_manager._updated_frame_id = frame_id

    def refresh_map(self, map_id: int = None) -> None:
        if map_id:
            if self._saved_map_data and map_id in self._saved_map_data:
                self._saved_map_data[map_id].last_updated = time.time()
                self.map_manager._map_data_updated()
            return
        if self._map_data is not None:
            self._map_data.last_updated = time.time()
            self.map_manager._map_data_updated()

    def set_active_areas(self, active_areas: list[list[int]]) -> None:
        map_data = self._map_data
        if map_data is not None:
            map_data.active_cruise_points = None
            map_data.active_areas = []
            for area in active_areas:
                x_coords = sorted([area[0], area[2]])
                y_coords = sorted([area[1], area[3]])
                map_data.active_areas.append(
                    Area(
                        x_coords[0],
                        y_coords[0],
                        x_coords[1],
                        y_coords[0],
                        x_coords[1],
                        y_coords[1],
                        x_coords[0],
                        y_coords[1],
                    )
                )
            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()

    def set_active_segments(self, active_segments: list[int]) -> None:
        map_data = self._map_data
        if map_data is not None:
            map_data.active_segments = active_segments
            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()

    def set_active_points(self, active_points: list[list[int]]) -> None:
        map_data = self._map_data
        if map_data is not None:
            map_data.active_points = []
            for point in active_points:
                map_data.active_points.append(
                    Point(
                        point[0],
                        point[1],
                    )
                )
            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()

    def set_cruise_points(self, active_cruise_points: list[list[int]]) -> None:
        map_data = self._map_data
        if map_data is not None:
            map_data.active_cruise_points = {}
            index = 0
            if active_cruise_points:
                map_data.path = None
                map_data.obstacles = None
                map_data.active_areas = None
                map_data.active_segments = None
                for point in active_cruise_points:
                    index = index + 1
                    map_data.active_cruise_points[index] = Coordinate(
                        point[0],
                        point[1],
                        bool(point[2]),
                        point[3],
                    )
            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()

    def clear_path(self) -> None:
        map_data = self._map_data
        if map_data is not None:
            map_data.path = None
            # map_data.obstacles = None
            # map_data.active_cruise_points = None
            map_data.active_areas = None
            map_data.active_segments = None
            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()

    def reset_map(self) -> None:
        map_data = self._map_data
        if map_data is not None:
            map_data.dimensions.width = 0
            map_data.dimensions.height = 0
            map_data.segments = {}
            map_data.floor_material = None
            map_data.hidden_segments = None
            map_data.path = None
            map_data.obstacles = None
            map_data.empty_map = True
            map_data.saved_map_status = 0
            self._set_updated_frame_id(map_data.frame_id + 1)
            self.refresh_map()

    def set_rotation(self, map_id: int, rotation: int) -> None:
        if map_id in self._saved_map_data:
            self._saved_map_data[map_id].rotation = rotation
            DreameMowerMapDecoder.set_floor_material(self._saved_map_data[map_id])
            if self._map_data is not None and map_id == self._selected_map_id:
                self._map_data.rotation = rotation
                DreameMowerMapDecoder.set_floor_material(self._map_data)
                self.refresh_map()
            self.refresh_map(map_id)

    def set_map_name(self, map_id: int, name: str) -> None:
        if map_id in self._saved_map_data:
            self._saved_map_data[map_id].custom_name = name
            self._saved_map_data[map_id].map_name = name
            self.refresh_map(map_id)
            self.refresh_map()

    def set_selected_map(self, map_id: int) -> None:
        if map_id != self._selected_map_id:
            self.set_current_map(map_id)

    def set_current_map(self, map_id: int) -> None:
        if map_id and map_id in self._saved_map_data:
            saved_map_data = copy.deepcopy(self._saved_map_data[map_id])
            saved_map_data.docked = self._map_data.docked
            saved_map_data.timestamp_ms = self._current_timestamp_ms
            saved_map_data.frame_id = None
            saved_map_data.map_name = None
            saved_map_data.map_index = 0
            saved_map_data.custom_name = None
            saved_map_data.saved_map = False
            saved_map_data.restored_map = True
            saved_map_data.temporary_map = False
            saved_map_data.empty_map = False
            saved_map_data.saved_map_status = 2
            DreameMowerMapDecoder.set_segment_cleanset(
                saved_map_data,
                saved_map_data.cleanset,
                self.map_manager._capability,
            )
            self.map_manager._map_data = saved_map_data
            self.map_manager._current_frame_id = None
            self.map_manager._current_map_id = map_id
            self.map_manager._selected_map_id = map_id
            self.refresh_map()

    def set_pathways(self, pathways) -> None:
        map_data = self._map_data
        if not map_data or not self._selected_map_id or map_data.pathways is None:
            return

        map_data.pathways = []
        if pathways:
            for line in pathways:
                map_data.pathways.append(
                    Wall(
                        line[0],
                        line[1],
                        line[2],
                        line[3],
                    )
                )

        self._saved_map_data[self._selected_map_id].pathways = map_data.pathways
        self._set_updated_frame_id(map_data.frame_id)
        self.refresh_map(self._selected_map_id)
        self.refresh_map()
        return

    def set_predefined_points(self, predefined_points) -> None:
        map_data = self._map_data
        if not map_data or not self._selected_map_id or map_data.predefined_points is None:
            return

        map_data.predefined_points = {}
        index = 0
        if predefined_points:
            for point in predefined_points:
                index = index + 1
                map_data.predefined_points[index] = Coordinate(
                    point[0],
                    point[1],
                    bool(point[2]),
                    point[3],
                )

        self._saved_map_data[self._selected_map_id].predefined_points = map_data.predefined_points
        self._set_updated_frame_id(map_data.frame_id)
        self.refresh_map(self._selected_map_id)
        self.refresh_map()
        return

    def set_obstacle_ignore(self, x, y, obstacle_ignored):
        map_data = self._map_data
        if not map_data or not map_data.obstacles:
            return

        for k, v in map_data.obstacles.items():
            if int(v.x) == int(x) and int(v.y) == int(y):
                map_data.obstacles[k].ignore_status = (
                    ObstacleIgnoreStatus.MANUALLY_IGNORED
                    if bool(obstacle_ignored)
                    else ObstacleIgnoreStatus.NOT_IGNORED
                )
                break

        self._set_updated_frame_id(map_data.frame_id)
        self.refresh_map()
        return

    def set_router_position(self, x, y):
        map_data = self._map_data
        if not map_data or not self._selected_map_id or map_data.router_position is None:
            return

        router_position = Point(int(x), int(y))
        self._saved_map_data[self._selected_map_id].router_position = router_position
        if self._saved_map_data[self._selected_map_id].wifi_map_data:
            self._saved_map_data[self._selected_map_id].wifi_map_data.router_position = router_position
        map_data.router_position = router_position
        if map_data.wifi_map_data:
            map_data.wifi_map_data.router_position = router_position
        self._set_updated_frame_id(map_data.frame_id)
        self.refresh_map(self._selected_map_id)
        self.refresh_map()
        return

    def delete_map(self, map_id: int = None) -> None:
        map_data = self._map_data
        if map_data and map_data.temporary_map:
            return

        if map_id is None:
            self.map_manager._map_data = None
            self.map_manager._selected_map_id = None
            self.map_manager._updated_frame_id = None
            self.map_manager._saved_map_data = {}
            self.map_manager._refresh_map_list()
            self.map_manager.request_next_map_list()
        else:
            if self._saved_map_data and map_id not in self._saved_map_data:
                self.map_manager.schedule_update(2)
                return

            if map_data and self._selected_map_id == map_id:
                if len(self.map_manager._map_list) > 1:
                    self.set_current_map(self.map_manager._map_list[-1])
                else:
                    self.map_manager._map_data = None
                    self._updated_frame_id = None
                    self._selected_map_id = None

            del self.map_manager._saved_map_data[map_id]
            self.map_manager._refresh_map_list()
            self.map_manager.request_next_map_list()

    def merge_segments(self, map_id: int, segments: list[int]) -> None:
        saved_map_data = self._saved_map_data
        if saved_map_data and map_id in saved_map_data and len(segments) == 2:
            map_data = saved_map_data[map_id]
            if map_data.segments and segments[0] in map_data.segments and segments[1] in map_data.segments:
                if segments[1] not in map_data.segments[segments[0]].neighbors:
                    _LOGGER.error("Segments are not neighbors with each other: %s", segments)
                    return

                data = np.zeros((map_data.dimensions.width * map_data.dimensions.height), np.uint8)
                for y in range(map_data.dimensions.height):
                    for x in range(map_data.dimensions.width):
                        index = y * map_data.dimensions.width + x
                        if (map_data.data[index] & 0x3F) == segments[1]:
                            data[index] = segments[0]
                        else:
                            data[index] = map_data.data[index]

                        if int(map_data.pixel_type[x, y]) == segments[1]:
                            map_data.pixel_type[x, y] = segments[0]

                map_data.data = bytes(data)
                del self.map_manager._saved_map_data[map_id].segments[segments[1]]
                new_segments = DreameMowerMapDecoder.get_segments(map_data, self.map_manager._vslam_map)
                map_data.segments[segments[0]].x = new_segments[segments[0]].x
                map_data.segments[segments[0]].y = new_segments[segments[0]].y
                if map_data.hidden_segments and segments[1] in map_data.hidden_segments:
                    map_data.hidden_segments.remove(segments[1])

                DreameMowerMapDecoder.set_floor_material(map_data)
                for k, v in map_data.segments.items():
                    if segments[1] in v.neighbors:
                        map_data.segments[k].neighbors.remove(segments[1])

                DreameMowerMapDecoder.set_segment_color_index(map_data)
                if self._map_data and map_id == self._selected_map_id:
                    self.set_current_map(map_id)
                self.refresh_map(map_id)

    def split_segments(self, map_id: int, segment: int, line: list[int]) -> None:
        if self._saved_map_data and map_id in self._saved_map_data:
            if self._map_data and map_id == self._selected_map_id:
                self.set_current_map(map_id)
            self.refresh_map(map_id)

    def save_temporary_map(self) -> None:
        if self._map_data and self._map_data.temporary_map:
            self._map_data.temporary_map = False
            self.refresh_map()
            self.map_manager.request_next_map_list()

    def discard_temporary_map(self) -> None:
        if self._map_data and self._map_data.temporary_map and self._selected_map_id:
            self.set_current_map(self._selected_map_id)
            self.map_manager.request_next_map_list()

    def replace_temporary_map(self, map_id: int = None) -> None:
        map_data = self._map_data
        if map_data and map_data.temporary_map:
            if not map_id and self._selected_map_id:
                map_id = self._selected_map_id

            if map_id in self._saved_map_data:
                new_map = copy.deepcopy(map_data)
                new_map.map_id = new_map.saved_map_id
                new_map.saved_map_id = None
                new_map.saved_map_status = -1
                new_map.saved_map = True
                new_map.cleanset = {}
                self.map_manager._saved_map_data[new_map.map_id] = new_map
                del self.map_manager._saved_map_data[map_id]
                self.map_manager._refresh_map_list()

                map_data.saved_map_id = new_map.map_id
                map_data.temporary_map = False
                map_data.saved_map = False
                map_data.saved_map_status = 0
                map_data.restored_map = True
                map_data.empty_map = False
                map_data.cleanset = {}
                DreameMowerMapDecoder.set_segment_cleanset(map_data, map_data.cleanset, self.map_manager._capability)
                self.map_manager._map_data = map_data
                self.map_manager._selected_map_id = new_map.map_id
                self.map_manager.request_next_map_list()
                self.refresh_map()

    def restore_map(self, recovery_map_info: RecoveryMapInfo) -> None:
        if recovery_map_info and recovery_map_info.map_id in self.map_manager._map_list:
            self.map_manager.schedule_update(15)
            recovery_map_data = (
                (
                    DreameMowerMapDecoder.decode_saved_map(
                        recovery_map_info.raw_map,
                        self.map_manager._vslam_map,
                        self._saved_map_data[recovery_map_info.map_id].rotation,
                        self.map_manager._aes_iv,
                    )
                )
                if recovery_map_info.map_data is None
                else recovery_map_info.map_data
            )
            recovery_map_data.recovery_map = False
            recovery_map_data.saved_map = True
            recovery_map_data.map_name = self._saved_map_data[recovery_map_info.map_id].map_name
            recovery_map_data.custom_name = self._saved_map_data[recovery_map_info.map_id].custom_name
            recovery_map_data.rotation = self._saved_map_data[recovery_map_info.map_id].rotation
            recovery_map_data.map_index = self._saved_map_data[recovery_map_info.map_id].map_index
            recovery_map_data.recovery_map_list = self._saved_map_data[recovery_map_info.map_id].recovery_map_list
            recovery_map_data.timestamp_ms = self._saved_map_data[recovery_map_info.map_id].timestamp_ms
            recovery_map_data.last_updated = time.time()
            if recovery_map_data.wifi_map:
                recovery_map_data.wifi_map.last_updated = time.time()

            self._saved_map_data[recovery_map_info.map_id] = recovery_map_data
            self.refresh_map(recovery_map_info.map_id)
            if recovery_map_info.map_id == self._selected_map_id:
                self.set_current_map(recovery_map_info.map_id)
                # self._map_data.restored_map = False
                DreameMowerMapDecoder.set_floor_material(self._map_data)

            self.map_manager._map_request_count = 0
            self.map_manager._map_request_time = None
            self.map_manager._need_map_request = True
            self.map_manager._need_map_list_request = True

    def set_cleaning_sequence(self, cleaning_sequence: list[int]) -> list[int] | None:
        map_data = self._map_data
        if map_data and map_data.segments and not map_data.temporary_map:
            new_cleaning_sequence = []
            if cleaning_sequence:
                for k, v in map_data.segments.items():
                    if k not in cleaning_sequence:
                        map_data.segments[k].order = 0
                        map_data.cleanset[str(k)][3] = 0

                index = 1
                for k in cleaning_sequence:
                    if int(k) in map_data.segments.keys():
                        map_data.segments[k].order = index
                        map_data.cleanset[str(k)][3] = index
                        new_cleaning_sequence.append(k)
                        index = index + 1
            else:
                for k in map_data.segments.keys():
                    map_data.segments[k].order = 0
                    map_data.cleanset[str(k)][3] = 0

            if self._saved_map_data and map_data.map_id in self._saved_map_data:
                self._saved_map_data[map_data.map_id].cleanset = copy.deepcopy(map_data.cleanset)

            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()
            return self.map_manager.cleaning_sequence

    def set_segment_order(self, segment_id: int, order: int) -> list[int] | None:
        map_data = self._map_data
        if map_data and map_data.segments and segment_id in map_data.segments and not map_data.temporary_map:
            if order > 0:
                current_order = map_data.segments[segment_id].order
                if current_order != order:
                    map_data.segments[segment_id].order = order
                    map_data.cleanset[str(segment_id)][3] = order
                    for k, v in map_data.segments.items():
                        if k != segment_id and v.order == order:
                            map_data.segments[k].order = (
                                len(self.map_manager.cleaning_sequence) if not current_order else current_order
                            )
            else:
                map_data.segments[segment_id].order = 0

            index = 1
            for k in self.map_manager.cleaning_sequence:
                if map_data.segments[k].order:
                    map_data.segments[k].order = index
                    map_data.cleanset[str(k)][3] = index
                    index = index + 1
                else:
                    map_data.cleanset[str(k)][3] = 0

            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
            ):
                self._saved_map_data[self._selected_map_id].cleanset = copy.deepcopy(map_data.cleanset)

            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()
            return self.map_manager.cleaning_sequence

    def cleanset(self, map_data: MapData) -> list[list[int]] | None:
        cleanset = []
        has_cleaning_mode = False
        for k, v in map_data.segments.items():
            if v.cleaning_times is None:
                v.cleaning_times = 1

            settings = [
                k,
                v.cleaning_times,
            ]

            if v.cleaning_mode is not None:
                has_cleaning_mode = True

            if has_cleaning_mode:
                settings.append(v.cleaning_mode if v.cleaning_mode is not None else 2)

            cleanset.append(settings)
        return cleanset

    def set_segment_cleaning_times(
        self, segment_id: int, cleaning_times: int, refresh_map: bool = True
    ) -> list[list[int]] | None:
        map_data = self._map_data
        if map_data and map_data.segments and segment_id in map_data.segments and not map_data.temporary_map:
            map_data.segments[segment_id].cleaning_times = cleaning_times
            map_data.cleanset[str(segment_id)][2] = cleaning_times
            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
            ):
                self._saved_map_data[self._selected_map_id].cleanset = copy.deepcopy(map_data.cleanset)
            if refresh_map:
                self._set_updated_frame_id(map_data.frame_id)
                self.refresh_map()
                return self.cleanset(map_data)

    def set_segment_cleaning_mode(
        self, segment_id: int, cleaning_mode: int, refresh_map: bool = True
    ) -> list[list[int]] | None:
        map_data = self._map_data
        if (
            map_data
            and map_data.segments
            and segment_id in map_data.segments
            and not map_data.temporary_map
            and map_data.segments[segment_id].cleaning_mode is not None
        ):
            map_data.segments[segment_id].cleaning_mode = cleaning_mode
            map_data.cleanset[str(segment_id)][4] = cleaning_mode
            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
            ):
                self._saved_map_data[self._selected_map_id].cleanset = copy.deepcopy(map_data.cleanset)
            if refresh_map:
                self._set_updated_frame_id(map_data.frame_id)
                self.refresh_map()
                return self.cleanset(map_data)

    def set_segment_cleaning_route(
        self, segment_id: int, cleaning_route: int, refresh_map: bool = True
    ) -> list[list[int]] | None:
        map_data = self._map_data
        if map_data and map_data.segments and segment_id in map_data.segments:
            if map_data.segments[segment_id].cleaning_route is not None:
                map_data.segments[segment_id].cleaning_route = cleaning_route

            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
            ):
                self._saved_map_data[self._selected_map_id].cleanset = copy.deepcopy(map_data.cleanset)
            if refresh_map:
                self._set_updated_frame_id(map_data.frame_id)
                self.refresh_map()
                return self.cleanset(map_data)

    def set_segment_floor_material(
        self, segment_id: int, floor_material: int, direction: int = None
    ) -> list[list[int]] | None:
        map_data = self._map_data
        if map_data and map_data.segments and segment_id in map_data.segments and not map_data.temporary_map:
            if direction is not None:
                if floor_material != 1:
                    direction = None
                elif map_data.rotation == 90 or map_data.rotation == 270:
                    direction = 0 if direction else 90

            map_data.segments[segment_id].floor_material = floor_material
            map_data.segments[segment_id].floor_material_direction = direction
            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
                and segment_id in self._saved_map_data[self._selected_map_id].segments
            ):
                self._saved_map_data[self._selected_map_id].segments[segment_id].floor_material = floor_material
                self._saved_map_data[self._selected_map_id].segments[segment_id].floor_material_direction = direction
                DreameMowerMapDecoder.set_segment_floor_material(
                    self._saved_map_data[self._selected_map_id],
                    segment_id,
                    self._saved_map_data[self._selected_map_id].floor_material,
                )
                self.refresh_map(self._selected_map_id)

            DreameMowerMapDecoder.set_segment_floor_material(map_data, segment_id, map_data.floor_material)
            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()
            return {
                str(k): (
                    {
                        "material": v.floor_material,
                        "direction": v.floor_material_direction,
                    }
                    if v.floor_material_direction is not None
                    else {"material": v.floor_material}
                )
                for k, v in map_data.segments.items()
            }
        return {}

    def set_segment_visibility(self, segment_id: int, visibility: int) -> list[list[int]] | None:
        map_data = self._map_data
        if map_data and map_data.segments and segment_id in map_data.segments and not map_data.temporary_map:
            map_data.segments[segment_id].visibility = visibility
            map_data.hidden_segments = [k for k, v in map_data.segments.items() if v.visibility == False]
            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
            ):
                self._saved_map_data[self._selected_map_id].segments[segment_id].visibility = visibility
                self._saved_map_data[self._selected_map_id].hidden_segments = [
                    k for k, v in self._saved_map_data[self._selected_map_id].segments.items() if v.visibility == False
                ]

            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()
            return map_data.hidden_segments
        return []

    def set_segment_name(self, segment_id: int, segment_type: int, custom_name: str = None) -> dict[str, Any] | None:
        map_data = self._map_data
        if (
            map_data
            and map_data.segments
            and segment_id in map_data.segments
            and self._selected_map_id
            and not map_data.temporary_map
        ):
            if (
                map_data.segments[segment_id].type != segment_type
                or map_data.segments[segment_id].custom_name != custom_name
            ):
                segment_info = {}
                map_data.segments[segment_id].type = segment_type
                if segment_type == 0:
                    map_data.segments[segment_id].index = 0
                    if custom_name is not None:
                        if custom_name == "":
                            custom_name = None
                        map_data.segments[segment_id].custom_name = custom_name
                else:
                    map_data.segments[segment_id].custom_name = None
                    map_data.segments[segment_id].index = map_data.segments[segment_id].next_type_index(
                        segment_type, map_data.segments
                    )

                map_data.segments[segment_id].set_name()

                self._saved_map_data[self._selected_map_id].segments[segment_id].custom_name = map_data.segments[
                    segment_id
                ].custom_name
                self._saved_map_data[self._selected_map_id].segments[segment_id].index = map_data.segments[
                    segment_id
                ].index
                self._saved_map_data[self._selected_map_id].segments[segment_id].type = map_data.segments[
                    segment_id
                ].type
                self._saved_map_data[self._selected_map_id].segments[segment_id].set_name()
                self.refresh_map(self._selected_map_id)

                for k, v in map_data.segments.items():
                    if map_data.segments[k].custom_name is not None:
                        segment_info[k] = {
                            MAP_PARAMETER_NAME: base64.b64encode(
                                map_data.segments[k].custom_name.encode("utf-8")
                            ).decode("utf-8"),
                            MAP_REQUEST_PARAMETER_TYPE: 0,
                            MAP_REQUEST_PARAMETER_INDEX: 0,
                        }
                    elif map_data.segments[k].type:
                        segment_info[k] = {
                            MAP_REQUEST_PARAMETER_TYPE: map_data.segments[k].type,
                            MAP_REQUEST_PARAMETER_INDEX: map_data.segments[k].index,
                        }
                    else:
                        segment_info[k] = {}

                    if map_data.segments[k].unique_id:
                        segment_info[k][MAP_REQUEST_PARAMETER_ZONE_ID] = map_data.segments[k].unique_id

                self._set_updated_frame_id(map_data.frame_id)
                self.refresh_map()
                return segment_info

    def set_zones(self, virtual_walls, no_go_areas) -> None:
        map_data = self._map_data
        if not map_data or not self._selected_map_id:
            return

        map_data.no_go_areas = []
        if no_go_areas:
            for area in no_go_areas:
                x_coords = sorted([area[0], area[2]])
                y_coords = sorted([area[1], area[3]])
                map_data.no_go_areas.append(
                    Area(
                        x_coords[0],
                        y_coords[0],
                        x_coords[1],
                        y_coords[0],
                        x_coords[1],
                        y_coords[1],
                        x_coords[0],
                        y_coords[1],
                    )
                )

        if virtual_walls:
            map_data.virtual_walls = [
                Wall(
                    wall[0],
                    wall[1],
                    wall[2],
                    wall[3],
                )
                for wall in virtual_walls
            ]
        else:
            map_data.virtual_walls = []

        self._set_updated_frame_id(map_data.frame_id)
        if (
            self._saved_map_data
            and self._selected_map_id is not None
            and self._selected_map_id in self._saved_map_data
        ):
            self._saved_map_data[self._selected_map_id].no_go_areas = map_data.no_go_areas
            self._saved_map_data[self._selected_map_id].virtual_walls = map_data.virtual_walls
            self.refresh_map(self._selected_map_id)
        self.refresh_map()

    @property
    def _map_data(self) -> MapData | None:
        return self.map_manager._map_data

    @property
    def _saved_map_data(self) -> MapData | None:
        return self.map_manager._saved_map_data

    @property
    def _selected_map_id(self) -> int | None:
        return self.map_manager._selected_map_id

    @property
    def _current_timestamp_ms(self) -> int | None:
        return self.map_manager._current_timestamp_ms
