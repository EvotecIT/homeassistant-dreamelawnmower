"""Geometry comparison and obstacle metadata contracts."""

from copy import deepcopy

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    Area,
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
