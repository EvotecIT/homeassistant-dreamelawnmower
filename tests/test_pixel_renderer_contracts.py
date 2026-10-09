"""Pixel renderer contracts at supported map, Pillow and wire boundaries."""

import json
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from dreame_lawn_mower_client._loader import load_internal_module

_renderer = load_internal_module("map_renderer")
_types = load_internal_module("map_types")
_device_types = load_internal_module("device_types")


def _frame():
    data = _types.MapData()
    data.map_id, data.frame_id = 1, 2
    data.empty_map, data.saved_map = False, False
    data.rotation = 0
    data.dimensions = _types.MapImageDimensions(0, 0, 18, 24, 50)
    data.pixel_type = np.full((24, 18), 253, dtype=np.uint8)
    data.data = bytes([253] * 432)
    data.segments = {}
    data.robot_position = _types.Point(300, 300, 0)
    data.last_updated = 0
    return data


def _pixels(png):
    with Image.open(BytesIO(png)) as image:
        image.load()
        return image.size, image.tobytes()


@pytest.mark.parametrize("missing", ["dimensions", "pixels", "shape"])
def test_partial_current_map_returns_default_png_and_empty_wire_data(missing):
    data = _frame()
    if missing == "dimensions":
        data.dimensions = None
    elif missing == "pixels":
        data.pixel_type = None
    else:
        data.pixel_type = np.zeros((1, 1), dtype=np.uint8)
    renderer = _renderer.DreameMowerMapRenderer(cache=False)
    assert _pixels(renderer.render_map(data)) == _pixels(renderer.default_map_image)
    assert json.loads(renderer.get_data_string(data)) == {}
    assert renderer.render_complete


def test_active_and_predefined_points_export_current_fractional_coordinates():
    data = _frame()
    data.active_points = [_types.Point(12.5, -7.25)]
    data.predefined_points = {1: _types.Coordinate(0, 3.5, False, 0)}
    exported = json.loads(_renderer.DreameMowerMapRenderer().get_data_string(data))
    assert exported["active_points"] == [[12.5, -7.25]]
    assert exported["predefined_points"] == [[0, 3.5]]


@pytest.mark.parametrize("robot_type", [0, _device_types.RobotType.VSLAM])
def test_resources_support_constructor_robot_type_and_serializable_empty_map(
    robot_type,
):
    renderer = _renderer.DreameMowerMapRenderer(robot_type=robot_type)
    capability = SimpleNamespace(
        customized_cleaning=False, wifi_map=False, camera_streaming=False
    )
    resources = renderer.get_resources(capability)
    assert resources.robot_type == int(robot_type)
    assert resources.robot and resources.charger and resources.font
    empty = _types.MapData()
    empty.empty_map = True
    exported = json.loads(renderer.get_data_string(empty, resources))
    assert exported["resources"]["robot_type"] == int(robot_type)
    assert exported["resources"]["robot"] == resources.robot


def test_path_stroke_follows_current_map_coordinates():
    renderer = _renderer.DreameMowerMapRenderer(cache=False)
    dimensions = _types.MapImageDimensions(0, 0, 20, 20, 50)
    path = [
        _types.Path(100, 100, _device_types.PathType.SWEEP),
        _types.Path(700, 100, _device_types.PathType.LINE),
    ]
    layer = renderer.render_path(
        path, (255, 0, 0, 255), (20, 20), None, dimensions, 1, 1
    )
    assert layer.getbbox() is not None
    assert layer.getpixel((8, 17)) == (255, 0, 0, 255)
    assert layer.getpixel((8, 8))[3] == 0


def test_cached_render_observes_updates_to_same_map_instance():
    data = _frame()
    renderer = _renderer.DreameMowerMapRenderer(cache=True)
    first = _pixels(renderer.render_map(data))
    data.robot_position = _types.Point(800, 600, 90)
    second = _pixels(renderer.render_map(data))
    assert first != second
    assert renderer.render_complete


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_cached_rotation_matches_fresh_render(rotation):
    data = _frame()
    renderer = _renderer.DreameMowerMapRenderer(cache=True)
    renderer.render_map(data)
    data.rotation = rotation
    rotated = renderer.render_map(data)
    assert _pixels(rotated) == _pixels(
        _renderer.DreameMowerMapRenderer(cache=False).render_map(data)
    )
    assert _pixels(rotated) != _pixels(renderer.default_map_image)


@pytest.mark.parametrize("failure_stage", ["objects", "snapshot"])
def test_failed_update_retains_previous_image_and_calibration_then_recovers(
    failure_stage,
    monkeypatch,
):
    data = _frame()
    renderer = _renderer.DreameMowerMapRenderer(cache=True)
    previous = _pixels(renderer.render_map(data))
    calibration = renderer.calibration_points
    data.rotation = 90

    def fail(*_args, **_kwargs):
        raise OSError("synthetic object layer failure")

    with monkeypatch.context() as patch:
        if failure_stage == "objects":
            patch.setattr(renderer, "render_objects", fail)
        else:
            patch.setattr(_renderer.copy, "deepcopy", fail)
        assert _pixels(renderer.render_map(data)) == previous
    assert renderer.calibration_points == calibration
    assert renderer.render_complete
    assert _pixels(renderer.render_map(data)) == _pixels(
        _renderer.DreameMowerMapRenderer(cache=False).render_map(data)
    )


@pytest.mark.parametrize(
    "cache,failure_stage", [(True, "pillow"), (False, "pillow"), (True, "snapshot")]
)
def test_first_render_failure_returns_default_png_and_restores_completion(
    cache, failure_stage, monkeypatch
):
    renderer = _renderer.DreameMowerMapRenderer(cache=cache)
    expected = _pixels(renderer.default_map_image)

    def fail(*_args, **_kwargs):
        raise OSError("synthetic Pillow allocation failure")

    if failure_stage == "pillow":
        monkeypatch.setattr(_renderer.Image, "fromarray", fail)
    else:
        monkeypatch.setattr(_renderer.copy, "deepcopy", fail)
    assert _pixels(renderer.render_map(_frame())) == expected
    assert renderer.calibration_points is None
    assert renderer.render_complete


def test_no_floor_material_pattern_returns_original_pixel_array():
    image = np.full((24, 24, 4), 200, dtype=np.uint8)
    before = image.copy()
    dimensions = _types.MapImageDimensions(0, 0, 12, 12, 50)
    pixels = np.ones((12, 12), dtype=np.uint8)
    result = _renderer.DreameMowerMapRenderer().render_floor_material(
        image, {1: 0}, pixels, (0, 0, 0, 20), dimensions, 2
    )
    assert result is image
    np.testing.assert_array_equal(result, before)
