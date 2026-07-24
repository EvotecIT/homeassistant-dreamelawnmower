"""Configuration and cached data types for map rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Final


@dataclass
class MapRendererConfig:
    color: bool = True
    icon: bool = True
    name: bool = True
    name_background: bool = True
    order: bool = True
    cleaning_times: bool = True
    cleaning_mode: bool = True
    path: bool = True
    no_go: bool = True
    no_mop: bool = True
    virtual_wall: bool = True
    pathway: bool = True
    active_area: bool = True
    active_point: bool = True
    charger: bool = True
    robot: bool = True
    cleaning_direction: bool = True
    obstacle: bool = True
    pet: bool = True
    material: bool = True
    furniture: bool = True
    cruise_point: bool = True


@dataclass
class MapRendererColorScheme:
    floor: tuple[int] = (221, 221, 221, 255)
    outside: tuple[int] = (0, 0, 0, 0)
    wall: tuple[int] = (159, 159, 159, 255)
    passive_segment: tuple[int] = (200, 200, 200, 255)
    hidden_segment: tuple[int] = (226, 226, 226, 255)
    new_segment: tuple[int] = (153, 191, 255, 255)
    cleaned_area: tuple[int] = (158, 240, 117, 255)
    dirty_area: tuple[int] = (247, 135, 106, 255)
    clean_area: tuple[int] = (156, 202, 250, 255)
    second_clean_area: tuple[int] = (123, 148, 172, 255)
    neglected_segment: tuple[int] = (255, 159, 10, 110)
    no_go: tuple[int] = (177, 0, 0, 50)
    no_go_outline: tuple[int] = (199, 0, 0, 200)
    virtual_wall: tuple[int] = (199, 0, 0, 200)
    pathway: tuple[int] = (23, 111, 244, 200)
    active_area: tuple[int] = (255, 255, 255, 80)
    active_area_outline: tuple[int] = (34, 109, 242, 255)  # (103, 156, 244, 200)
    active_point: tuple[int] = (255, 255, 255, 80)
    active_point_outline: tuple[int] = (34, 109, 242, 255)  # (103, 156, 244, 200)
    path: tuple[int] = (255, 255, 255, 255)
    segment: tuple[list[tuple[int]]] = (
        [(171, 199, 248, 255), (121, 170, 255, 255)],
        [(249, 224, 125, 255), (255, 211, 38, 255)],
        [(184, 227, 255, 255), (141, 210, 255, 255)],
        [(184, 217, 141, 255), (150, 217, 141, 255)],
    )
    obstacle_bg: tuple[int] = (34, 109, 242, 255)
    icon_background: tuple[int] = (0, 0, 0, 100)
    settings_background: tuple[int] = (255, 255, 255, 175)
    settings_icon_background: tuple[int] = (255, 255, 255, 205)
    material_color: tuple[int] = (0, 0, 0, 20)
    text: tuple[int] = (255, 255, 255, 255)
    order: tuple[int] = (255, 255, 255, 255)
    text_stroke: tuple[int] = (240, 240, 240, 200)
    invert: bool = False
    dark: bool = False


MAP_COLOR_SCHEME_LIST: Final = {
    "Dreame Light": MapRendererColorScheme(),
    "Dreame Dark": MapRendererColorScheme(
        floor=(110, 110, 110, 255),
        wall=(64, 64, 64, 255),
        passive_segment=(100, 100, 100, 255),
        hidden_segment=(116, 116, 116, 255),
        new_segment=(0, 91, 244, 255),
        no_go=(133, 0, 0, 128),
        no_go_outline=(149, 0, 0, 200),
        virtual_wall=(133, 0, 0, 200),
        active_area=(200, 200, 200, 70),
        active_area_outline=(28, 81, 176, 255),  # (9, 54, 129, 200),
        active_point=(200, 200, 200, 80),
        active_point_outline=(28, 81, 176, 255),  # (9, 54, 129, 200),
        path=(200, 200, 200, 255),
        segment=(
            [(13, 64, 155, 255), (0, 55, 150, 255)],
            [(143, 75, 7, 255), (117, 53, 0, 255)],
            [(0, 106, 176, 255), (0, 96, 158, 255)],
            [(76, 107, 36, 255), (44, 107, 36, 255)],
        ),
        obstacle_bg=(28, 81, 176, 255),
        material_color=(255, 255, 255, 20),
        settings_icon_background=(255, 255, 255, 195),
        dark=True,
    ),
    "Mijia Light": MapRendererColorScheme(
        new_segment=(131, 178, 255, 255),
        virtual_wall=(255, 45, 45, 200),
        no_go=(230, 30, 30, 128),
        no_go_outline=(255, 45, 45, 200),
        segment=(
            [(131, 178, 255, 255), (105, 142, 204, 255)],
            [(245, 201, 66, 255), (196, 161, 53, 255)],
            [(103, 207, 229, 255), (82, 165, 182, 255)],
            [(255, 155, 101, 255), (204, 124, 81, 255)],
        ),
        obstacle_bg=(131, 178, 255, 255),
    ),
    "Mijia Dark": MapRendererColorScheme(
        floor=(150, 150, 150, 255),
        wall=(119, 133, 153, 255),
        new_segment=(99, 148, 230, 255),
        passive_segment=(100, 100, 100, 255),
        hidden_segment=(116, 116, 116, 255),
        no_go=(133, 0, 0, 128),
        no_go_outline=(149, 0, 0, 200),
        virtual_wall=(133, 0, 0, 200),
        active_area=(200, 200, 200, 70),
        active_area_outline=(9, 54, 129, 200),
        active_point=(200, 200, 200, 80),
        active_point_outline=(9, 54, 129, 200),
        path=(200, 200, 200, 255),
        segment=(
            [(108, 141, 195, 255), (76, 99, 137, 255)],
            [(188, 157, 62, 255), (133, 111, 44, 255)],
            [(88, 161, 176, 255), (62, 113, 123, 255)],
            [(195, 125, 87, 255), (138, 89, 62, 255)],
        ),
        obstacle_bg=(108, 141, 195, 255),
        material_color=(255, 255, 255, 35),
        settings_icon_background=(255, 255, 255, 195),
        dark=True,
    ),
    "Grayscale": MapRendererColorScheme(
        floor=(100, 100, 100, 255),
        wall=(40, 40, 40, 255),
        passive_segment=(50, 50, 50, 255),
        hidden_segment=(55, 55, 55, 255),
        new_segment=(80, 80, 80, 255),
        no_go=(133, 0, 0, 128),
        no_go_outline=(149, 0, 0, 200),
        virtual_wall=(133, 0, 0, 200),
        active_area=(221, 221, 221, 60),
        active_area_outline=(22, 103, 238, 200),
        active_point=(221, 221, 221, 80),
        active_point_outline=(22, 103, 238, 200),
        path=(200, 200, 200, 255),
        segment=(
            [(90, 90, 90, 255), (95, 95, 95, 255)],
            [(80, 80, 80, 255), (85, 85, 85, 255)],
            [(70, 70, 70, 255), (75, 75, 75, 255)],
            [(60, 60, 60, 255), (65, 65, 65, 255)],
        ),
        obstacle_bg=(90, 90, 90, 255),
        material_color=(255, 255, 255, 20),
        icon_background=(200, 200, 200, 200),
        settings_icon_background=(255, 255, 255, 205),
        text=(0, 0, 0, 255),
        text_stroke=(0, 0, 0, 100),
        invert=True,
        dark=True,
    ),
    "Transparent": MapRendererColorScheme(
        floor=(0, 0, 0, 0),
        wall=(0, 0, 0, 0),
        passive_segment=(0, 0, 0, 0),
        hidden_segment=(0, 0, 0, 0),
        new_segment=(0, 0, 0, 0),
        path=(255, 255, 255, 200),
        segment=(
            [(0, 0, 0, 0), (121, 170, 255, 255)],
            [(0, 0, 0, 0), (255, 211, 38, 255)],
            [(0, 0, 0, 0), (141, 210, 255, 255)],
            [(0, 0, 0, 0), (150, 217, 141, 255)],
        ),
    ),
}

MAP_ICON_SET_LIST: Final = {"Dreame": 0, "Dreame Old": 1, "Mijia": 2, "Material": 3}


class MapRendererLayer(IntEnum):
    IMAGE = 0
    OBJECTS = 1
    PATH = 2
    PATH_MASK = 3
    NO_GO = 5
    WALL = 6
    PATHWAY = 7
    ACTIVE_AREA = 8
    ACTIVE_POINT = 9
    FURNITURES = 10
    FURNITURE = 11
    SEGMENTS = 12
    SEGMENT = 13
    CHARGER = 14
    ROBOT = 15
    ROUTER = 16
    OBSTACLES = 17
    OBSTACLE = 18
    CRUISE_POINTS = 19
    CRUISE_POINT = 20


@dataclass
class Line:
    x: int | list[int] = None
    y: int | list[int] = None
    ishorizontal: bool = False
    direction: int = 0


@dataclass
class CLine(Line):
    length: int = 0
    findEnd: bool = False


@dataclass
class ALine:
    p0: Line = field(default_factory=lambda: Line(0, 0, False, 0))
    p1: Line = field(default_factory=lambda: Line(0, 0, False, 0))
    length: int = 0


@dataclass
class Paths:
    clines: list[CLine] = field(default_factory=lambda: [])
    alines: list[ALine] = field(default_factory=lambda: [])
    length: int = 0


@dataclass
class Angle:
    lines: list[ALine] = field(default_factory=lambda: [])
    horizontalDir: int = 0
    verticalDir: int = 0


@dataclass
class MapRendererResources:
    renderer: str = ""
    icon_set: int = 0
    robot_type: int = 0
    robot: str = None
    charger: str = None
    charging: str = None
    cleaning: str = None
    warning: str = None
    sleeping: str = None
    cleaning_direction: str = None
    selected_segment: str = None
    cruise_point_background: str = None
    segment: dict[int, dict[str, str]] = None
    default_map_image: str = None
    font: str = None
    repeats: list[str] = None
    cleaning_mode: list[str] = None
    cleaning_route: list[str] = None
    emptying: str = None
    cruise_path_point_background: str = None
    obstacle_background: str = None
    obstacle_hidden_background: str = None
    obstacle: dict[int, dict[str, str]] = None
    furniture: dict[int, dict[str, str]] = None
    rotate: str = None
    delete: str = None
    resize: str = None
    move: str = None
    problem: str = None
    wifi: str = None
    version: int = 1


@dataclass
class MapRendererData:
    data: dict[int, list[int]]
    size: list[int] = None
    frame_id: int = 0
    saved_map: bool = False
    wifi_map: bool = False
    history_map: bool = False
    recovery_map: bool = False
    segments: dict[int, list[int | str]] | None = None
    active_segments: list[int] = field(default_factory=lambda: [])
    active_areas: list[list[int]] = field(default_factory=lambda: [])
    active_points: list[list[int]] = field(default_factory=lambda: [])
    active_cruise_points: list[list[int]] = field(default_factory=lambda: [])
    task_cruise_points: bool = False
    predefined_points: list[list[int]] | None = None
    no_mop: list[list[int]] = field(default_factory=lambda: [])
    no_go: list[list[int]] = field(default_factory=lambda: [])
    virtual_walls: list[list[int]] = field(default_factory=lambda: [])
    pathways: list[list[int]] | None = None
    obstacles: list[list[int | float]] = field(default_factory=lambda: [])
    furnitures: list[list[int | float]] | None = None
    path: list[list[int]] = field(default_factory=lambda: [])
    floor_material: dict[int, list[int]] | None = None
    hidden_segments: dict[int, list[int]] | None = None
    neglected_segments: dict[int, list[int]] | None = None
    robot_position: list[int] | None = None
    charger_position: list[int] | None = None
    router_position: list[int] | None = None
    ai_outborders_user: list[list[int]] | None = None
    ai_outborders: list[list[int]] | None = None
    ai_outborders_new: list[list[int]] | None = None
    ai_outborders_2d: list[list[int]] | None = None
    second_cleaning: int | None = None
    multiple_cleaning_time: int | None = None
    dos: int | None = None
    ai_furniture_warning: int | None = None
    walls_info: Any | None = None
    walls_info_new: Any | None = None
    furniture_version: int | None = None
    startup_method: str | None = None
    cleanup_method: str | None = None
    cleaned_area: int | None = None
    cleaning_time: int | None = None
    robot_status: int | None = None
    station_status: int | None = None
    completed: bool | None = None
    remaining_battery: int | None = None
    cleanset: bool = False
    docked: bool = True
    work_status: int = 0
    resources: MapRendererResources = None
    version: int = 1


__all__ = [
    "MapRendererConfig",
    "MapRendererColorScheme",
    "MAP_COLOR_SCHEME_LIST",
    "MAP_ICON_SET_LIST",
    "MapRendererLayer",
    "Line",
    "CLine",
    "ALine",
    "Paths",
    "Angle",
    "MapRendererResources",
    "MapRendererData",
]
