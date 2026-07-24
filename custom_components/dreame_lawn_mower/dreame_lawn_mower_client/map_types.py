"""Geometry and mutable map-state types used by the reusable client."""

from __future__ import annotations

import json
import math
import time
from datetime import datetime
from enum import IntEnum
from typing import Any

from .device_types import (
    ATTR_A,
    ATTR_ACTIVE_AREAS,
    ATTR_ACTIVE_CRUISE_POINTS,
    ATTR_ACTIVE_POINTS,
    ATTR_ACTIVE_SEGMENTS,
    ATTR_ANGLE,
    ATTR_CHARGER,
    ATTR_CLEANING_MODE,
    ATTR_CLEANING_TIMES,
    ATTR_COLOR_INDEX,
    ATTR_COMPLETED,
    ATTR_FLOOR_MATERIAL,
    ATTR_FLOOR_MATERIAL_DIRECTION,
    ATTR_FRAME_ID,
    ATTR_FURNITURES,
    ATTR_HEIGHT,
    ATTR_ICON,
    ATTR_IGNORE_STATUS,
    ATTR_INDEX,
    ATTR_IS_EMPTY,
    ATTR_MAP_ID,
    ATTR_MAP_INDEX,
    ATTR_MAP_NAME,
    ATTR_NAME,
    ATTR_NO_GO_AREAS,
    ATTR_OBSTACLES,
    ATTR_ORDER,
    ATTR_PATHWAYS,
    ATTR_PICTURE_STATUS,
    ATTR_POSSIBILTY,
    ATTR_PREDEFINED_POINTS,
    ATTR_RECOVERY_MAP_LIST,
    ATTR_ROBOT_POSITION,
    ATTR_ROTATION,
    ATTR_ROUTER_POSITION,
    ATTR_SCALE,
    ATTR_SIZE_TYPE,
    ATTR_STARTUP_METHOD,
    ATTR_TYPE,
    ATTR_UNIQUE_ID,
    ATTR_UPDATED,
    ATTR_VIRTUAL_WALLS,
    ATTR_VISIBILITY,
    ATTR_WIDTH,
    ATTR_X,
    ATTR_X0,
    ATTR_X1,
    ATTR_X2,
    ATTR_X3,
    ATTR_Y,
    ATTR_Y0,
    ATTR_Y1,
    ATTR_Y2,
    ATTR_Y3,
    ATTR_ZONE,
    ATTR_ZONE_ID,
    ATTR_ZONES,
    PIID,
    SEGMENT_TYPE_CODE_TO_HA_ICON,
    SEGMENT_TYPE_CODE_TO_NAME,
    CleansetType,
    DreameMowerFloorMaterialDirection,
    DreameMowerProperty,
    DreameMowerSegmentVisibility,
    DreameMowerStatus,
    FurnitureType,
    ObstacleIgnoreStatus,
    ObstaclePictureStatus,
    ObstacleType,
    PathType,
    SegmentNeglectReason,
    TaskInterruptReason,
    piid,
)


class Point:
    def __init__(self, x: float, y: float, a=None) -> None:
        self.x = x
        self.y = y
        self.a = a

    def __str__(self) -> str:
        if self.a is None:
            return f"({self.x}, {self.y})"
        return f"({self.x}, {self.y}, a = {self.a})"

    def __repr__(self) -> str:
        return self.__str__()

    def __eq__(self: Point, other: Point) -> bool:
        return (
            other is not None
            and self.x == other.x
            and self.y == other.y
            and self.a == other.a
        )

    def as_dict(self) -> dict[str, Any]:
        if self.a is None:
            return {ATTR_X: self.x, ATTR_Y: self.y}
        return {ATTR_X: self.x, ATTR_Y: self.y, ATTR_A: self.a}

    def to_img(self, image_dimensions, offset=True) -> Point:
        return image_dimensions.to_img(self, offset)

    def to_coord(self, image_dimensions, offset=True) -> Point:
        return image_dimensions.to_coord(self, offset)

    def rotated(self, image_dimensions, degree) -> Point:
        w = int(
            (image_dimensions.width * image_dimensions.scale)
            + image_dimensions.padding[0]
            + image_dimensions.padding[2]
            - image_dimensions.crop[0]
            - image_dimensions.crop[2]
        )
        h = int(
            (image_dimensions.height * image_dimensions.scale)
            + image_dimensions.padding[1]
            + image_dimensions.padding[3]
            - image_dimensions.crop[1]
            - image_dimensions.crop[3]
        )
        x = self.x
        y = self.y
        while degree > 0:
            tmp = y
            y = w - x
            x = tmp
            tmp = h
            h = w
            w = tmp
            degree = degree - 90
        return Point(x, y)

    def __mul__(self, other) -> Point:
        return Point(self.x * other, self.y * other, self.a)

    def __truediv__(self, other) -> Point:
        return Point(self.x / other, self.y / other, self.a)


class Path(Point):
    def __init__(self, x: float, y: float, path_type: PathType) -> None:
        super().__init__(x, y)
        self.path_type = path_type

    def as_dict(self) -> dict[str, Any]:
        attributes = {**super().as_dict()}
        if self.path_type:
            attributes[ATTR_TYPE] = self.path_type.value
        return attributes


