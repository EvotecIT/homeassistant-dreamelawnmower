"""Geometry comparison and obstacle metadata contracts."""

from copy import deepcopy

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import map_decoder
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    Area,
    CleaningHistory,
    Coordinate,
    Furniture,
    FurnitureType,
    MapData,
    MapImageDimensions,
    Obstacle,
    Point,
    Segment,
    Wall,
    Zone,
)


@pytest.mark.parametrize(
    ("value", "changed_attribute"),
    [
        (Point(1, 2), "x"),
        (Obstacle(1, 2, 0, 99), "x"),
        (Zone(1, 2, 3, 4), "x0"),
        (Segment(1), "x0"),
        (Wall(1, 2, 3, 4), "x0"),
        (Area(1, 2, 3, 4, 5, 6, 7, 8), "x0"),
        (Furniture(1, 2, 3, 4, 5, 6, FurnitureType.SINGLE_BED, 1), "x"),
        (Coordinate(1, 2, False, 0), "x"),
        (MapImageDimensions(0, 0, 10, 10, 5), "width"),
        (MapData(), "map_id"),
    ],
)
def test_map_value_equality_handles_unrelated_objects(value, changed_attribute):
    same = deepcopy(value)
    assert value == same
    assert value != object()
    assert value != None  # noqa: E711 - exercise the public equality protocol.
    setattr(same, changed_attribute, 999)
    assert value != same


@pytest.mark.parametrize(
    ("object_id", "filename", "expected"),
    [
        (42, "folder/photo-detail.jpg", "42-photo"),
        (None, "folder/photo-detail.jpg", "photo"),
        (42, None, None),
    ],
)
def test_obstacle_object_name_uses_available_vendor_identity(
    object_id, filename, expected
):
    obstacle = Obstacle(1, 2, 0, 99, object_id=object_id, file_name=filename)
    assert obstacle.object_name == expected


def test_segment_coloring_separates_neighbors_and_handles_missing_geometry() -> None:
    map_data = MapData()
    map_decoder.DreameMowerMapDecoder.set_segment_color_index(map_data)
    map_data.segments = {
        index: Segment(index, neighbors=[other for other in range(4) if other != index])
        for index in range(4)
    }
    map_decoder.DreameMowerMapDecoder.set_segment_color_index(map_data)
    colors = {segment.color_index for segment in map_data.segments.values()}
    assert colors == {0, 1, 2, 3}


def test_segment_cleaning_mode_clears_when_new_record_omits_it() -> None:
    map_data = MapData()
    segment = Segment(3)
    map_data.segments = {3: segment}
    map_decoder.DreameMowerMapDecoder.set_segment_cleanset(
        map_data, {"3": [1, 3, 2, 4, 2]}
    )
    assert segment.cleaning_mode == 2
    map_decoder.DreameMowerMapDecoder.set_segment_cleanset(
        map_data, {"3": [1, 3, 1, 0]}
    )
    assert segment.cleaning_mode is None
    assert segment.cleaning_times == 1
    assert segment.order == 0


@pytest.mark.parametrize("material,direction,rotation,code,rotated", [
    (0, None, 0, 0, None), (2, None, 0, 3, None),
    (1, 0, 0, 1, 0), (1, 0, 90, 1, 90), (1, 90, 90, 2, 0),
])
def test_floor_material_preserves_codes_and_rotated_direction(
    material: int, direction: int | None, rotation: int,
    code: int, rotated: int | None,
) -> None:
    data = MapData()
    segment = Segment(3, x0=0, y0=0, x1=100, y1=50)
    segment.floor_material = material
    segment.floor_material_direction = direction
    data.segments = {3: segment}
    data.rotation = rotation
    map_decoder.DreameMowerMapDecoder.set_floor_material(data)
    assert data.floor_material == {3: code}
    assert segment.floor_material_rotated_direction == rotated


