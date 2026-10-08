"""Contracts for the styled legacy current-map fallback."""

from __future__ import annotations

from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    legacy_map_visuals,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.const import (
    MAP_DATA_JSON_CLASS,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_renderer import (
    DreameMowerMapRenderer,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    MapData,
    MapImageDimensions,
    MapPixelType,
    Point,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (
    map_render_style,
)


def test_legacy_renderer_receives_shared_presentation_options() -> None:
    marker_buffer = BytesIO()
    Image.new("RGBA", (24, 18), (255, 0, 0, 255)).save(
        marker_buffer,
        format="PNG",
    )
    style = map_render_style(
        "midnight",
        stroke_scale=1.5,
        marker_scale=1.75,
        marker_image=marker_buffer.getvalue(),
    )

    renderer = legacy_map_visuals._legacy_renderer(style=style, label_scale=2.5)

    assert renderer.presentation_stroke_scale == 1.5
    assert renderer.presentation_marker_scale == 1.75
    assert renderer.presentation_label_scale == 2.5
    assert renderer.presentation_marker_image is not None
    assert renderer.presentation_marker_image.size == (24, 18)
    assert renderer.color_scheme.outside == style.background
    assert renderer.color_scheme.wall == style.boundary
    assert renderer.color_scheme.path == style.mow_path
    assert renderer.color_scheme.text == style.label


def test_legacy_map_png_keeps_map_metadata_on_styled_image() -> None:
    map_data = MapData()
    map_data.map_id = 1
    map_data.frame_id = 2
    map_data.empty_map = False
    map_data.rotation = 0
    map_data.saved_map = False
    map_data.dimensions = MapImageDimensions(0, 0, 4, 4, 50)
    map_data.pixel_type = np.full((4, 4), MapPixelType.FLOOR.value)
    map_data.data = bytes([MapPixelType.FLOOR.value] * 16)
    map_data.segments = {}
    map_data.robot_position = Point(75, 75, 0)
    map_data.last_updated = 0

    image_png = legacy_map_visuals.render_legacy_map_png(
        map_data,
        label_scale=2.0,
        style=map_render_style("dark"),
    )

    with Image.open(BytesIO(image_png)) as image:
        image.load()
        assert image.format == "PNG"
        assert image.width > 1
        assert image.height > 1
        assert MAP_DATA_JSON_CLASS in image.text


@pytest.mark.parametrize(
    "robot_type,icon_set,robot_status",
    [(0, 0, 0), (0, 1, 0), (0, 2, 0), (1, 3, 0), (0, 0, 1)],
)
def test_legacy_renderer_accepts_unknown_headings_in_existing_icon_modes(
    robot_type, icon_set, robot_status, caplog
) -> None:
    map_data = MapData()
    map_data.map_id, map_data.frame_id = 1, 2
    map_data.empty_map, map_data.docked = False, True
    map_data.rotation = 0
    map_data.dimensions = MapImageDimensions(0, 0, 4, 4, 50)
    map_data.pixel_type = np.full((4, 4), MapPixelType.FLOOR.value)
    map_data.data = bytes([MapPixelType.FLOOR.value] * 16)
    map_data.segments = {}
    map_data.robot_position = Point(75, 75)
    map_data.charger_position = Point(75, 75)
    map_data.last_updated = 0
    renderer = DreameMowerMapRenderer(robot_type=robot_type, cache=False)
    renderer.icon_set = icon_set

    png = renderer.render_map(map_data, robot_status=robot_status)

    with Image.open(BytesIO(png)) as image:
        image.load()
        assert image.width > 1 and image.height > 1
    assert map_data.robot_position.a is map_data.charger_position.a is None
    assert not [record for record in caplog.records if record.levelno >= 40]