class Obstacle(Point):
    def __init__(
        self,
        x: float,
        y: float,
        type: int,
        possibility: int,
        object_id: int = None,
        file_name: str = None,
        key: int = None,
        pos_x: float = None,
        pos_y: float = None,
        width: float = None,
        height: float = None,
        picture_status: int = 0,
        ignore_status: int = 0,
    ) -> None:
        super().__init__(x, y)
        self.type = (
            ObstacleType(type)
            if type in ObstacleType._value2member_map_
            else ObstacleType.UNKNOWN
        )
        self.possibility = possibility
        self.object_id = object_id
        self.key = key
        self.file_name = file_name
        self.object_name = file_name
        self.pos_x = pos_x
        self.pos_y = pos_y
        self.height = height
        self.width = width
        self.picture_status = (
            ObstaclePictureStatus(picture_status)
            if picture_status in ObstaclePictureStatus._value2member_map_
            else ObstaclePictureStatus.UNKNOWN
        )
        self.ignore_status = (
            ObstacleIgnoreStatus(ignore_status)
            if ignore_status in ObstacleIgnoreStatus._value2member_map_
            else ObstacleIgnoreStatus.UNKNOWN
        )
        self.id = (
            str(self.object_id) if self.object_id else f"0{int(self.x)}0{int(self.y)}"
        )

        if file_name and "/" in file_name:
            self.object_name = file_name.split("/")[-1]
            if "-" in self.object_name:
                self.object_name = self.object_name.split("-")[0]
        if id:
            self.object_name = f"{id}-{self.object_name}"

        self.segment = None

    def set_segment(self, map_data):
        if map_data and map_data.segments and map_data.pixel_type is not None:
            x = int((self.x - map_data.dimensions.left) / map_data.dimensions.grid_size)
            y = int((self.y - map_data.dimensions.top) / map_data.dimensions.grid_size)
            if (
                x >= 0
                and x < map_data.dimensions.width
                and y >= 0
                and y < map_data.dimensions.height
            ):
                obstacle_pixel = map_data.pixel_type[x, y]

                if obstacle_pixel not in map_data.segments:
                    for v in map_data.segments.values():
                        if v.check_point(
                            self.x, self.y, map_data.dimensions.grid_size * 4
                        ):
                            self.segment = v.name
                            break
                else:
                    self.segment = map_data.segments[obstacle_pixel].name

    def as_dict(self) -> dict[str, Any]:
        attributes = super().as_dict()
        attributes[ATTR_TYPE] = self.type.name.replace("_", " ").title()
        if self.possibility is not None:
            attributes[ATTR_POSSIBILTY] = self.possibility
        if self.picture_status is not None:
            attributes[ATTR_PICTURE_STATUS] = self.picture_status.name.replace(
                "_", " "
            ).title()
        if self.ignore_status is not None:
            attributes[ATTR_IGNORE_STATUS] = self.ignore_status.name.replace(
                "_", " "
            ).title()
        if self.segment is not None:
            attributes[ATTR_ZONE] = self.segment
        return attributes

    def __eq__(self: Obstacle, other: Obstacle) -> bool:
        return not (
            other is None
            or self.x != other.x
            or self.y != other.y
            or self.type != other.type
            or self.possibility != other.possibility
            or self.key != other.key
            or self.file_name != other.file_name
            or self.pos_x != other.pos_x
            or self.pos_y != other.pos_y
            or self.height != other.height
            or self.width != other.width
            or self.picture_status != other.picture_status
            or self.ignore_status != other.ignore_status
        )


