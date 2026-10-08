"""Geometry comparison and obstacle metadata contracts."""

from copy import deepcopy
from datetime import datetime

import pytest

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
    RecoveryMapInfo,
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
        (0, "folder/photo-detail.jpg", "0-photo"),
        (None, "folder/photo-detail.jpg", "photo"),
        (42, None, None),
    ],
)
def test_obstacle_object_name_uses_available_vendor_identity(
    object_id, filename, expected
):
    obstacle = Obstacle(1, 2, 0, 99, object_id=object_id, file_name=filename)
    assert obstacle.object_name == expected


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


@pytest.mark.parametrize("timestamp", [None, 0, 1700000000])
def test_recovery_map_attributes_preserve_missing_and_epoch_timestamps(timestamp):
    info = {"objname": "map-object", "first": 0}
    if timestamp is not None:
        info["time"] = timestamp
    recovered = RecoveryMapInfo(7, info)
    data = MapData()
    data.recovery_map_list = [recovered]
    assert data.as_dict()["recovery_map_list"] == [{
        "date": None if timestamp is None else datetime.fromtimestamp(
            timestamp
        ).strftime("%Y-%m-%d %H:%M"),
        "map_type": "Edited",
        "object_name": "map-object",
    }]
