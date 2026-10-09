"""Embedded map metadata follows current state and preserves raster coordinates."""

import json
from copy import deepcopy
from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    map_json_renderer,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    PathType,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    Area,
    MapData,
    MapImageDimensions,
    MapPixelType,
    Path,
    Point,
    Segment,
    Wall,
)

Renderer = map_json_renderer.DreameMowerMapDataJsonRenderer


def _map():
    data = MapData()
    data.map_id, data.frame_id = 7, 2
    data.empty_map, data.rotation = False, 0
    data.dimensions = MapImageDimensions(0, 0, 2, 4, 50)
    data.pixel_type = np.full((4, 2), MapPixelType.FLOOR.value, dtype=np.uint8)
    data.data = data.pixel_type.tobytes()
    data.robot_position = Point(50, 100, 22.5)
    data.path = [Path(0, 0, PathType.SWEEP), Path(50, 100, PathType.LINE)]
    return data


def _metadata(png):
    with Image.open(BytesIO(png)) as image:
        image.load()
        return json.loads(image.text["ValetudoMap"])


def _entities(metadata, kind):
    return [entity for entity in metadata["entities"] if entity["type"] == kind]


def _pixels(layer):
    runs = layer["compressedPixels"]
    return [
        (x + delta, y)
        for x, y, count in zip(runs[::3], runs[1::3], runs[2::3], strict=True)
        for delta in range(count)
    ]


def test_embedded_png_preserves_schema_fractional_heading_and_presentation_pixels():
    renderer = Renderer()
    expected = _metadata(renderer.render_map(_map()))
    assert expected["__class"] == "ValetudoMap"
    assert expected["metaData"] == {"version": 2, "rotation": 0}
    assert expected["size"] == {"x": 6554, "y": 6554}
    assert expected["pixelSize"] == 5
    assert _entities(expected, "robot_position") == [
        {
            "type": "robot_position",
            "points": [3282, 3267],
            "metaData": {"angle": 67.5},
        }
    ]
    buffer = BytesIO()
    original = Image.new("RGBA", (24, 18), (35, 80, 145, 125))
    original.save(buffer, format="PNG")
    result = renderer.embed_map_data(buffer.getvalue())
    with Image.open(BytesIO(result)) as image:
        assert image.size == original.size
        assert image.tobytes() == original.tobytes()
    assert _metadata(result) == expected


def test_raster_runs_reconstruct_all_pixels_without_reversing_or_merging_gaps():
    data = _map()
    data.path, data.robot_position = None, None
    data.pixel_type[:, :] = MapPixelType.OUTSIDE.value
    data.pixel_type[0, 0] = data.pixel_type[1, 0] = data.pixel_type[3, 0] = (
        MapPixelType.FLOOR.value
    )
    data.pixel_type[1, 1] = data.pixel_type[2, 1] = MapPixelType.WALL.value
    layers = _metadata(Renderer().render_map(data))["layers"]
    assert [layer["type"] for layer in layers] == ["floor", "wall"]
    assert _pixels(layers[0]) == [(655, 655), (656, 655), (658, 655)]
    assert _pixels(layers[1]) == [(656, 654), (657, 654)]
    assert layers[0]["pixels"] == []
    assert layers[0]["dimensions"] == {
        "x": {"min": 655, "max": 658, "mid": 656, "avg": 656},
        "y": {"min": 655, "max": 655, "mid": 655, "avg": 655},
        "pixelCount": 3.0,
    }


def test_zero_coordinate_average_is_a_real_number():
    data = _map()
    data.dimensions = MapImageDimensions(32767, -32768, 1, 1, 50)
    data.pixel_type = np.full((1, 1), MapPixelType.FLOOR.value, dtype=np.uint8)
    layer = _metadata(Renderer().render_map(data))["layers"][0]
    assert _pixels(layer) == [(0, 0)]
    assert layer["dimensions"]["x"]["avg"] == 0
    assert layer["dimensions"]["y"]["avg"] == 0


def test_same_length_path_survives_frame_change_and_uses_current_coordinates():
    renderer, data = Renderer(), _map()
    before = _metadata(renderer.render_map(data))
    current = deepcopy(data)
    current.frame_id += 1
    assert _entities(_metadata(renderer.render_map(current)), "path") == _entities(
        before, "path"
    )
    current.path[1].x = 150
    current.frame_id += 1
    assert _entities(_metadata(renderer.render_map(current)), "path") == [
        {
            "type": "path",
            "points": [3277, 3277, 3292, 3267],
        }
    ]


def test_mutable_frame_updates_positions_raster_and_segment_labels():
    renderer, data = Renderer(), _map()
    renderer.render_map(data)
    data.robot_position.x = 250
    data.pixel_type[0, 0] = 3
    data.segments = {3: Segment(3, custom_name="North lawn")}
    data.active_segments = [3]
    result = _metadata(renderer.render_map(data))
    assert _entities(result, "robot_position")[0]["points"] == [3302, 3267]
    segment = next(layer for layer in result["layers"] if layer["type"] == "segment")
    assert _pixels(segment) == [(655, 655)]
    assert segment["metaData"] == {"segmentId": 3, "active": True, "name": "North lawn"}
    data.segments[3].name = "Renamed lawn"
    result = _metadata(renderer.render_map(data))
    segment = next(layer for layer in result["layers"] if layer["type"] == "segment")
    assert segment["metaData"]["name"] == "Renamed lawn"