class Zone:
    def __init__(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1

    def __str__(self) -> str:
        return f"[{self.x0}, {self.y0}, {self.x1}, {self.y1}]"

    def __eq__(self: Zone, other: Zone) -> bool:
        return (
            other is not None
            and self.x0 == other.x0
            and self.y0 == other.y0
            and self.x1 == other.x1
            and self.y1 == other.y1
        )

    def __repr__(self) -> str:
        return self.__str__()

    def as_dict(self) -> dict[str, Any]:
        return {ATTR_X0: self.x0, ATTR_Y0: self.y0, ATTR_X1: self.x1, ATTR_Y1: self.y1}

    def as_area(self) -> Area:
        return Area(
            self.x0, self.y0, self.x0, self.y1, self.x1, self.y1, self.x1, self.y0
        )

    def to_img(self, image_dimensions, offset=True) -> Zone:
        p0 = Point(self.x0, self.y0).to_img(image_dimensions, offset)
        p1 = Point(self.x1, self.y1).to_img(image_dimensions, offset)
        return Zone(p0.x, p0.y, p1.x, p1.y)

    def to_coord(self, image_dimensions, offset=True) -> Zone:
        p0 = Point(self.x0, self.y0).to_coord(image_dimensions, offset)
        p1 = Point(self.x1, self.y1).to_coord(image_dimensions, offset)
        return Zone(p0.x, p0.y, p1.x, p1.y)

    def check_point(self, x, y, size) -> bool:
        return self.as_area().check_point(x, y, size)


class Segment(Zone):
    def __init__(
        self,
        segment_id: int,
        x0: float | None = None,
        y0: float | None = None,
        x1: float | None = None,
        y1: float | None = None,
        x: int | None = None,
        y: int | None = None,
        name: str = None,
        custom_name: str = None,
        index: int = 0,
        type: int = 0,
        icon: str = None,
        neighbors: list[int] | None = None,
        cleaning_times: int = None,
        cleaning_mode: int = None,
        order: int = None,
    ) -> None:
        super().__init__(x0, y0, x1, y1)
        self.segment_id = segment_id
        self.unique_id = None
        self.x = x
        self.y = y
        self.name = name
        self.custom_name = custom_name
        self.type = type
        self.index = index
        self.icon = icon
        self.neighbors = neighbors if neighbors is not None else []
        self.order = order
        self.cleaning_times = cleaning_times
        self.cleaning_mode = cleaning_mode
        self.cleaning_route = None
        self.color_index = None
        self.floor_material = None
        self.floor_material_direction = None
        self.floor_material_rotated_direction = None
        self.visibility = None
        self.cleanset_type = CleansetType.NONE
        self.set_name()

    @property
    def outline(self) -> list[list[int]]:
        return [
            [self.x0, self.y0],
            [self.x0, self.y1],
            [self.x1, self.y1],
            [self.x1, self.y0],
        ]

    @property
    def center(self) -> list[int]:
        return [self.x, self.y]

    @property
    def letter(self) -> str:
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        return (
            f"{letters[((self.segment_id % 26) - 1)]}{math.floor(self.segment_id / 26)}"
            if self.segment_id > 26
            else letters[self.segment_id - 1]
        )

    def set_name(self) -> None:
        if self.type != 0 and SEGMENT_TYPE_CODE_TO_NAME.get(self.type):
            self.name = SEGMENT_TYPE_CODE_TO_NAME[self.type]
            if self.index > 0:
                self.name = f"{self.name} {self.index + 1}"
        elif self.custom_name is not None:
            self.name = self.custom_name
        else:
            self.name = f"Zone {self.segment_id}"
        self.icon = SEGMENT_TYPE_CODE_TO_HA_ICON.get(self.type, "mdi:home-outline")

    def next_type_index(self, type, segments) -> int:
        index = 0
        if type > 0:
            for segment_id in sorted(
                segments, key=lambda segment_id: segments[segment_id].index
            ):
                if (
                    segment_id != self.segment_id
                    and segments[segment_id].type == type
                    and segments[segment_id].index == index
                ):
                    index = index + 1
        return index

    def name_list(self, segments) -> dict[int, str]:
        list = {}
        for k, v in SEGMENT_TYPE_CODE_TO_NAME.items():
            index = self.next_type_index(k, segments)
            name = f"{v}"
            if index > 0:
                name = f"{name} {index + 1}"

            list[k] = name

        name = f"Zone {self.segment_id}"
        if self.type == 0:
            name = f"{self.name}"
        list[0] = name
        if self.type != 0:  # and self.index > 0:
            list[self.type] = self.name

        return {v: k for k, v in list.items()}

    def as_dict(self) -> dict[str, Any]:
        attributes = {**super().as_dict()}
        if self.segment_id:
            attributes[ATTR_ZONE_ID] = self.segment_id
        if self.name is not None:
            attributes[ATTR_NAME] = self.name
        if self.order is not None:
            attributes[ATTR_ORDER] = self.order
        if self.cleaning_times is not None:
            attributes[ATTR_CLEANING_TIMES] = self.cleaning_times
        if (
            self.cleaning_mode is not None
            and self.cleanset_type != CleansetType.DEFAULT
        ):
            attributes[ATTR_CLEANING_MODE] = self.cleaning_mode
        if self.type is not None:
            attributes[ATTR_TYPE] = self.type
        if self.index is not None:
            attributes[ATTR_INDEX] = self.index
        if self.icon is not None:
            attributes[ATTR_ICON] = self.icon
        if self.color_index is not None:
            attributes[ATTR_COLOR_INDEX] = self.color_index
        if self.unique_id is not None:
            attributes[ATTR_UNIQUE_ID] = self.unique_id
        if self.floor_material is not None:
            attributes[ATTR_FLOOR_MATERIAL] = self.floor_material
        if self.floor_material_rotated_direction is not None:
            attributes[ATTR_FLOOR_MATERIAL_DIRECTION] = (
                DreameMowerFloorMaterialDirection(
                    self.floor_material_rotated_direction
                ).name.title()
            )
        if self.visibility is not None:
            attributes[ATTR_VISIBILITY] = DreameMowerSegmentVisibility(
                int(self.visibility)
            ).name.title()
        if self.x is not None and self.y is not None:
            attributes[ATTR_X] = self.x
            attributes[ATTR_Y] = self.y

        return attributes

    def __eq__(self: Segment, other: Segment) -> bool:
        return not (
            other is None
            or self.x0 != other.x0
            or self.y0 != other.y0
            or self.x1 != other.x1
            or self.y1 != other.y1
            or self.x != other.x
            or self.y != other.y
            or self.name != other.name
            or self.index != other.index
            or self.type != other.type
            or self.color_index != other.color_index
            or self.icon != other.icon
            or self.neighbors != other.neighbors
            or self.order != other.order
            or self.cleaning_times != other.cleaning_times
            or self.cleaning_mode != other.cleaning_mode
            or self.cleaning_route != other.cleaning_route
            or self.floor_material != other.floor_material
            or self.floor_material_direction != other.floor_material_direction
            or self.floor_material_rotated_direction
            != other.floor_material_rotated_direction
            or self.visibility != other.visibility
        )

    def __str__(self) -> str:
        return f"{{zone_id: {self.segment_id}, outline: {self.outline}}}"

    def __repr__(self) -> str:
        return self.__str__()


class Wall:
    def __init__(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1

    def __eq__(self: Wall, other: Wall) -> bool:
        return (
            other is not None
            and self.x0 == other.x0
            and self.y0 == other.y0
            and self.x1 == other.x1
            and self.y1 == other.y1
        )

    def __str__(self) -> str:
        return f"[{self.x0}, {self.y0}, {self.x1}, {self.y1}]"

    def __repr__(self) -> str:
        return self.__str__()

    def as_dict(self) -> dict[str, Any]:
        return {ATTR_X0: self.x0, ATTR_Y0: self.y0, ATTR_X1: self.x1, ATTR_Y1: self.y1}

    def to_img(self, image_dimensions, offset=True) -> Wall:
        p0 = Point(self.x0, self.y0).to_img(image_dimensions, offset)
        p1 = Point(self.x1, self.y1).to_img(image_dimensions, offset)
        return Wall(p0.x, p0.y, p1.x, p1.y)

    def to_coord(self, image_dimensions, offset=True) -> Wall:
        p0 = Point(self.x0, self.y0).to_coord(image_dimensions, offset)
        p1 = Point(self.x1, self.y1).to_coord(image_dimensions, offset)
        return Wall(p0.x, p0.y, p1.x, p1.y)

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


class Area:
    def __init__(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        x3: float,
        y3: float,
    ) -> None:
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1
        self.x2 = x2
        self.y2 = y2
        self.x3 = x3
        self.y3 = y3

    def __eq__(self: Area, other: Area) -> bool:
        return (
            other is not None
            and self.x0 == other.x0
            and self.y0 == other.y0
            and self.x1 == other.x1
            and self.y1 == other.y1
            and self.x2 == other.x2
            and self.y2 == other.y2
            and self.x3 == other.x3
            and self.y3 == other.y3
        )

    def __str__(self) -> str:
        return (
            f"[{self.x0}, {self.y0}, {self.x1}, {self.y1}, "
            f"{self.x2}, {self.y2}, {self.x3}, {self.y3}]"
        )

    def __repr__(self) -> str:
        return self.__str__()

    def as_dict(self) -> dict[str, Any]:
        return {
            ATTR_X0: self.x0,
            ATTR_Y0: self.y0,
            ATTR_X1: self.x1,
            ATTR_Y1: self.y1,
            ATTR_X2: self.x2,
            ATTR_Y2: self.y2,
            ATTR_X3: self.x3,
            ATTR_Y3: self.y3,
        }

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1, self.x2, self.y2, self.x3, self.y3]

    def to_img(self, image_dimensions, offset=True) -> Area:
        p0 = Point(self.x0, self.y0).to_img(image_dimensions, offset)
        p1 = Point(self.x1, self.y1).to_img(image_dimensions, offset)
        p2 = Point(self.x2, self.y2).to_img(image_dimensions, offset)
        p3 = Point(self.x3, self.y3).to_img(image_dimensions, offset)
        return Area(p0.x, p0.y, p1.x, p1.y, p2.x, p2.y, p3.x, p3.y)

    def to_coord(self, image_dimensions, offset=True) -> Area:
        p0 = Point(self.x0, self.y0).to_coord(image_dimensions, offset)
        p1 = Point(self.x1, self.y1).to_coord(image_dimensions, offset)
        p2 = Point(self.x2, self.y2).to_coord(image_dimensions, offset)
        p3 = Point(self.x3, self.y3).to_coord(image_dimensions, offset)
        return Area(p0.x, p0.y, p1.x, p1.y, p2.x, p2.y, p3.x, p3.y)

    def check_size(self, size) -> bool:
        return self.x2 - self.x0 == size and self.y2 - self.y1 == size

    def check_point(self, x, y, size) -> bool:
        x_coords = [self.x0, self.x1, self.x2, self.x3]
        y_coords = [self.y0, self.y1, self.y2, self.y3]

        min_x = min(x_coords)
        max_x = max(x_coords)
        min_y = min(y_coords)
        max_y = max(y_coords)
        return (
            x >= min_x - size
            and x <= max_x + size
            and y >= min_y - size
            and y <= max_y + size
        )


class Furniture(Point):
    def __init__(
        self,
        x: float,
        y: float,
        x0: float,
        y0: float,
        width: float,
        height: float,
        type: FurnitureType,
        size_type: int,
        angle: float = 0,
        scale: float = 1.0,
        furniture_id: int = None,
        segment_id: int = None,
    ) -> None:
        super().__init__(x, y)
        self.x0 = x0
        self.y0 = y0
        self.width = width
        self.height = height
        if x0 and y0 and width and height:
            self.x1 = x0 + width
            self.y1 = y0
            self.x2 = x0 + width
            self.y2 = y0 + height
            self.x3 = x0
            self.y3 = y0 + height
        else:
            self.x1 = None
            self.y1 = None
            self.x2 = None
            self.y2 = None
            self.x3 = None
            self.y3 = None
        self.type = type
        self.size_type = size_type
        self.angle = angle
        self.scale = scale
        self.furniture_id = furniture_id
        self.segment_id = segment_id

    def as_dict(self) -> dict[str, Any]:
        attributes = super().as_dict()
        attributes[ATTR_TYPE] = self.type.name.replace("_", " ").title()
        if self.x0 is not None and self.y0 is not None:
            attributes[ATTR_X0] = self.x0
            attributes[ATTR_Y0] = self.y0
        if self.x1 is not None and self.y1 is not None:
            attributes[ATTR_X1] = self.x1
            attributes[ATTR_Y1] = self.y1
        if self.x2 is not None and self.y2 is not None:
            attributes[ATTR_X2] = self.x2
            attributes[ATTR_Y2] = self.y2
        if self.x3 is not None and self.y3 is not None:
            attributes[ATTR_X3] = self.x3
            attributes[ATTR_Y3] = self.y3
        if self.width and self.height:
            attributes[ATTR_WIDTH] = self.width
            attributes[ATTR_HEIGHT] = self.height
        if self.segment_id:
            attributes[ATTR_ZONE_ID] = self.segment_id
        attributes[ATTR_SIZE_TYPE] = self.size_type
        attributes[ATTR_ANGLE] = self.angle
        attributes[ATTR_SCALE] = self.scale
        return attributes

    def __eq__(self: Furniture, other: Furniture) -> bool:
        return not (
            other is None
            or self.x != other.x
            or self.y != other.y
            or self.x0 != other.x0
            or self.y0 != other.y0
            or self.width != other.width
            or self.height != other.height
            or self.type != other.type
            or self.size_type != other.size_type
            or self.angle != other.angle
            or self.scale != other.scale
        )


class Coordinate(Point):
    def __init__(self, x: float, y: float, completed: bool, type: int) -> None:
        super().__init__(x, y)
        self.type = type
        self.completed = completed

    def as_dict(self) -> dict[str, Any]:
        attributes = {**super().as_dict()}
        if self.type is not None:
            attributes[ATTR_TYPE] = self.type
        if self.completed is not None:
            attributes[ATTR_COMPLETED] = self.completed
        return attributes

    def __eq__(self: Coordinate, other: Coordinate) -> bool:
        return not (
            other is None
            or self.x != other.x
            or self.y != other.y
            or self.type != other.type
            or self.completed != other.completed
        )


class MapImageDimensions:
    def __init__(
        self, top: int, left: int, height: int, width: int, grid_size: int
    ) -> None:
        self.top = top
        self.left = left
        self.height = height
        self.width = width
        self.grid_size = grid_size
        self.scale = 1
        self.padding = [0, 0, 0, 0]
        self.crop = [0, 0, 0, 0]
        self.bounds = None

    def to_img(self, point: Point, offset=True) -> Point:
        left = self.left
        top = self.top
        if not offset and (left % self.grid_size != 0 or top % self.grid_size != 0):
            left = left + (self.grid_size / 2)
            top = top - (self.grid_size / 2)

        return Point(
            ((point.x - left) / self.grid_size) * self.scale
            + self.padding[0]
            - self.crop[0],
            (((self.height - 1) * self.grid_size - (point.y - top)) / self.grid_size)
            * self.scale
            + self.padding[1]
            - self.crop[1],
        )

    def to_coord(self, point: Point, offset=True) -> Point:
        left = self.left
        top = self.top
        if not offset and (left % self.grid_size != 0 or top % self.grid_size != 0):
            left = left + (self.grid_size / 2)
            top = top - (self.grid_size / 2)

        return Point(
            ((point.x - left) / self.grid_size),
            (((self.height - 1) * self.grid_size - (point.y - top)) / self.grid_size),
        )

    def __eq__(self: MapImageDimensions, other: MapImageDimensions) -> bool:
        return (
            other is not None
            and self.top == other.top
            and self.left == other.left
            and self.height == other.height
            and self.width == other.width
            and self.grid_size == other.grid_size
        )


class CleaningHistory:
    def __init__(self, history_data, property_mapping) -> None:
        self.date: datetime = None
        self.status: DreameMowerStatus = None
        self.cleaning_time: int = 0
        self.cleaned_area: int = 0
        self.file_name: str = None
        self.key = None
        self.object_name = None
        self.completed: bool = None
        self.map_index: int = None
        self.map_name: str = None
        self.cruise_type: int = None
        self.cleanup_method: CleanupMethod = None
        self.second_cleaning: int = None
        self.multiple_cleaning_time: str = None
        self.pet_focused_cleaning: int = None
        self.task_interrupt_reason: TaskInterruptReason = None
        self.neglected_segments: dict[int, int] = None
        self.clean_again: int = None

        for history_data_item in history_data:
            pid = history_data_item[piid]
            value = (
                history_data_item["value"]
                if "value" in history_data_item
                else history_data_item["val"]
            )

            if pid == PIID(DreameMowerProperty.STATUS, property_mapping):
                if value in DreameMowerStatus._value2member_map_:
                    self.status = DreameMowerStatus(value)
                else:
                    self.status = DreameMowerStatus.UNKNOWN
            elif pid == PIID(DreameMowerProperty.CLEANING_TIME, property_mapping):
                self.cleaning_time = value
            elif pid == PIID(DreameMowerProperty.CLEANED_AREA, property_mapping):
                self.cleaned_area = value
            elif pid == PIID(DreameMowerProperty.CLEANING_START_TIME, property_mapping):
                self.date = datetime.fromtimestamp(value)
            elif pid == PIID(DreameMowerProperty.CLEAN_LOG_FILE_NAME, property_mapping):
                self.file_name = value
                if len(self.file_name) > 1:
                    if "," in self.file_name:
                        values = self.file_name.split(",")
                        self.object_name = values[0]
                        self.key = values[1]
                    else:
                        self.object_name = self.file_name
            elif pid == PIID(DreameMowerProperty.CLEAN_LOG_STATUS, property_mapping):
                self.completed = bool(value)
            elif pid == PIID(DreameMowerProperty.MAP_INDEX, property_mapping):
                self.map_index = value
            elif pid == PIID(DreameMowerProperty.MAP_NAME, property_mapping):
                self.map_name = value
            elif pid == PIID(DreameMowerProperty.CRUISE_TYPE, property_mapping):
                self.cruise_type = value
            elif pid == PIID(DreameMowerProperty.CLEANING_PROPERTIES, property_mapping):
                props = json.loads(value)
                if "cmc" in props:
                    value = props["cmc"]
                    self.cleanup_method = (
                        CleanupMethod(value)
                        if value in CleanupMethod._value2member_map_
                        else CleanupMethod.OTHER
                    )
                if "abnormal_end" in props:
                    values = json.loads(props["abnormal_end"])
                    self.task_interrupt_reason = (
                        TaskInterruptReason(values[0])
                        if values[0] in TaskInterruptReason._value2member_map_
                        else TaskInterruptReason.UNKNOWN
                    )
                self.second_cleaning = props.get("ismultiple")
                self.multiple_cleaning_time = props.get("multime")
                self.pet_focused_cleaning = props.get("pet")
                self.neglected_segments = props.get("area_clean_detail")
                self.clean_again = props.get("cleanagain")
                if "area_clean_detail" in props:
                    values = props["area_clean_detail"]
                    if len(values) > 1:
                        values = json.loads(values)
                        if values:
                            self.neglected_segments = {
                                v[0]: SegmentNeglectReason(v[1])
                                for v in values
                                if v[1] in SegmentNeglectReason._value2member_map_
                            }


class RecoveryMapInfo:
    def __init__(self, map_id, map_info) -> None:
        self.date = map_info.get("time")
        self.raw_map: str = map_info.get("thb")
        self.object_name: str = map_info.get("objname")
        self.map_data: MapData = None
        self.map_id: int = map_id

        map_type = map_info.get("first", -1)
        self.map_type = (
            RecoveryMapType(map_type)
            if map_type in RecoveryMapType._value2member_map_
            else RecoveryMapType.UNKNOWN
        )

        if self.date:
            self.date = datetime.fromtimestamp(self.date)

    def as_dict(self):
        return {
            "date": time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(self.date.timestamp())
            ),
            "map_type": self.map_type.name.replace("_", " ").title(),
            "object_name": self.object_name,
        }


