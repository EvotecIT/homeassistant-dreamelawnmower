"""Native app maps and encoded runtime poses must share one coordinate unit."""

from copy import deepcopy

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    app_map_geometry,
    position_tracking,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (
    map_render_style,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.vector_map import (
    DreameLawnMowerVectorBoundary,
    DreameLawnMowerVectorMap,
    DreameLawnMowerVectorPath,
    DreameLawnMowerVectorZone,
)

from .test_app_action_live_map import native_maps, tracked_client


def _coarse_native_maps():
    """Keep independent coarse geometry, rather than deriving it from a pose."""
    maps = native_maps()
    maps["maps"][0]["payload"] = {
        "map": [
            {"id": 1, "data": [[-80, -40], [120, -40], [120, 60], [-80, 60]]},
            {"id": 200, "type": 1, "data": [[-90, 0], [-80, 0]]},
        ],
        "spot": [{"id": 9, "data": [[0, 0], [10, 0], [10, 10]]}],
        "point": [[30.3, -20.3]],
        "trajectory": [{"data": [[-60, 0], [80, 20]]}],
    }
    return maps


def test_native_app_geometry_uses_the_same_units_as_the_stored_map():
    maps = _coarse_native_maps()
    original = deepcopy(maps)
    geometry = app_map_geometry.parse_app_map_geometry(maps, 0)
    # These are stored-map coordinates, specified independently of the adapter.
    stored = DreameLawnMowerVectorMap(
        zones=(DreameLawnMowerVectorZone(
            1, ((-800, -400), (1200, -400), (1200, 600), (-800, 600)),
        ),),
        paths=(DreameLawnMowerVectorPath(200, ((-900, 0), (-800, 0))),),
        boundary=DreameLawnMowerVectorBoundary(-900, -400, 1200, 600),
        map_id=1, map_index=0, current_map_id=1,
    )
    assert geometry is not None
    assert (
        position_tracking.map_position_identity(geometry)
        == position_tracking.map_position_identity(stored)
    )
    assert geometry.spot_areas[0].points == ((0, 0), (100, 0), (100, 100))
    assert geometry.clean_points == ((303, -203),)
    assert geometry.mow_paths[0].segments == (((-600, 0), (800, 200)),)
    assert maps == original


@pytest.mark.parametrize("rotation", [0, 90])
def test_coarse_app_geometry_aligns_encoded_pose_in_camera_and_scene(rotation):
    client = tracked_client()
    client._sync_get_app_maps = lambda **kwargs: _coarse_native_maps()
    style = map_render_style("dark", rotation=rotation)
    view = client._sync_refresh_vector_map_view(current_map_index=0, style=style)
    assert view.image_png
    assert view.details["runtime_position_valid"] is True
    assert (view.details["position_x"], view.details["position_y"]) == (100, 100)
    assert view.details["runtime_track_point_count"] == 3
    scene = client._sync_get_mowing_map_scene(
        map_index=0, style=style, label_scale=1,
    )
    overlay = client.mowing_map_runtime_overlay(scene)
    assert overlay["position_status"] == "current"
    assert (overlay["position"]["x"], overlay["position"]["y"]) == (
        scene.projection.point(100, 100)
    )
    assert len(overlay["trail"][0]) == 3