def test_segment_selection_moves_unselected_pixels_to_floor():
    renderer, data = Renderer(), _map()
    data.pixel_type[:, :] = 3
    data.segments = {3: Segment(3)}
    assert _metadata(renderer.render_map(data))["layers"][0]["type"] == "segment"
    data.active_segments = [4]
    layers = _metadata(renderer.render_map(data))["layers"]
    assert len(layers) == 1 and layers[0]["type"] == "floor"
    assert len(_pixels(layers[0])) == 8


def test_path_start_breaks_lines_without_drawing_between_sections():
    data = _map()
    data.path.extend([Path(150, 200, PathType.SWEEP), Path(200, 250, PathType.LINE)])
    assert _entities(_metadata(Renderer().render_map(data)), "path") == [
        {"type": "path", "points": [3277, 3277, 3282, 3267]},
        {"type": "path", "points": [3292, 3257, 3297, 3252]},
    ]


@pytest.mark.parametrize("sections", ["single", "starts", "trailing-start"])
def test_path_entities_contain_only_drawable_segments(sections):
    data = _map()
    expected = []
    if sections == "single":
        data.path = data.path[:1]
    elif sections == "starts":
        data.path = [Path(0, 0, PathType.SWEEP), Path(150, 200, PathType.SWEEP)]
    else:
        data.path.append(Path(150, 200, PathType.SWEEP))
        expected = [{"type": "path", "points": [3277, 3277, 3282, 3267]}]
    assert _entities(_metadata(Renderer().render_map(data)), "path") == expected


def test_restrictions_and_unknown_headings_follow_mutable_current_state():
    renderer, data = Renderer(), _map()
    data.robot_position.a = None
    data.charger_position = Point(0, 0)
    data.no_go_areas = [Area(0, 0, 50, 0, 50, 50, 0, 50)]
    data.active_areas = [Area(100, 100, 150, 100, 150, 150, 100, 150)]
    data.active_points = [Point(200, 200)]
    data.virtual_walls = [Wall(0, 0, 100, 100)]
    result = _metadata(renderer.render_map(data))
    assert _entities(result, "robot_position")[0]["metaData"] == {}
    assert _entities(result, "charger_location") == [
        {
            "type": "charger_location",
            "points": [3277, 3277],
            "metaData": {},
        }
    ]
    assert _entities(result, "no_go_area")[0]["points"] == [
        3277,
        3277,
        3282,
        3277,
        3282,
        3272,
        3277,
        3272,
    ]
    assert _entities(result, "active_zone") == [
        {
            "type": "active_zone",
            "points": [3287, 3267, 3292, 3267, 3292, 3262, 3287, 3262],
        },
        {
            "type": "active_zone",
            "points": [3222, 3332, 3372, 3332, 3372, 3182, 3222, 3182],
        },
    ]
    assert _entities(result, "virtual_wall")[0]["points"] == [3277, 3277, 3287, 3267]
    data.no_go_areas[0].x0 = 100
    data.virtual_walls[0].x1 = 200
    data.active_points.clear()
    result = _metadata(renderer.render_map(data))
    assert _entities(result, "no_go_area")[0]["points"][0] == 3287
    assert _entities(result, "virtual_wall")[0]["points"][2] == 3297
    assert len(_entities(result, "active_zone")) == 1


def test_mutable_geometry_invalidates_raster_without_replacing_map_object():
    renderer, data = Renderer(), _map()
    renderer.render_map(data)
    data.dimensions.left = 50
    data.rotation = 90
    result = _metadata(renderer.render_map(data))
    assert min(x for x, _ in _pixels(result["layers"][0])) == 656
    assert result["metaData"]["rotation"] == 90


@pytest.mark.parametrize(
    "fallback", [None, "empty", "dimensions", "raster", "shape", "small-grid"]
)
def test_missing_or_unsupported_geometry_uses_default_metadata_without_stale_entities(
    fallback,
):
    renderer, data = Renderer(), _map()
    renderer.render_map(data)
    if fallback is None:
        data = None
    elif fallback == "empty":
        data.empty_map = True
    elif fallback == "dimensions":
        data.dimensions = None
    elif fallback == "raster":
        data.pixel_type = None
    elif fallback == "shape":
        data.pixel_type = np.zeros((1, 1), dtype=np.uint8)
    else:
        data.dimensions.grid_size = 1
    default = _metadata(renderer.default_map_image)
    assert _metadata(Renderer().render_map(data)) == default
    assert _metadata(renderer.render_map(data)) == default
    assert _metadata(renderer.embed_map_data(renderer.default_map_image)) == default
    assert renderer.render_complete
    assert _metadata(renderer.render_map(_map())) != default


def test_failed_png_export_restores_completion_and_retains_last_successful_metadata(
    monkeypatch,
):
    renderer, data = Renderer(), _map()
    before_png = renderer.render_map(data)
    before = _metadata(before_png)
    original = renderer._to_buffer
    data.robot_position.x += 100
    data = deepcopy(data)
    data.frame_id += 1

    def fail(*_args):
        raise OSError("synthetic PNG output failure")

    monkeypatch.setattr(renderer, "_to_buffer", fail)
    with pytest.raises(OSError, match="synthetic PNG"):
        renderer.render_map(data)
    assert renderer.render_complete
    monkeypatch.setattr(renderer, "_to_buffer", original)
    assert _metadata(renderer.embed_map_data(before_png)) == before
    assert _metadata(renderer.render_map(data)) != before


def test_embed_requires_a_successful_render():
    renderer = Renderer()
    with pytest.raises(ValueError, match="rendered"):
        renderer.embed_map_data(renderer.default_map_image)