class MapFrameType(IntEnum):
    I = 73  # noqa: E741 - protocol frame identifier
    P = 80
    # T = ??
    W = 87


class MapPixelType(IntEnum):
    OUTSIDE = 0
    WIFI_WALL = 2
    WIFI_UNREACHED = 10
    WIFI_POOR = 11
    WIFI_LOW = 12
    WIFI_HIGH = 13
    WIFI_EXCELLENT = 14
    WALL = 255
    FLOOR = 254
    NEW_SEGMENT = 253
    UNKNOWN = 252
    OBSTACLE_WALL = 251
    NEW_SEGMENT_UNKNOWN = 250
    HIDDEN_WALL = 249
    CLEAN_AREA = 248
    DIRTY_AREA = 247


class RecoveryMapType(IntEnum):
    UNKNOWN = -1
    EDITED = 0
    ORIGINAL = 1
    BACKUP = 2


class StartupMethod(IntEnum):
    OTHER = -1
    BY_BUTTON = 0
    THROUGH_APP = 1
    SCHEDULED_ACTIVATION = 2
    THROUGH_VOICE = 3


class CleanupMethod(IntEnum):
    OTHER = -1
    DEFAULT_MODE = 0
    CUSTOMIZED_CLEANING = 1
    CLEANGENIUS = 2


