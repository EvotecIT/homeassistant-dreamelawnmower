"""Contracts for the styled legacy current-map fallback."""

from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    legacy_map_visuals,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.const import (
    MAP_DATA_JSON_CLASS,
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
