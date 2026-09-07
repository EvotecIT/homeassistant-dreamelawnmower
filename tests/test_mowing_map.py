"""Contracts for stable mowing backgrounds and truthful runtime overlays."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_mowing_map,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (
    map_render_style,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerStatusBlob,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.mowing_map import (
    MAX_SCENE_POINTS,
    MAX_TRAIL_POINTS,
    build_mowing_map_scene,
    mowing_map_overlay,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.vector_map import (
    DreameLawnMowerVectorBoundary,
    DreameLawnMowerVectorMap,
    DreameLawnMowerVectorMowPath,
    DreameLawnMowerVectorZone,
)

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)


def garden():
    return DreameLawnMowerVectorMap(
        map_index=2,
        map_id=7,
        name="Garden",
        boundary=DreameLawnMowerVectorBoundary(0, 0, 1000, 500),
        zones=(
            DreameLawnMowerVectorZone(
                zone_id=1,
                points=((0, 0), (1000, 0), (1000, 500), (0, 500)),
            ),
        ),
    )


def telemetry():
    return DreameLawnMowerStatusBlob(
        supported=True,
        frame_valid=True,
        received_at=NOW.isoformat(),
        candidate_runtime_pose_x=200,
        candidate_runtime_pose_y=200,
        candidate_runtime_heading_deg=0,
    )


def test_background_composites_with_the_host_without_claiming_cut_coverage():
    style = map_render_style("mint")
    scene = build_mowing_map_scene(garden(), style=style)
    with Image.open(BytesIO(scene.image_png)) as image:
        assert image.getpixel((0, 0))[3] == 0
        # An interior sample away from boundaries and the zone label.
        x, y = scene.projection.point(200, 100)
        assert 0 < image.getpixel((x, y))[3] <= 64
    # The existing camera palette remains unchanged.
    assert style.background[3] == 255
    assert style.zone_fills[0][3] == 230


@pytest.mark.parametrize("field", ["clean_points", "cruise_points"])
def test_every_rendered_point_collection_counts_against_the_budget(field):
    vector = replace(garden(), **{field: ((10, 10),) * MAX_SCENE_POINTS})
    with pytest.raises(ValueError, match="geometry budget"):
        build_mowing_map_scene(vector, style=map_render_style())


def test_input_budget_precedes_map_parsing_and_ignores_unneeded_history(monkeypatch):
    monkeypatch.setattr(client_mowing_map, "MAX_SCENE_INPUT_UNITS", 10)
    assert client_mowing_map.bounded_mowing_map_batch(
        {
            "MAP.0": "small",
            "MAP.info": 5,
            "M_PATH.0": "x" * 100,
        }
    ) == {"MAP.0": "small", "MAP.info": 5}
    with pytest.raises(ValueError, match="input budget"):
        client_mowing_map.bounded_mowing_map_batch({"MAP.0": "x" * 11})
    monkeypatch.setattr(client_mowing_map, "MAX_SCENE_CHUNKS", 1)
    with pytest.raises(ValueError, match="chunk budget"):
        client_mowing_map.bounded_mowing_map_batch({"MAP.0": "a", "MAP.1": "b"})


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_background_dimensions_and_overlay_share_projection(rotation):
    scene = build_mowing_map_scene(garden(), style=map_render_style(rotation=rotation))
    with Image.open(BytesIO(scene.image_png)) as image:
        assert image.size == (scene.projection.width, scene.projection.height)
    overlay = mowing_map_overlay(scene, telemetry(), map_index=2, active=True, now=NOW)
    position = overlay["position"]
    assert (position["x"], position["y"]) == scene.projection.point(200, 200)
    assert position["heading"] == (270 + rotation) % 360
    assert overlay["coverage_available"] is False


def test_historical_paths_do_not_change_background_or_claim_mission_coverage():
    original = garden()
    style = map_render_style()
    first = build_mowing_map_scene(original, style=style)
    original.mow_paths = (DreameLawnMowerVectorMowPath(1, (((10, 10), (500, 200)),)),)
    second = build_mowing_map_scene(original, style=style)
    assert second.revision == first.revision
    assert second.image_png == first.image_png
    recreated = build_mowing_map_scene(replace(original, map_id=8), style=style)
    assert recreated.revision != first.revision


@pytest.mark.parametrize(
    "change,status",
    [
        ({"frame_valid": False}, "unavailable"),
        ({"received_at": (NOW - timedelta(seconds=91)).isoformat()}, "stale"),
        ({"received_at": (NOW + timedelta(seconds=6)).isoformat()}, "stale"),
        ({"received_at": "invalid"}, "unavailable"),
        ({"candidate_runtime_pose_x": 1001}, "out_of_bounds"),
        ({"candidate_runtime_pose_x": float("nan")}, "out_of_bounds"),
    ],
)
def test_unverified_position_is_not_shown(change, status):
    scene = build_mowing_map_scene(garden(), style=map_render_style())
    overlay = mowing_map_overlay(
        scene,
        replace(telemetry(), **change),
        map_index=2,
        active=True,
        now=NOW,
    )
    assert overlay["position"] is None
    assert overlay["position_status"] == status


def test_map_mismatch_hides_both_marker_and_trail():
    scene = build_mowing_map_scene(garden(), style=map_render_style())
    overlay = mowing_map_overlay(
        scene,
        telemetry(),
        map_index=1,
        active=True,
        now=NOW,
        track_segments=(((10, 10), (20, 20)),),
    )
    assert overlay["position"] is None
    assert overlay["trail"] == []
    assert overlay["position_status"] == "map_mismatch"


def test_trail_preserves_gaps_and_stays_bounded():
    scene = build_mowing_map_scene(garden(), style=map_render_style())
    overlay = mowing_map_overlay(
        scene,
        telemetry(),
        map_index=2,
        active=True,
        now=NOW,
        track_segments=(((10, 10), (20, 20), (2000, 2000), (30, 30), (40, 40)),),
    )
    assert [len(part) for part in overlay["trail"]] == [2, 2]
    crowded = mowing_map_overlay(
        scene,
        telemetry(),
        map_index=2,
        active=True,
        now=NOW,
        track_segments=(tuple((n % 500, 100) for n in range(MAX_TRAIL_POINTS + 100)),),
    )
    assert sum(len(part) for part in crowded["trail"]) == MAX_TRAIL_POINTS
    ended = mowing_map_overlay(
        scene,
        telemetry(),
        map_index=2,
        active=False,
        now=NOW,
        track_segments=(((10, 10), (20, 20)),),
    )
    assert ended["trail"] == []


def test_camera_and_interactive_overlay_share_dock_evidence_after_session_ends():
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        client_position,
        position_tracking,
    )

    now = datetime.now(UTC)
    tracker = position_tracking.MowerPositionTracker()
    tracker.record(replace(telemetry(), received_at=now.isoformat()), map_index=2)
    client = SimpleNamespace(
        _position_tracker=tracker,
        _latest_snapshot=SimpleNamespace(
            available=True,
            activity="docked",
            docked=True,
            state_event_at=(now - timedelta(seconds=2)).timestamp(),
        ),
        _latest_runtime_status_blob=None,
        _runtime_live_map_index=None,
        _runtime_session_active=False,
        _runtime_live_track_segments=(),
    )
    position = client_position.vector_map_position(client, garden())
    scene = build_mowing_map_scene(garden(), style=map_render_style())
    mixin = client_mowing_map._DreameLawnMowerClientMowingMapMixin
    overlay = mixin.mowing_map_runtime_overlay(client, scene)
    assert position.status == overlay["position_status"] == "known_dock"
    assert overlay["position_observed_at"] == position.details()["position_observed_at"]
    assert overlay["position"]["x"] == scene.projection.point(position.x, position.y)[0]
    assert overlay["trail"] == []
    assert overlay["docked"] is True


def test_interactive_client_does_not_bypass_replaced_geometry_rejection():
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        position_tracking,
    )

    now = datetime.now(UTC)
    blob = replace(telemetry(), received_at=now.isoformat())
    tracker = position_tracking.MowerPositionTracker()
    tracker.record(blob, map_index=2)
    client = SimpleNamespace(
        _position_tracker=tracker,
        _latest_snapshot=SimpleNamespace(available=True, activity="mowing"),
        _latest_runtime_status_blob=blob,
        _runtime_live_task_id=None,
        _runtime_live_map_index=2,
        _runtime_session_active=True,
        _runtime_live_track_segments=(((10, 10), (20, 20)),),
    )
    mixin = client_mowing_map._DreameLawnMowerClientMowingMapMixin
    first = build_mowing_map_scene(garden(), style=map_render_style())
    assert mixin.mowing_map_runtime_overlay(client, first)["position"] is not None
    replaced = build_mowing_map_scene(
        replace(garden(), map_id=8), style=map_render_style()
    )
    overlay = mixin.mowing_map_runtime_overlay(client, replaced)
    assert overlay["position"] is None
    assert overlay["trail"] == []


def test_current_marker_expiry_remains_bound_to_measurement_time():
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        position_tracking,
    )

    observed = NOW - timedelta(seconds=89)
    position = position_tracking.MowerPosition(200, 200, 0, observed.timestamp(), 2)
    scene = build_mowing_map_scene(garden(), style=map_render_style())
    overlay = mowing_map_overlay(
        scene,
        replace(telemetry(), received_at=observed.isoformat()),
        map_index=2,
        active=True,
        retained_position=position,
        now=NOW,
    )
    assert overlay["position_status"] == "current"
    assert overlay["updated_at"] == overlay["position_observed_at"]
