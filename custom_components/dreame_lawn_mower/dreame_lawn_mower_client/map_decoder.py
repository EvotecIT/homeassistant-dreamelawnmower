"""Legacy map payload decoding with one focused implementation owner."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import math
import re
import traceback
import zlib
from functools import cmp_to_key
from typing import Any

import numpy as np
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .const import (
    MAP_PARAMETER_NAME,
)
from .device_types import (
    CleansetType,
    DreameMowerDeviceCapability,
    FurnitureType,
    ObstacleType,
    PathType,
)
from .map_types import (
    Area,
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
    Segment,
    StartupMethod,
    TaskEndType,
    Wall,
)

_LOGGER = logging.getLogger(__name__)


class DreameMowerMapDecoder:
    HEADER_SIZE = 27

    @staticmethod
    def _read_int_8(data: bytes, offset: int = 0) -> int:
        return int.from_bytes(data[offset : offset + 1], byteorder="big", signed=True)

    @staticmethod
    def _read_int_8_le(data: bytes, offset: int = 0) -> int:
        return int.from_bytes(
            data[offset : offset + 1], byteorder="little", signed=True
        )

    @staticmethod
    def _read_int_16(data: bytes, offset: int = 0) -> int:
        return int.from_bytes(data[offset : offset + 2], byteorder="big", signed=True)

    @staticmethod
    def _read_int_16_le(data: bytes, offset: int = 0) -> int:
        return int.from_bytes(
            data[offset : offset + 2], byteorder="little", signed=True
        )

    @staticmethod
    def _compare_segment_neighbors(r1: Segment, r2: Segment) -> bool:
        alen = 0
        blen = 0
        if r1.neighbors:
            alen = len(r1.neighbors)
        if r2.neighbors:
            blen = len(r2.neighbors)

        if alen == blen:
            return r1.segment_id - r2.segment_id

        return blen - alen

    @staticmethod
    def _compare_colors(c1: list[int], c2: list[int]) -> bool:
        return c1[1] - c2[1] if c1[1] != c2[1] else c1[0] - c2[0]

    @staticmethod
    def _get_pixel_type(
        map_data: MapData, pixel, vslam_map: bool = False
    ) -> MapPixelType:
        if map_data.frame_map:
            segment_id = pixel >> 2

            if 0 < segment_id < 64:
                if segment_id == 63:
                    return MapPixelType.WALL.value
                if segment_id == 62:
                    return MapPixelType.FLOOR.value
                if segment_id == 61:
                    return MapPixelType.UNKNOWN.value
                return segment_id

            segment_id = pixel & 0b00000011
            # as implemented on the app
            if segment_id == 1 or segment_id == 3:
                return MapPixelType.NEW_SEGMENT.value
            if segment_id == 2:
                return MapPixelType.WALL.value
        elif vslam_map:
            segment_id = pixel & 0x7F
            if segment_id == 1:
                return MapPixelType.NEW_SEGMENT.value
            elif segment_id == 3:
                return MapPixelType.NEW_SEGMENT_UNKNOWN.value
            elif segment_id == 2:
                return MapPixelType.WALL.value
        else:
            if pixel >> 7:
                segment_id = pixel & 0x3F
                return (
                    MapPixelType.HIDDEN_WALL.value
                    if map_data.hidden_segments
                    and segment_id
                    and segment_id in map_data.hidden_segments
                    else MapPixelType.WALL.value
                )

            segment_id = pixel & 0x7F
            if segment_id > 0:
                if map_data.saved_map_status == 1 or map_data.saved_map_status == 0:
                    # as implemented on the app
                    if segment_id == 1 or segment_id == 3:
                        return MapPixelType.NEW_SEGMENT.value
                    if segment_id == 2:
                        return MapPixelType.WALL.value
                    return MapPixelType.OUTSIDE.value

                return segment_id

        return MapPixelType.OUTSIDE.value

    @staticmethod
    def _get_segment_center(
        map_data, segment_id: int, center: int, vertical: bool
    ) -> int | None:
        # Find center point implemented as on the app
        lines = []
        zero_pixels = -1
        segment_pixel = 0
        line = None

        for k in range(
            map_data.dimensions.height if vertical else map_data.dimensions.width
        ):
            pixel_type = (
                map_data.data[
                    (k * map_data.dimensions.width + center)
                    if vertical
                    else (center * map_data.dimensions.width + k)
                ]
                & 0x3F
            )
            if pixel_type == segment_id:
                segment_pixel = k
                zero_pixels = 0
                if line is None:
                    line = [segment_pixel]
            elif pixel_type == 0:
                if zero_pixels >= 0:
                    zero_pixels = zero_pixels + 1
                    if zero_pixels >= 4 and line is not None:
                        line.append(segment_pixel)
                        lines.append(line)
                        line = None
            elif line is not None:
                line.append(segment_pixel)
                lines.append(line)
                line = None

        if line is not None:
            line.append(segment_pixel)
            lines.append(line)
            line = None

        if lines:
            maxLine = lines[0]
            if len(lines) > 1:
                for item in lines[1:]:
                    if item[1] - item[0] > maxLine[1] - maxLine[0]:
                        maxLine = item

            return int(math.ceil((maxLine[1] - maxLine[0]) / 2 + maxLine[0]))
        return None

    @staticmethod
    def decode_map_partial(raw_map, iv=None, key=None) -> MapDataPartial | None:
        _LOGGER.debug("raw_map: %s", raw_map)
        raw_map = raw_map.replace("_", "/").replace("-", "+")

        if len(raw_map) < 3:
            return None

        if "," in raw_map and key is None:
            values = raw_map.split(",")
            key = values[1]
            raw_map = values[0]

        raw_map = base64.decodebytes(raw_map.encode("utf8"))

        if key is not None:
            if iv is None:
                iv = ""
            try:
                cipher = Cipher(
                    algorithms.AES(
                        hashlib.sha256(key.encode()).hexdigest()[0:32].encode("utf8")
                    ),
                    modes.CBC(iv.encode("utf8")),
                    backend=default_backend(),
                )
                decryptor = cipher.decryptor()
                raw_map = decryptor.update(raw_map) + decryptor.finalize()
            except Exception as ex:
                _LOGGER.error(
                    "Map data decryption failed: %s. Private key might be missing. "
                    "Please report this issue with your device model.",
                    ex,
                )
                return None

        try:
            raw_map = zlib.decompress(raw_map)
            if not raw_map or len(raw_map) < DreameMowerMapDecoder.HEADER_SIZE:
                _LOGGER.error("Wrong header size for map")
                return None
        except Exception as ex:
            _LOGGER.error("Map data decompression failed: %s", ex)
            return None

        partial_map = MapDataPartial()
        partial_map.map_id = DreameMowerMapDecoder._read_int_16_le(raw_map)
        partial_map.frame_id = DreameMowerMapDecoder._read_int_16_le(raw_map, 2)
        partial_map.frame_type = DreameMowerMapDecoder._read_int_8(raw_map, 4)
        partial_map.raw = raw_map
        image_size = DreameMowerMapDecoder.HEADER_SIZE + (
            DreameMowerMapDecoder._read_int_16_le(raw_map, 19)
            * DreameMowerMapDecoder._read_int_16_le(raw_map, 21)
        )
        if len(raw_map) >= image_size:
            try:
                data_json = json.loads(raw_map[image_size:].decode("utf8"))
                if data_json.get("timestamp_ms"):
                    partial_map.timestamp_ms = int(data_json["timestamp_ms"])

                partial_map.data_json = data_json
            except Exception:
                pass
        return partial_map

    @staticmethod
    def decode_map(
        raw_map: str,
        vslam_map: bool,
        rotation: int = 0,
        iv: str = None,
        key: str = None,
    ) -> tuple[MapData, MapData | None]:
        return DreameMowerMapDecoder.decode_map_data_from_partial(
            DreameMowerMapDecoder.decode_map_partial(raw_map, iv, key),
            vslam_map,
            rotation,
        )

    @staticmethod
    def decode_saved_map(
        raw_map: str, vslam_map: bool, rotation: int = 0, iv: str = None
    ) -> MapData | None:
        return DreameMowerMapDecoder.decode_map(raw_map, vslam_map, rotation, iv)[0]

    @staticmethod
    def decode_map_data_from_partial(
        partial_map: MapDataPartial, vslam_map: bool, rotation: int = 0
    ) -> MapData | None:
        if partial_map is None:
            return

        map_data = MapData()
        map_data.map_id = partial_map.map_id
        map_data.frame_id = partial_map.frame_id
        map_data.frame_type = partial_map.frame_type
        map_data.timestamp_ms = partial_map.timestamp_ms

        raw = partial_map.raw
        map_data.robot_position = Point(
            DreameMowerMapDecoder._read_int_16_le(raw, 5),
            DreameMowerMapDecoder._read_int_16_le(raw, 7),
            DreameMowerMapDecoder._read_int_16_le(raw, 9),
        )
        map_data.charger_position = Point(
            DreameMowerMapDecoder._read_int_16_le(raw, 11),
            DreameMowerMapDecoder._read_int_16_le(raw, 13),
            DreameMowerMapDecoder._read_int_16_le(raw, 15),
        )

        grid_size = DreameMowerMapDecoder._read_int_16_le(raw, 17)
        width = DreameMowerMapDecoder._read_int_16_le(raw, 19)
        height = DreameMowerMapDecoder._read_int_16_le(raw, 21)
        left = DreameMowerMapDecoder._read_int_16_le(raw, 23)
        top = DreameMowerMapDecoder._read_int_16_le(raw, 25)

        image_size = DreameMowerMapDecoder.HEADER_SIZE + width * height
        data_json = partial_map.data_json
        if data_json is None:
            data_json = {}

        _LOGGER.debug("Map Data Json: %s", data_json)

        try:
            if (
                "origin" in data_json
                and data_json["origin"]
                and len(data_json["origin"]) > 1
            ):
                left = data_json["origin"][0]
                top = data_json["origin"][1]

            map_data.dimensions = MapImageDimensions(
                top, left, height, width, grid_size
            )

            map_data.rotation = rotation

            if map_data.frame_type != MapFrameType.W.value:
                if "mra" in data_json:
                    map_data.rotation = int(data_json["mra"])

                if "cs" in data_json:
                    map_data.cleaned_area = int(data_json["cs"])

                if "ct" in data_json:
                    map_data.cleaning_time = int(data_json["ct"])

                if "wm" in data_json:
                    map_data.work_status = int(data_json["wm"])

                if "cf" in data_json:
                    map_data.completed = bool(data_json["cf"] == 1)

                if "clean_finish_remain_electricity" in data_json:
                    map_data.remaining_battery = int(
                        data_json["clean_finish_remain_electricity"]
                    )

                map_data.customized_cleaning = data_json.get("customeClean")
                map_data.docked = bool("oc" in data_json and data_json["oc"])
                map_data.line_to_robot = bool("l2r" in data_json and data_json["l2r"])
                map_data.frame_map = bool(
                    data_json.get("fsm") and data_json["fsm"] == 1
                )
                map_data.restored_map = bool(
                    data_json.get("rpur") and data_json["rpur"] == 1
                )
                map_data.saved_map_status = -1
                if "ris" in data_json:
                    map_data.saved_map_status = data_json["ris"]
                map_data.clean_log = bool(
                    data_json.get("iscleanlog") and data_json["iscleanlog"] == 1
                )
                map_data.recovery_map = bool("us" in data_json and data_json["us"] == 1)
                map_data.new_map = bool("risp" in data_json and data_json["risp"] == 0)
                if "smd" in data_json:
                    map_data.startup_method = (
                        StartupMethod(data_json["smd"])
                        if data_json["smd"] in StartupMethod._value2member_map_
                        else StartupMethod.OTHER
                    )
                if "ctyi" in data_json:
                    map_data.task_end_type = (
                        TaskEndType(data_json["ctyi"])
                        if data_json["ctyi"] in TaskEndType._value2member_map_
                        else TaskEndType.OTHER
                    )
                map_data.multiple_cleaning_time = data_json.get("multime")
                map_data.dos = data_json.get("dos")
                map_data.temporary_map = bool(
                    data_json.get("suw")
                    and (data_json["suw"] == 6 or data_json["suw"] == 5)
                    and data_json.get("fsm") is None
                )
                map_data.saved_map = bool(
                    map_data.frame_type == MapFrameType.I.value
                    and not map_data.restored_map
                    and not map_data.frame_map
                    and map_data.saved_map_status == -1
                    and not map_data.clean_log
                )

                if (
                    data_json.get("nc") and data_json["nc"]
                ) or map_data.charger_position.a == 32767:
                    map_data.charger_position = None

                if (
                    data_json.get("nr") and data_json["nr"]
                ) or map_data.robot_position.a == 32767:
                    map_data.robot_position = None

                if not map_data.saved_map and not map_data.recovery_map:
                    map_data.index = 0

                if data_json.get("tr"):
                    matches = [
                        m.groupdict()
                        for m in re.compile(
                            r"(?P<operator>[MWSLl])(?P<x>-?\d+),(?P<y>-?\d+)"
                        ).finditer(data_json["tr"])
                    ]
                    current_position = Point(0, 0)
                    map_data.path = []
                    for match in matches:
                        operator = match["operator"]
                        x = int(match["x"])
                        y = int(match["y"])

                        if operator == "L":
                            current_position = Path(
                                current_position.x + x,
                                current_position.y + y,
                                PathType.LINE,
                            )
                        else:
                            # You will only get "l" paths with in a P frame.
                            # The path connects to the previous frame and should
                            # be rendered as a line.
                            if operator == "l":
                                operator = "L"
                            current_position = Path(x, y, PathType(operator))

                        map_data.path.append(current_position)

                if data_json.get("sa") and isinstance(data_json["sa"], list):
                    map_data.active_segments = [sa[0] for sa in data_json["sa"]]

                if "delsr" in data_json:
                    map_data.hidden_segments = data_json["delsr"]

                if data_json.get("da2"):
                    if data_json["da2"].get("areas"):
                        map_data.active_areas = []
                        for area in data_json["da2"]["areas"]:
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

                if data_json.get("sp"):
                    map_data.active_points = []
                    for point in data_json["sp"]:
                        map_data.active_points.append(Point(point[0], point[1]))

                if "cleanset" in data_json:
                    map_data.cleanset = data_json["cleanset"]
                    if isinstance(map_data.cleanset, str):
                        map_data.cleanset = json.loads(map_data.cleanset)
            else:
                map_data.need_optimization = True
                map_data.wifi_map = True

            map_data.empty_map = (
                map_data.frame_type == MapFrameType.I.value
                or map_data.frame_type == MapFrameType.W.value
            )
            if (width * height) > 0:
                map_data.data = raw[DreameMowerMapDecoder.HEADER_SIZE : image_size]
                map_data.empty_map = bool(width == 2 and height == 2)
                if map_data.empty_map:
                    for y in range(height):
                        for x in range(width):
                            if map_data.data[(width * y) + x] > 0:
                                map_data.empty_map = False
                                break

                map_data.pixel_type = np.full(
                    (width, height), MapPixelType.OUTSIDE.value, dtype=np.uint8
                )
                if not map_data.empty_map:
                    map_data.empty_map = True
                    if map_data.frame_type == MapFrameType.W.value:
                        try:
                            for y in range(height):
                                for x in range(width):
                                    pixel = map_data.data[(width * y) + x] & 15
                                    if pixel > 0:
                                        map_data.empty_map = False
                                        map_data.pixel_type[x, y] = MapPixelType(pixel)
                        except Exception:
                            pass
                    elif map_data.frame_type == MapFrameType.I.value:
                        if map_data.frame_map:
                            for y in range(height):
                                for x in range(width):
                                    pixel = map_data.data[(width * y) + x]
                                    if pixel > 0:
                                        map_data.empty_map = False
                                        segment_id = pixel >> 2
                                        if 0 < segment_id < 64:
                                            if segment_id == 63:
                                                map_data.pixel_type[x, y] = (
                                                    MapPixelType.WALL.value
                                                )
                                            elif segment_id == 62:
                                                map_data.pixel_type[x, y] = (
                                                    MapPixelType.FLOOR.value
                                                )
                                            elif segment_id == 61:
                                                map_data.pixel_type[x, y] = (
                                                    MapPixelType.UNKNOWN.value
                                                )
                                            else:
                                                map_data.pixel_type[x, y] = segment_id
                                        else:
                                            segment_id = pixel & 0x3F
                                            if segment_id == 1 or segment_id == 3:
                                                map_data.pixel_type[x, y] = (
                                                    MapPixelType.NEW_SEGMENT.value
                                                )
                                            elif segment_id == 2:
                                                map_data.pixel_type[x, y] = (
                                                    MapPixelType.WALL.value
                                                )
                        elif (
                            map_data.saved_map_status == 1
                            or map_data.saved_map_status == 0
                        ):
                            for y in range(height):
                                for x in range(width):
                                    pixel = map_data.data[(width * y) + x]
                                    if pixel > 0:
                                        segment_id = pixel & 0x3F
                                        # as implemented on the app
                                        if segment_id == 1 or segment_id == 3:
                                            map_data.empty_map = False
                                            map_data.pixel_type[x, y] = (
                                                MapPixelType.NEW_SEGMENT.value
                                            )
                                        elif segment_id == 2:
                                            map_data.empty_map = False
                                            map_data.pixel_type[x, y] = (
                                                MapPixelType.WALL.value
                                            )
                        elif (
                            vslam_map
                            and not map_data.saved_map
                            and not map_data.recovery_map
                        ) or map_data.saved_map_status == 2:
                            for y in range(height):
                                for x in range(width):
                                    segment_id = map_data.data[(width * y) + x] & 0x3F
                                    if segment_id > 0:
                                        map_data.empty_map = False
                                        if segment_id == 2:
                                            map_data.pixel_type[x, y] = (
                                                MapPixelType.WALL.value
                                            )
                                        else:
                                            map_data.pixel_type[x, y] = (
                                                MapPixelType.NEW_SEGMENT.value
                                            )
                        else:
                            for y in range(height):
                                for x in range(width):
                                    pixel = map_data.data[(width * y) + x]
                                    if pixel > 0:
                                        map_data.empty_map = False
                                        segment_id = pixel & 0x3F
                                        if pixel >> 7:
                                            map_data.pixel_type[x, y] = (
                                                MapPixelType.HIDDEN_WALL.value
                                                if map_data.hidden_segments
                                                and segment_id
                                                and segment_id
                                                in map_data.hidden_segments
                                                else MapPixelType.WALL.value
                                            )
                                        else:
                                            if segment_id > 0:
                                                map_data.pixel_type[x, y] = segment_id

                        segments = DreameMowerMapDecoder.get_segments(
                            map_data, vslam_map
                        )
                        if segments and "seg_inf" in data_json:
                            seg_inf = data_json["seg_inf"]
                            for k, _value in segments.items():
                                if seg_inf.get(str(k)):
                                    segment_info = seg_inf[str(k)]
                                    if segment_info.get("nei_id") is not None:
                                        segments[k].neighbors = segment_info["nei_id"]
                                    if segment_info.get("type") is not None:
                                        segments[k].type = segment_info["type"]
                                    if segment_info.get("index") is not None:
                                        segments[k].index = segment_info["index"]
                                    if segment_info.get("zoneID") is not None:
                                        segments[k].unique_id = segment_info["zoneID"]
                                    if segment_info.get("material") is not None:
                                        segments[k].floor_material = segment_info[
                                            "material"
                                        ]
                                    if segment_info.get("direction") is not None:
                                        segments[
                                            k
                                        ].floor_material_direction = segment_info[
                                            "direction"
                                        ]
                                    if segment_info.get(MAP_PARAMETER_NAME):
                                        segments[k].custom_name = base64.b64decode(
                                            segment_info.get(MAP_PARAMETER_NAME)
                                        ).decode("utf-8")
                                    segments[k].visibility = (
                                        bool(k not in map_data.hidden_segments)
                                        if map_data.hidden_segments is not None
                                        else True
                                    )
                                    segments[k].set_name()

                        map_data.segments = segments

            if map_data.wifi_map:
                map_data.robot_position = None
                map_data.data = None
                return map_data, None

            saved_map_data = None
            restored_map = map_data.restored_map

            if "whmp" in data_json:
                router_position = data_json["whmp"]
                if router_position and len(router_position) > 1:
                    map_data.router_position = Point(
                        router_position[0],
                        router_position[1],
                    )

            wifi_map = data_json.get("whm")
            if map_data.saved_map and wifi_map and len(wifi_map) > 1:
                wifi_map_data = DreameMowerMapDecoder.decode_saved_map(
                    data_json["whm"], False, map_data.rotation
                )
                if wifi_map_data:
                    map_data.wifi_map_data = wifi_map_data
                    if map_data.wifi_map_data.router_position is None:
                        map_data.wifi_map_data.router_position = (
                            map_data.router_position
                        )

            if "rism" in data_json:
                saved_map_data = DreameMowerMapDecoder.decode_saved_map(
                    data_json["rism"],
                    vslam_map,
                    map_data.rotation,
                )

                if saved_map_data is not None:
                    saved_map_data.timestamp_ms = map_data.timestamp_ms
                    map_data.saved_map_id = saved_map_data.map_id
                    if saved_map_data.temporary_map:
                        map_data.temporary_map = saved_map_data.temporary_map

                    if (
                        restored_map
                        or map_data.recovery_map
                        or (
                            map_data.saved_map_status == 2
                            and (
                                map_data.empty_map
                                or (not map_data.frame_map and not vslam_map)
                            )
                        )
                    ):
                        map_data.segments = copy.deepcopy(saved_map_data.segments)
                        if saved_map_data.floor_material is not None:
                            map_data.floor_material = copy.deepcopy(
                                saved_map_data.floor_material
                            )
                        if (
                            map_data.hidden_segments is None
                            and saved_map_data.hidden_segments is not None
                        ):
                            map_data.hidden_segments = copy.deepcopy(
                                saved_map_data.hidden_segments
                            )

                        if map_data.saved_map_status == 2 and not map_data.frame_map:
                            left = min(
                                map_data.dimensions.left, saved_map_data.dimensions.left
                            )
                            top = min(
                                map_data.dimensions.top, saved_map_data.dimensions.top
                            )
                            width = int(
                                (
                                    max(
                                        map_data.dimensions.left
                                        + (
                                            map_data.dimensions.width
                                            * map_data.dimensions.grid_size
                                        ),
                                        saved_map_data.dimensions.left
                                        + (
                                            saved_map_data.dimensions.width
                                            * saved_map_data.dimensions.grid_size
                                        ),
                                    )
                                    - left
                                )
                                / saved_map_data.dimensions.grid_size
                            )
                            height = int(
                                (
                                    max(
                                        map_data.dimensions.top
                                        + (
                                            map_data.dimensions.height
                                            * map_data.dimensions.grid_size
                                        ),
                                        saved_map_data.dimensions.top
                                        + (
                                            saved_map_data.dimensions.height
                                            * saved_map_data.dimensions.grid_size
                                        ),
                                    )
                                    - top
                                )
                                / saved_map_data.dimensions.grid_size
                            )
                            si = int(
                                (saved_map_data.dimensions.left - left)
                                / saved_map_data.dimensions.grid_size
                            )
                            sj = int(
                                (saved_map_data.dimensions.top - top)
                                / saved_map_data.dimensions.grid_size
                            )
                            sim = si + saved_map_data.dimensions.width
                            sjm = sj + saved_map_data.dimensions.height
                            ni = int(
                                (map_data.dimensions.left - left)
                                / map_data.dimensions.grid_size
                            )
                            nj = int(
                                (map_data.dimensions.top - top)
                                / map_data.dimensions.grid_size
                            )
                            nim = ni + map_data.dimensions.width
                            njm = nj + map_data.dimensions.height
                            pixel_type = np.zeros((width, height), np.uint8)

                            for j in range(height):
                                for i in range(width):
                                    if j >= sj and i >= si and j < sjm and i < sim:
                                        saved_value = saved_map_data.data[
                                            (i - si)
                                            + (
                                                (j - sj)
                                                * saved_map_data.dimensions.width
                                            )
                                        ]
                                        segment_id = saved_value & 0x3F
                                    else:
                                        saved_value = -1
                                        segment_id = 0

                                    if (
                                        map_data.restored_map
                                        and segment_id
                                        and saved_value != -1
                                    ):
                                        if saved_value >> 7 == 1:
                                            pixel_type[i, j] = 255
                                        elif saved_value == 63:
                                            pixel_type[i, j] = 253
                                        else:
                                            pixel_type[i, j] = segment_id
                                    elif j >= nj and i >= ni and j < njm and i < nim:
                                        clean_value = int(
                                            map_data.pixel_type[(i - ni), (j - nj)]
                                        )
                                        if clean_value == 255:
                                            pixel_type[i, j] = clean_value
                                        elif clean_value == 253:
                                            pixel_type[i, j] = (
                                                segment_id if segment_id else 254
                                            )

                            map_data.combined_pixel_type = pixel_type
                            map_data.combined_dimensions = MapImageDimensions(
                                top, left, height, width, map_data.dimensions.grid_size
                            )

                        else:
                            # map_data.data = saved_map_data.data
                            map_data.combined_pixel_type = saved_map_data.pixel_type
                            map_data.combined_dimensions = saved_map_data.dimensions

                        if map_data.empty_map:
                            map_data.restored_map = False
                            restored_map = True
                            map_data.empty_map = False
                    else:
                        if saved_map_data.segments is not None:
                            if map_data.segments is None and (
                                map_data.saved_map_status == 1
                                or map_data.saved_map_status == 0
                            ):
                                map_data.segments = {}

                            for k, v in saved_map_data.segments.items():
                                if map_data.segments and k in map_data.segments:
                                    # as implemented on the app
                                    map_data.segments[k].icon = v.icon
                                    map_data.segments[k].name = v.name
                                    map_data.segments[k].custom_name = v.custom_name
                                    map_data.segments[k].type = v.type
                                    map_data.segments[k].index = v.index
                                    map_data.segments[k].unique_id = v.unique_id
                                    map_data.segments[k].neighbors = v.neighbors
                                    map_data.segments[
                                        k
                                    ].floor_material = v.floor_material
                                    map_data.segments[
                                        k
                                    ].floor_material_direction = (
                                        v.floor_material_direction
                                    )
                                    map_data.segments[k].visibility = v.visibility
                                    map_data.segments[k].color_index = v.color_index
                                    if map_data.saved_map_status == 2:
                                        map_data.segments[k].x = v.x
                                        map_data.segments[k].y = v.y

                    if not saved_map_data.cleanset:
                        saved_map_data.cleanset = copy.deepcopy(map_data.cleanset)

                    if (
                        (map_data.saved_map_status == 2 or map_data.docked)
                        and map_data.charger_position is None
                        and not map_data.saved_map
                        and not map_data.recovery_map
                        and saved_map_data.charger_position
                    ):
                        map_data.charger_position = saved_map_data.charger_position

                    # map_data.walls_info = saved_map_data.walls_info
                    # map_data.walls_info_new = saved_map_data.walls_info_new
                    if map_data.saved_map_status == 2:
                        map_data.no_go_areas = saved_map_data.no_go_areas
                        map_data.virtual_walls = saved_map_data.virtual_walls
                        map_data.pathways = saved_map_data.pathways
                        map_data.router_position = saved_map_data.router_position
                        if saved_map_data.saved_furnitures is not None:
                            map_data.furnitures = saved_map_data.saved_furnitures
                            map_data.furniture_version = (
                                saved_map_data.furniture_version
                            )

                        if vslam_map:
                            map_data.segments = copy.deepcopy(saved_map_data.segments)
                            map_data.charger_position = copy.deepcopy(
                                saved_map_data.charger_position
                            )

            if (
                not map_data.saved_map
                and map_data.robot_position is None
                and map_data.docked
                and map_data.charger_position
            ):
                map_data.robot_position = copy.deepcopy(map_data.charger_position)

            if map_data.segments:
                if not map_data.saved_map:
                    DreameMowerMapDecoder.set_robot_segment(map_data)

                if (
                    map_data.saved_map
                    or next(iter(map_data.segments.values())).color_index is None
                ):
                    DreameMowerMapDecoder.set_segment_color_index(map_data)

                DreameMowerMapDecoder.set_floor_material(map_data)

            if "funiture_info" in data_json:
                map_data.furniture_version = 1
                map_data.saved_furnitures = {}
                index = 0
                for furniture in data_json["funiture_info"]:
                    index = index + 1
                    furniture_type = furniture[1]
                    if furniture_type == 8:
                        furniture_type = 25
                    elif furniture_type == 25:
                        furniture_type = 8

                    if furniture[3] > 0 and furniture[4] > 0:
                        map_data.saved_furnitures[index] = Furniture(
                            int(furniture[6]),
                            int(furniture[7]),
                            int(furniture[6] - (furniture[3] / 2)),
                            int(furniture[7] - (furniture[4] / 2)),
                            furniture[3],
                            furniture[4],
                            FurnitureType(furniture_type),
                            int(furniture[13]),
                            furniture[9],
                            furniture[12],
                            furniture[0],
                            furniture[2],
                        )

            if map_data.furnitures is None:
                furniture_key = (
                    "ai_furniture_user"
                    if "ai_furniture_user" in data_json
                    and len(data_json["ai_furniture_user"])
                    else (
                        "ai_furniture_new"
                        if "ai_furniture_new" in data_json
                        and len(data_json["ai_furniture_new"])
                        else "ai_furniture"
                    )
                )
                if furniture_key in data_json:
                    map_data.furniture_version = 0
                    map_data.furnitures = {}
                    index = 0
                    for furniture in data_json[furniture_key]:
                        size = len(furniture)
                        if size >= 4:
                            furniture_type = int(furniture[2])
                            index = index + 1
                            if furniture_type in FurnitureType._value2member_map_:
                                center_x = int(furniture[0])
                                center_y = int(furniture[1])
                                start_x0 = center_x
                                start_y0 = center_y
                                rect_width = 0
                                rect_height = 0
                                angle = 0
                                scale = 1.0
                                if size >= 8:
                                    start_x0 = int(furniture[4])
                                    start_y0 = int(furniture[5])
                                    rect_width = abs(int(furniture[6]))
                                    rect_height = abs(int(furniture[7]))
                                    if size >= 9:
                                        angle = float(furniture[8])
                                        if furniture_key == "ai_furniture":
                                            if angle == 180:
                                                angle = 0
                                            elif angle == 0:
                                                angle = 180
                                    if size >= 10:
                                        scale = float(furniture[9])

                                map_data.furnitures[index] = Furniture(
                                    center_x,
                                    center_y,
                                    start_x0,
                                    start_y0,
                                    rect_width,
                                    rect_height,
                                    FurnitureType(furniture_type),
                                    int(furniture[3]),
                                    angle,
                                    scale,
                                )

            if "ai_obstacle" in data_json:
                map_data.obstacles = {}
                index = 0
                for obstacle in data_json["ai_obstacle"]:
                    size = len(obstacle)
                    if size >= 4:
                        obstacle_type = int(obstacle[2])
                        index = index + 1
                        if obstacle_type in ObstacleType._value2member_map_:
                            id = int(obstacle[4])
                            x = float(obstacle[0])
                            y = float(obstacle[1])
                            possibility = int(float(obstacle[3]) * 100)
                            if size >= 7 and (
                                id >= 1000
                                or obstacle_type == ObstacleType.NEGLECTED_ZONE.value
                            ):
                                if size >= 8:
                                    if (
                                        obstacle_type
                                        == ObstacleType.NEGLECTED_ZONE.value
                                    ):
                                        segment_id = int(x)
                                        x = 0
                                        y = 0
                                        possibility = None
                                        if (
                                            map_data.segments
                                            and segment_id in map_data.segments
                                        ):
                                            x = map_data.segments[segment_id].x
                                            y = map_data.segments[segment_id].y

                                    map_data.obstacles[str(index)] = Obstacle(
                                        x,
                                        y,
                                        ObstacleType(obstacle_type),
                                        possibility,
                                        id,
                                        obstacle[5],
                                        obstacle[6],
                                        float(obstacle[7]) * 100,
                                        float(obstacle[8]) * 100,
                                        float(obstacle[9]) * 100,
                                        float(obstacle[10]) * 100,
                                        int(obstacle[11]) if size >= 13 else 2,
                                        (
                                            int(obstacle[-1])
                                            if len(str(obstacle[-1])) == 1
                                            and (
                                                int(obstacle[-1]) >= 0
                                                or int(obstacle[-1]) <= 2
                                            )
                                            else 0
                                        ),
                                    )
                                else:
                                    map_data.obstacles[str(index)] = Obstacle(
                                        x,
                                        y,
                                        ObstacleType(obstacle_type),
                                        possibility,
                                        id,
                                        obstacle[6],
                                        obstacle[5],
                                    )
                            else:
                                map_data.obstacles[str(index)] = Obstacle(
                                    x,
                                    y,
                                    ObstacleType(obstacle_type),
                                    possibility,
                                )
                            if map_data.segments:
                                map_data.obstacles[str(index)].set_segment(map_data)

            if "vw" in data_json:
                virtual_walls = data_json["vw"]
                if virtual_walls.get("rect") and not map_data.no_go_areas:
                    map_data.no_go_areas = []
                    for area in virtual_walls["rect"]:
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

                if virtual_walls.get("line") and not map_data.virtual_walls:
                    map_data.virtual_walls = [
                        Wall(
                            virtual_wall[0],
                            virtual_wall[1],
                            virtual_wall[2],
                            virtual_wall[3],
                        )
                        for virtual_wall in virtual_walls["line"]
                    ]

            if "vws" in data_json:
                virtual_walls = data_json["vws"]
                if "vwsl" in virtual_walls and not map_data.pathways:
                    map_data.pathways = []
                    for line in virtual_walls["vwsl"]:
                        map_data.pathways.append(
                            Wall(
                                line[0],
                                line[1],
                                line[2],
                                line[3],
                            )
                        )

            if "pointinfo" in data_json:
                points = data_json["pointinfo"]
                if points:
                    if isinstance(points, list):
                        points = points[0]
                    if "spoint" in points and not map_data.predefined_points:
                        map_data.predefined_points = {}
                        index = 0
                        for point in points["spoint"]:
                            index = index + 1
                            map_data.predefined_points[index] = Coordinate(
                                point[0],
                                point[1],
                                bool(point[2]),
                                point[3],
                            )

                    if "tpoint" in points and not map_data.active_cruise_points:
                        map_data.active_cruise_points = {}
                        index = 0
                        for point in points["tpoint"]:
                            index = index + 1
                            map_data.active_cruise_points[index] = Coordinate(
                                point[0],
                                point[1],
                                bool(point[2]),
                                point[3],
                            )

            if "tpointinfo" in data_json:
                map_data.task_cruise_points = {}
                for point in data_json["tpointinfo"]:
                    index = index + 1
                    map_data.task_cruise_points[index] = Coordinate(
                        point[0],
                        point[1],
                        bool(point[2]),
                        point[3],
                    )

            if not map_data.saved_map:
                if "decmap" in data_json or map_data.multiple_cleaning_time:
                    map_data.cleaning_map_data = (
                        DreameMowerMapDecoder.decode_cleaning_map_data(
                            map_data, data_json.get("decmap")
                        )
                    )
                    if map_data.cleaning_map_data:
                        map_data.cleaned_segments = (
                            map_data.cleaning_map_data.cleaned_segments
                        )

            # map_data.ai_outborders_user = data_json.get("ai_outborders_user")
            # map_data.ai_outborders = data_json.get("ai_outborders")
            # map_data.ai_outborders_new = data_json.get("ai_outborders_new")
            # map_data.ai_outborders_2d = data_json.get("ai_outborders_2d")
            # map_data.ai_furniture_warning = data_json.get("ai_furniture_warning")
            # if "walls_info" in data_json:
            #    map_data.walls_info = data_json["walls_info"]
            # if "walls_info_new" in data_json:
            #    map_data.walls_info = data_json["walls_info_new"]

            if vslam_map and not map_data.saved_map:
                map_data.need_optimization = not restored_map
        except Exception:
            _LOGGER.error("Map Parse Failed: %s", traceback.format_exc())

        return map_data, saved_map_data

    @staticmethod
    def decode_p_map_data_from_partial(
        partial_map: MapDataPartial, current_map_data: MapData, vslam_map: bool
    ) -> MapData | None:
        if partial_map.frame_type != MapFrameType.P.value:
            return None

        map_data, saved_map_data = DreameMowerMapDecoder.decode_map_data_from_partial(
            partial_map,
            vslam_map,
        )
        if map_data is None:
            return None

        current_map_data.frame_id = map_data.frame_id
        current_map_data.robot_position = map_data.robot_position
        current_map_data.timestamp_ms = map_data.timestamp_ms
        current_map_data.docked = map_data.docked
        current_map_data.line_to_robot = map_data.line_to_robot
        current_map_data.temporary_map = map_data.temporary_map
        current_map_data.saved_map = False
        current_map_data.empty_map = False
        current_map_data.restored_map = False
        current_map_data.recovery_map = False
        current_map_data.clean_log = False

        if map_data.docked is not None:
            current_map_data.docked = map_data.docked

        if map_data.charger_position is not None and (
            not vslam_map or current_map_data.saved_map_status != 2
        ):
            current_map_data.charger_position = map_data.charger_position

        if map_data.obstacles is not None:
            current_map_data.obstacles = map_data.obstacles

        if map_data.active_cruise_points is not None:
            current_map_data.active_cruise_points = map_data.active_cruise_points

        # P map only returns difference between its previous frame.
        # Resize the map and update the buffer at the received data offset.
        if map_data.data:
            current_dimensions = current_map_data.dimensions
            new_dimensions = map_data.dimensions

            # Find max image size
            grid_size = new_dimensions.grid_size
            left = min(new_dimensions.left, current_dimensions.left)
            top = min(new_dimensions.top, current_dimensions.top)
            max_left = max(
                new_dimensions.left + (new_dimensions.width * grid_size),
                current_dimensions.left
                + (current_dimensions.width * current_dimensions.grid_size),
            )
            max_top = max(
                new_dimensions.top + (new_dimensions.height * grid_size),
                current_dimensions.top
                + (current_dimensions.height * current_dimensions.grid_size),
            )

            # Calculate new image size
            width = int((max_left - left) / grid_size)
            height = int((max_top - top) / grid_size)

            # Create new buffer
            data = np.zeros((width * height), np.uint8)
            pixel_type = np.full(
                (width, height), MapPixelType.OUTSIDE.value, dtype=np.uint8
            )

            # Calculate old image offset
            left_offset = int(
                (current_dimensions.left - left) / current_dimensions.grid_size
            )
            top_offset = int(
                (current_dimensions.top - top) / current_dimensions.grid_size
            )

            # Copy old image to buffer
            for y in range(current_dimensions.height):
                for x in range(current_dimensions.width):
                    data[(width * (top_offset + y)) + left_offset + x] = (
                        current_map_data.data[(current_dimensions.width * y) + x]
                    )
                    pixel_type[left_offset + x, top_offset + y] = (
                        current_map_data.pixel_type[x, y]
                    )

            # Calculate new image offset
            left_offset = int((new_dimensions.left - left) / grid_size)
            top_offset = int((new_dimensions.top - top) / grid_size)

            # Copy new image to buffer at calculated offset
            for y in range(new_dimensions.height):
                for x in range(new_dimensions.width):
                    current_index = (new_dimensions.width * y) + x
                    if map_data.data[current_index]:
                        new_index = (width * (top_offset + y)) + left_offset + x
                        # Combine the current and received buffer values.
                        data[new_index] = data[new_index] + map_data.data[current_index]
                        # Calculate the new pixel type from updated buffer value
                        pixel_type[left_offset + x, top_offset + y] = (
                            DreameMowerMapDecoder._get_pixel_type(
                                current_map_data,
                                int(data[new_index]),
                                vslam_map,
                            )
                        )

            # Update size and buffer
            current_map_data.data = bytes(data)
            current_map_data.pixel_type = pixel_type
            current_map_data.dimensions = MapImageDimensions(
                top, left, height, width, grid_size
            )

            if vslam_map:
                current_map_data.need_optimization = True

        if map_data.path:
            # Append new paths received with P frame
            if current_map_data.path:
                current_map_data.path.extend(map_data.path)
            else:
                current_map_data.path = map_data.path

        if current_map_data.obstacles is not None:
            for k, _value in current_map_data.obstacles.items():
                current_map_data.obstacles[k].set_segment(current_map_data)

        DreameMowerMapDecoder.set_robot_segment(current_map_data)
        return current_map_data

    @staticmethod
    def decode_cleaning_map_data(map_data, cleaning_map_str):
        partial_cleaning_map = None
        if cleaning_map_str and len(cleaning_map_str) > 1:
            partial_cleaning_map = DreameMowerMapDecoder.decode_map_partial(
                cleaning_map_str
            )
            if partial_cleaning_map is None:
                return

        cleaning_map = MapData()
        if partial_cleaning_map:
            cleaning_map.map_id = partial_cleaning_map.map_id
            cleaning_map.frame_id = partial_cleaning_map.frame_id
            cleaning_map.frame_type = partial_cleaning_map.frame_type
            cleaning_map.timestamp_ms = partial_cleaning_map.timestamp_ms
            cleaning_map.cleaned_segments = partial_cleaning_map.data_json.get(
                "CleanArea"
            )
        else:
            cleaning_map.map_id = map_data.map_id
            cleaning_map.frame_id = map_data.frame_id
            cleaning_map.frame_type = map_data.frame_type
            cleaning_map.timestamp_ms = map_data.timestamp_ms

        cleaning_map.dimensions = map_data.dimensions
        cleaning_map.charger_position = map_data.charger_position
        cleaning_map.robot_position = map_data.robot_position
        cleaning_map.segments = map_data.segments
        cleaning_map.pixel_type = map_data.pixel_type.copy()
        cleaning_map.rotation = map_data.rotation
        cleaning_map.saved_map_status = map_data.saved_map_status
        cleaning_map.docked = map_data.docked
        cleaning_map.dos = map_data.dos
        cleaning_map.multiple_cleaning_time = map_data.multiple_cleaning_time
        cleaning_map.cleanup_method = map_data.cleanup_method
        cleaning_map.startup_method = map_data.startup_method
        cleaning_map.history_map = True
        cleaning_map.saved_map = False
        cleaning_map.cleaning_map = True
        if cleaning_map.docked and cleaning_map.robot_position is None:
            cleaning_map.robot_position = map_data.charger_position

        cleaning_map.multiple_cleaning_time = map_data.multiple_cleaning_time
        if partial_cleaning_map:
            grid_size = DreameMowerMapDecoder._read_int_16_le(
                partial_cleaning_map.raw, 17
            )
            width = DreameMowerMapDecoder._read_int_16_le(partial_cleaning_map.raw, 19)
            height = DreameMowerMapDecoder._read_int_16_le(partial_cleaning_map.raw, 21)
            left = DreameMowerMapDecoder._read_int_16_le(partial_cleaning_map.raw, 23)
            top = DreameMowerMapDecoder._read_int_16_le(partial_cleaning_map.raw, 25)

            data = partial_cleaning_map.raw[
                DreameMowerMapDecoder.HEADER_SIZE : DreameMowerMapDecoder.HEADER_SIZE
                + width * height
            ]

            for y in range(height):
                for x in range(width):
                    value = data[int(y * width + x)] & 3
                    if value > 0:
                        xx = int(
                            ((left + (x * grid_size)) - map_data.dimensions.left)
                            / map_data.dimensions.grid_size
                        )
                        yy = int(
                            ((top + (y * grid_size)) - map_data.dimensions.top)
                            / map_data.dimensions.grid_size
                        )
                        if cleaning_map.check_point(xx, yy, True):
                            cleaning_map.pixel_type[xx, yy] = 249 - value

        cleaning_map.has_dirty_area = bool(
            MapPixelType.DIRTY_AREA.value in cleaning_map.pixel_type
        )
        cleaning_map.has_cleaned_area = bool(
            MapPixelType.CLEAN_AREA.value in cleaning_map.pixel_type
        )

        return cleaning_map

    @staticmethod
    def get_segments(map_data: MapData, vslam_map: bool) -> dict[str, Any]:
        segments = {}
        for y in range(map_data.dimensions.height):
            for x in range(map_data.dimensions.width):
                segment_id = int(map_data.pixel_type[x, y])
                if segment_id > 0 and segment_id < 64:
                    if segment_id not in segments:
                        segments[segment_id] = Segment(segment_id, x, y, x, y)
                        continue

                    if x < segments[segment_id].x0:
                        segments[segment_id].x0 = x
                    elif x > segments[segment_id].x1:
                        segments[segment_id].x1 = x

                    if y < segments[segment_id].y0:
                        segments[segment_id].y0 = y
                    elif y > segments[segment_id].y1:
                        segments[segment_id].y1 = y

        if segments:
            for k, v in segments.items():
                x = int(math.ceil((v.x1 - v.x0) / 2 + v.x0))
                y = int(math.ceil((v.y1 - v.y0) / 2 + v.y0))

                if map_data.saved_map:
                    if vslam_map:
                        if map_data.pixel_type[x, y] != k:
                            startI = -1
                            endI = -1
                            for i in range(map_data.dimensions.width):
                                value = map_data.pixel_type[i, y]
                                if startI == -1:
                                    if value == k:
                                        startI = i
                                elif value != k or i == (map_data.dimensions.width - 1):
                                    endI = i - 1
                                    break

                            if startI != -1 and endI != -1:
                                x = (endI - startI) + startI
                    else:
                        center_x = DreameMowerMapDecoder._get_segment_center(
                            map_data, k, y, False
                        )
                        if center_x is not None:
                            center_y = DreameMowerMapDecoder._get_segment_center(
                                map_data, k, center_x, True
                            )
                            if center_y is not None:
                                x = center_x
                                y = center_y

                segments[k].x0 = int(
                    map_data.dimensions.left + (v.x0 * map_data.dimensions.grid_size)
                )
                segments[k].y0 = int(
                    map_data.dimensions.top
                    + (v.y0 * map_data.dimensions.grid_size)
                    - map_data.dimensions.grid_size
                )
                segments[k].x1 = int(
                    map_data.dimensions.left
                    + (v.x1 * map_data.dimensions.grid_size)
                    + map_data.dimensions.grid_size
                )
                segments[k].y1 = int(
                    map_data.dimensions.top + (v.y1 * map_data.dimensions.grid_size)
                )
                segments[k].x = int(
                    map_data.dimensions.left + (x * map_data.dimensions.grid_size)
                )
                segments[k].y = int(
                    map_data.dimensions.top + (y * map_data.dimensions.grid_size)
                )
                segments[k].set_name()
        return segments

    @staticmethod
    def set_robot_segment(map_data: MapData) -> None:
        if (
            map_data.segments
            and map_data.saved_map_status == 2
            and map_data.robot_position is not None
        ):
            map_data.robot_segment = map_data.pixel_type[
                int(
                    (map_data.robot_position.x - map_data.dimensions.left)
                    / map_data.dimensions.grid_size
                ),
                int(
                    (map_data.robot_position.y - map_data.dimensions.top)
                    / map_data.dimensions.grid_size
                ),
            ]
            if map_data.robot_segment not in map_data.segments:
                map_data.robot_segment = 0
                for k, v in map_data.segments.items():
                    if v.check_point(
                        map_data.robot_position.x,
                        map_data.robot_position.y,
                        map_data.dimensions.grid_size * 4,
                    ):
                        map_data.robot_segment = k
                        break
        else:
            map_data.robot_segment = None

    @staticmethod
    def set_segment_cleanset(
        map_data: MapData,
        cleanset: dict[str, list[int]],
        capability: DreameMowerDeviceCapability = None,
    ) -> None:
        if map_data is not None and map_data.segments is not None:
            # Restored maps can omit cleanset values even though the mower
            # continues using these defaults.
            default_cleanset = [
                1,
                3,
                1,
                0,
            ]

            if capability:
                if capability.cleaning_route:
                    default_cleanset.extend([2, 33])

            cleanset_type = CleansetType.NONE
            if cleanset is not None:
                cleanset_type = CleansetType.DEFAULT
                if len(cleanset) == 0:
                    if capability:
                        if capability.cleaning_route:
                            cleanset_type = CleansetType.CLEANING_ROUTE
                        elif capability.custom_cleaning_mode:
                            cleanset_type = CleansetType.CLEANING_MODE
                else:
                    for _segment_id, v in cleanset.items():
                        if len(v) > 5 and v[5] > 0:
                            cleanset_type = CleansetType.CLEANING_MODE
                            if capability:
                                if capability.cleaning_route:
                                    cleanset_type = CleansetType.CLEANING_ROUTE
                                break
                        if len(v) > 4:
                            cleanset_type = CleansetType.CLEANING_MODE
                            break

            for k, _segment in map_data.segments.items():
                map_data.segments[k].cleanset_type = cleanset_type
                if cleanset_type != CleansetType.NONE:
                    segment_id = str(k)
                    if segment_id not in cleanset:
                        cleanset[segment_id] = default_cleanset.copy()

                    item = cleanset[segment_id]
                    map_data.segments[k].cleaning_times = item[2]
                    map_data.segments[k].order = item[3]
                    if len(item) > 4:
                        map_data.segments[k].cleaning_mode = item[4]
                    else:
                        map_data.segments[k].cleaning_route = None
                else:
                    map_data.segments[k].cleaning_times = None
                    map_data.segments[k].order = None
                    map_data.segments[k].cleaning_mode = None
                    map_data.segments[k].cleaning_route = None

    @staticmethod
    def set_segment_color_index(map_data: MapData) -> None:
        """Find segment color index as implemented on the app"""
        area_color_index = {}
        sorted_segments = sorted(
            map_data.segments.values(),
            key=cmp_to_key(DreameMowerMapDecoder._compare_segment_neighbors),
        )
        for segment in sorted_segments:
            used_ids = []
            if segment.neighbors is not None:
                for nid in segment.neighbors:
                    if nid in area_color_index:
                        used_ids.append(area_color_index[nid])

            area_color_num = {}
            for i in range(4):
                area_color_num[i] = [i, 0]

            for _segment_id, j in area_color_index.items():
                area_color_num[j][1] = area_color_num[j][1] + 1

            area_color_num = sorted(
                area_color_num.values(),
                key=cmp_to_key(DreameMowerMapDecoder._compare_colors),
            )

            for area_color in area_color_num:
                color = area_color[0]
                if color not in used_ids:
                    area_color_index[segment.segment_id] = color
                    break

            if segment.segment_id not in area_color_index:
                area_color_index[segment.segment_id] = 0

        for k, v in area_color_index.items():
            map_data.segments[k].color_index = v

    @staticmethod
    def set_segment_floor_material(
        map_data: MapData, segment_id: int, floor_material
    ) -> None:
        if (
            floor_material is not None
            and map_data.segments
            and segment_id in map_data.segments
        ):
            if map_data.segments[segment_id].floor_material is not None:
                if map_data.segments[segment_id].floor_material_direction is not None:
                    map_data.segments[segment_id].floor_material_rotated_direction = (
                        map_data.segments[segment_id].floor_material_direction
                        if map_data.rotation == 0 or map_data.rotation == 180
                        else 90
                        if map_data.segments[segment_id].floor_material_direction == 0
                        else 0
                    )

                floor_material[segment_id] = (
                    0
                    if map_data.segments[segment_id].floor_material <= 0
                    or map_data.segments[segment_id].floor_material > 2
                    else (
                        3
                        if map_data.segments[segment_id].floor_material == 2
                        else (
                            2
                            if map_data.segments[segment_id].floor_material_direction
                            == 90
                            or (
                                map_data.segments[segment_id].x1
                                - map_data.segments[segment_id].x0
                            )
                            <= (
                                map_data.segments[segment_id].y1
                                - map_data.segments[segment_id].y0
                            )
                            else 1
                        )
                    )
                )

    @staticmethod
    def set_floor_material(map_data: MapData) -> None:
        if map_data.segments:
            floor_material = {}
            for k in map_data.segments.keys():
                DreameMowerMapDecoder.set_segment_floor_material(
                    map_data, k, floor_material
                )
            if floor_material:
                map_data.floor_material = floor_material