class TaskEndType(IntEnum):
    OTHER = 0
    MANUAL_DOCKING = 1
    NORMAL_RECHARGING = 2
    ABNORMAL_DOCKING = 3
    INTERRUPTION_ENDED = 4


class MapDataPartial:
    def __init__(self) -> None:
        self.map_id: int | None = None  # Map header: map_id
        self.frame_id: int | None = None  # Map header: frame_id
        self.frame_type: int | None = None  # Map header: frame_type
        self.timestamp_ms: int | None = None  # Data json: timestamp_ms
        self.raw: bytes | None = None  # Unzipped raw map
        self.data_json: object | None = {}  # Data json


class MapData:
    def __init__(self) -> None:
        # Header
        self.map_id: int | None = None  # Map header: map_id
        self.frame_id: int | None = None  # Map header: frame_id
        self.frame_type: int | None = None  # Map header: frame_type
        # Map header: robot x, robot y, robot angle
        self.robot_position: Point | None = None
        # Map header: charger x, charger y, charger angle
        self.charger_position: Point | None = None
        self.optimized_charger_position: Point | None = None
        self.router_position: Point | None = None  # Data json: whmp
        # Map header: top, left, height, width, grid_size
        self.dimensions: MapImageDimensions | None = None
        self.optimized_dimensions: MapImageDimensions | None = None
        self.combined_dimensions: MapImageDimensions | None = None
        self.data: Any | None = None  # Raw image data for handling P frames
        # Data json
        self.timestamp_ms: int | None = None  # Data json: timestamp_ms
        self.rotation: int | None = None  # Data json: mra
        self.no_go_areas: list[Area] | None = None  # Data json: vw.rect
        self.virtual_walls: list[Wall] | None = None  # Data json: vw.line
        self.pathways: list[Wall] | None = None  # Data json: vws.vwsl
        self.path: Path | None = None  # Data json: tr
        self.active_segments: int | None = None  # Data json: sa
        self.active_areas: list[Area] | None = None  # Data json: da2
        self.active_points: list[Point] | None = None  # Data json: sp
        # Data json: rism.map_header.map_id
        self.saved_map_id: int | None = None
        self.saved_map_status: int | None = None  # Data json: ris
        self.restored_map: bool | None = None  # Data json: rpur
        self.frame_map: bool | None = None  # Data json: fsm
        self.docked: bool | None = None  # Data json: oc
        self.clean_log: bool | None = None  # Data json: iscleanlog
        self.cleanset: dict[str, list[int]] | None = None  # Data json: cleanset
        self.line_to_robot: bool | None = None  # Data json: l2r
        self.temporary_map: int | None = None  # Data json: suw
        self.cleaned_area: int | None = None  # Data json: cs
        self.cleaning_time: int | None = None  # Data json: ct
        self.completed: bool | None = None  # Data json: cf
        self.neglected_segments: list[int] | None = None  #
        self.second_cleaning: bool | None = None  #
        # Data json: clean_finish_remain_electricity
        self.remaining_battery: int | None = None
        self.work_status: int | None = None  # Data json: wm
        self.recovery_map: bool | None = None  # Data json: us
        # Generated from recovery map list json
        self.recovery_map_type: RecoveryMapType | None = None
        self.obstacles: dict[int, Obstacle] | None = None  # Data json: ai_obstacle
        # Data json: ai_furniture
        self.furnitures: dict[int, Furniture] | None = None
        # Data json: furniture_info
        self.saved_furnitures: dict[int, Furniture] | None = None
        self.new_map: bool | None = None  # Data json: risp
        self.startup_method: StartupMethod | None = None  # Data json: smd
        self.task_end_type: TaskEndType | None = None  # Data json: ctyi
        self.cleanup_method: CleanupMethod | None = None  #
        # Data json: customeclean
        self.customized_cleaning: int | None = None
        # Data json: CleanArea (from dirty map data)
        self.cleaned_segments: list[Any] | None = None
        self.multiple_cleaning_time: int | None = None  # Data json: multime
        self.dos: int | None = None  # Data json: dos
        # Generated
        self.custom_name: str | None = None  # Map list json: name
        self.map_index: int | None = None  # Generated from saved map list
        self.map_name: str | None = None  # Generated map name for map list
        # Generated pixel map for rendering colors
        self.pixel_type: Any | None = None
        self.optimized_pixel_type: Any | None = None
        self.combined_pixel_type: Any | None = None
        # Generated segments from pixel_type
        self.segments: dict[int, Segment] | None = None
        # Generated from seg_inf.material
        self.floor_material: dict[int, int] | None = None
        self.saved_map: bool | None = None  # Generated for rism map
        self.empty_map: bool | None = None  # Generated from pixel_type
        self.wifi_map_data: MapData | None = None  # Generated from whm
        self.wifi_map: bool | None = None  #
        # Generated from decmap
        self.cleaning_map_data: MapData | None = None
        self.cleaning_map: bool | None = None  #
        self.has_cleaned_area: bool | None = None  #
        self.has_dirty_area: bool | None = None  #
        self.history_map: bool | None = None  #
        self.furniture_version: bool | None = None  #
        # Generated from recovery map list
        self.recovery_map_list: list[RecoveryMapInfo] | None = None
        # Data json: pointinfo.tpoint
        self.active_cruise_points: list[Coordinate] | None = None
        # Data json: pointinfo.spoint
        self.predefined_points: dict[int, Coordinate] | None = None
        # Data json: tpointinfo
        self.task_cruise_points: list[Coordinate] | None = None
        # Generated from pixel_type and robot poisiton
        self.hidden_segments: int | None = None  # Data json: delsr
        self.robot_segment: int | None = None
        # For renderer to detect changes
        self.last_updated: float | None = None
        # For vslam map rendering optimization
        self.need_optimization: bool | None = None
        # 3D Map Properties
        self.ai_outborders_user: Any | None = None
        self.ai_outborders: Any | None = None
        self.ai_outborders_new: Any | None = None
        self.ai_outborders_2d: Any | None = None
        self.ai_furniture_warning: Any | None = None
        self.walls_info: Any | None = None
        self.walls_info_new: Any | None = None

    def __eq__(self: MapData, other: MapData) -> bool:
        if other is None:
            return False

        if self.map_id != other.map_id:
            return False

        if self.custom_name != other.custom_name:
            return False

        if self.rotation != other.rotation:
            return False

        if self.work_status != other.work_status:
            return False

        if self.robot_position != other.robot_position:
            return False

        if self.charger_position != other.charger_position:
            return False

        if self.no_go_areas != other.no_go_areas:
            return False

        if self.virtual_walls != other.virtual_walls:
            return False

        if self.pathways != other.pathways:
            return False

        if self.docked != other.docked:
            return False

        if self.active_segments != other.active_segments:
            return False

        if self.active_areas != other.active_areas:
            return False

        if self.active_points != other.active_points:
            return False

        if self.active_cruise_points != other.active_cruise_points:
            return False

        if self.clean_log != other.clean_log:
            return False

        if self.saved_map_status != other.saved_map_status:
            return False

        if self.restored_map != other.restored_map:
            return False

        if self.frame_map != other.frame_map:
            return False

        if self.temporary_map != other.temporary_map:
            return False

        if self.saved_map != other.saved_map:
            return False

        if self.new_map != other.new_map:
            return False

        if self.cleanset != other.cleanset:
            return False

        if self.furnitures != other.furnitures:
            return False

        if self.saved_furnitures != other.saved_furnitures:
            return False

        if self.obstacles != other.obstacles:
            return False

        if self.predefined_points != other.predefined_points:
            return False

        if self.router_position != other.router_position:
            return False

        if self.hidden_segments != other.hidden_segments:
            return False

        return True

    def as_dict(self) -> dict[str, Any]:
        attributes_list = {}
        if self.charger_position is not None:
            attributes_list[ATTR_CHARGER] = (
                self.optimized_charger_position
                if self.optimized_charger_position is not None
                else self.charger_position
            )
        if self.segments is not None and (
            self.saved_map or self.saved_map_status == 2 or self.restored_map
        ):
            attributes_list[ATTR_ZONES] = {
                k: v.as_dict() for k, v in sorted(self.segments.items())
            }
        if not self.saved_map and self.robot_position is not None:
            attributes_list[ATTR_ROBOT_POSITION] = self.robot_position
        if self.map_id:
            attributes_list[ATTR_MAP_ID] = self.map_id
        if self.map_name is not None:
            attributes_list[ATTR_MAP_NAME] = self.map_name
        if self.rotation is not None:
            attributes_list[ATTR_ROTATION] = self.rotation
        if self.last_updated is not None:
            attributes_list[ATTR_UPDATED] = datetime.fromtimestamp(self.last_updated)
        if not self.saved_map and self.active_areas is not None:
            attributes_list[ATTR_ACTIVE_AREAS] = self.active_areas
        if not self.saved_map and self.active_segments is not None:
            attributes_list[ATTR_ACTIVE_SEGMENTS] = self.active_segments
        if not self.saved_map and self.active_points is not None:
            attributes_list[ATTR_ACTIVE_POINTS] = self.active_points
        if not self.saved_map and self.active_cruise_points is not None:
            attributes_list[ATTR_ACTIVE_CRUISE_POINTS] = self.active_cruise_points
        if self.predefined_points:
            attributes_list[ATTR_PREDEFINED_POINTS] = list(
                self.predefined_points.values()
            )
        if self.virtual_walls is not None:
            attributes_list[ATTR_VIRTUAL_WALLS] = self.virtual_walls
        if self.pathways is not None:
            attributes_list[ATTR_PATHWAYS] = self.pathways
        if self.no_go_areas is not None:
            attributes_list[ATTR_NO_GO_AREAS] = self.no_go_areas
        if self.empty_map is not None:
            attributes_list[ATTR_IS_EMPTY] = self.empty_map
        if self.frame_id:
            attributes_list[ATTR_FRAME_ID] = self.frame_id
        if self.map_index:
            attributes_list[ATTR_MAP_INDEX] = self.map_index
        if self.obstacles:
            attributes_list[ATTR_OBSTACLES] = self.obstacles
        if self.saved_furnitures and self.saved_map:
            attributes_list[ATTR_FURNITURES] = list(self.saved_furnitures.values())
        elif self.furnitures:
            attributes_list[ATTR_FURNITURES] = list(self.furnitures.values())
        if self.router_position:
            attributes_list[ATTR_ROUTER_POSITION] = self.router_position
        if self.startup_method:
            attributes_list[ATTR_STARTUP_METHOD] = self.startup_method.name.replace(
                "_", " "
            ).title()
        if self.recovery_map_list:
            attributes_list[ATTR_RECOVERY_MAP_LIST] = [
                v.as_dict() for v in reversed(self.recovery_map_list)
            ]
        return attributes_list

    def check_point(self, x, y, absolute=False) -> bool:
        if not absolute:
            x = int((x - self.dimensions.left) / self.dimensions.grid_size)
            y = int((y - self.dimensions.top) / self.dimensions.grid_size)
        if x < 0 or x >= self.dimensions.width or y < 0 or y >= self.dimensions.height:
            return False
        value = int(self.pixel_type[x, y])
        return value > 0 and value != 255


__all__ = [
    "Point",
    "Path",
    "Obstacle",
    "Zone",
    "Segment",
    "Wall",
    "Area",
    "Furniture",
    "Coordinate",
    "MapImageDimensions",
    "CleaningHistory",
    "RecoveryMapInfo",
    "MapFrameType",
    "MapPixelType",
    "RecoveryMapType",
    "StartupMethod",
    "CleanupMethod",
    "TaskEndType",
    "MapDataPartial",
    "MapData",
]