def test_floor_material_does_not_infer_direction_without_bounds() -> None:
    data = MapData()
    segment = Segment(3)
    segment.floor_material = 1
    data.segments = {3: segment}
    map_decoder.DreameMowerMapDecoder.set_floor_material(data)
    assert data.floor_material is None


def test_material_change_clears_previously_rotated_direction() -> None:
    data = MapData()
    segment = Segment(3, x0=0, y0=0, x1=100, y1=50)
    data.segments = {3: segment}
    data.rotation = 90
    segment.floor_material = 1
    segment.floor_material_direction = 0
    map_decoder.DreameMowerMapDecoder.set_floor_material(data)
    assert segment.floor_material_rotated_direction == 90
    segment.floor_material = 2
    segment.floor_material_direction = None
    map_decoder.DreameMowerMapDecoder.set_floor_material(data)
    assert segment.floor_material_rotated_direction is None
    assert data.floor_material == {3: 3}


@pytest.mark.parametrize("has_dimensions", [False, True])
def test_point_validation_rejects_incomplete_map(has_dimensions):
    data = MapData()
    if has_dimensions:
        data.dimensions = MapImageDimensions(0, 0, 2, 2, 5)
    assert data.check_point(0, 0) is False


def test_point_validation_respects_grid_bounds_and_blocked_pixels():
    import numpy as np

    data = MapData()
    data.dimensions = MapImageDimensions(10, 20, 2, 2, 5)
    data.pixel_type = np.array([[1, 0], [255, 2]], dtype=np.uint8)
    assert data.check_point(20, 10) is True
    assert data.check_point(20, 15) is False
    assert data.check_point(25, 10) is False
    assert data.check_point(25, 15) is True
    assert data.check_point(30, 10) is False
    assert data.check_point(-1, 0, absolute=True) is False
    assert data.check_point(1, 1, absolute=True) is True


@pytest.mark.parametrize("value_key", ["value", "val"])
@pytest.mark.parametrize("filename", ["map-object", "map-object,decryption-key"])
def test_cleaning_history_preserves_map_file_identity(value_key, filename):
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        device_types,
    )

    mapping = {device_types.DreameMowerProperty.CLEAN_LOG_FILE_NAME: {"piid": 7}}
    history = CleaningHistory([{"piid": 7, value_key: filename}], mapping)
    assert history.file_name == filename
    assert history.object_name == "map-object"
    assert history.key == ("decryption-key" if "," in filename else None)


@pytest.mark.parametrize("missing", ["x0", "y0", "x1", "y1"])
def test_segment_requires_complete_bounds_for_geometry(missing):
    segment = Segment(1, x0=10, y0=20, x1=30, y1=40)
    setattr(segment, missing, None)
    assert segment.check_point(20, 30, 1) is False
    dimensions = MapImageDimensions(0, 0, 100, 100, 1)
    for convert in (segment.as_area, lambda: segment.to_img(dimensions),
                    lambda: segment.to_coord(dimensions)):
        with pytest.raises(ValueError, match="bounds are unavailable"):
            convert()
    setattr(segment, missing, {"x0": 10, "y0": 20, "x1": 30, "y1": 40}[missing])
    assert segment.check_point(20, 30, 1) is True
    assert segment.to_img(dimensions).to_coord(dimensions) == segment


@pytest.mark.parametrize("pixel", [0, 1])
def test_obstacle_assignment_uses_map_pixels_or_segment_bounds(pixel):
    import numpy as np

    data = MapData()
    data.dimensions = MapImageDimensions(0, 0, 10, 10, 10)
    data.pixel_type = np.full((10, 10), pixel, dtype=np.uint8)
    data.segments = {1: Segment(1, x0=0, y0=0, x1=100, y1=100, name="Zone 1")}
    obstacle = Obstacle(50, 50, 0, 99)
    obstacle.set_segment(data)
    assert obstacle.segment == "Zone 1"
    data.dimensions = None
    obstacle.set_segment(data)
    assert obstacle.segment == "Zone 1"
    obstacle.set_segment(None)
    assert obstacle.segment == "Zone 1"
