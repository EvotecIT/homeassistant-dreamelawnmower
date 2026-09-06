"""Shared presentation options for camera and interactive map consumers."""

from collections.abc import Mapping
from typing import Any

from .const import (
    CONF_MAP_MARKER_SCALE,
    CONF_MAP_MOWING_PATH_STYLE,
    CONF_MAP_ROTATION,
    CONF_MAP_ROTATIONS,
    CONF_MAP_SPOT_AREA_STYLE,
    CONF_MAP_STROKE_SCALE,
    CONF_MAP_THEME,
    DEFAULT_MAP_MARKER_SCALE,
    DEFAULT_MAP_MOWING_PATH_STYLE,
    DEFAULT_MAP_ROTATION,
    DEFAULT_MAP_SPOT_AREA_STYLE,
    DEFAULT_MAP_STROKE_SCALE,
    DEFAULT_MAP_THEME,
)
from .dreame_lawn_mower_client.map_visuals import MapRenderStyle, map_render_style


def map_rotation(options: Mapping[str, Any], map_index: int | None) -> int:
    """Resolve a saved map's override before the global display rotation."""
    rotations = options.get(CONF_MAP_ROTATIONS, {})
    if isinstance(rotations, Mapping) and map_index is not None:
        value = rotations.get(str(map_index), rotations.get(map_index))
        if value in (0, 90, 180, 270):
            return int(value)
    return int(options.get(CONF_MAP_ROTATION, DEFAULT_MAP_ROTATION))


def map_style(
    options: Mapping[str, Any],
    map_index: int | None,
    *,
    marker_image: bytes | None = None,
) -> MapRenderStyle:
    """Translate HA options once into the renderer's reusable style contract."""
    return map_render_style(
        options.get(CONF_MAP_THEME, DEFAULT_MAP_THEME),
        rotation=map_rotation(options, map_index),
        stroke_scale=options.get(CONF_MAP_STROKE_SCALE, DEFAULT_MAP_STROKE_SCALE),
        marker_scale=options.get(CONF_MAP_MARKER_SCALE, DEFAULT_MAP_MARKER_SCALE),
        marker_image=marker_image,
        spot_area_style=options.get(
            CONF_MAP_SPOT_AREA_STYLE, DEFAULT_MAP_SPOT_AREA_STYLE
        ),
        mowing_path_style=options.get(
            CONF_MAP_MOWING_PATH_STYLE, DEFAULT_MAP_MOWING_PATH_STYLE
        ),
    )
