"""A sleeping docked mower needs no live-session lease to use its station."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.app_map_geometry import (  # noqa: E501
    parse_app_map_geometry,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client_vector_map_view import (  # noqa: E501
    geometry_map_view,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (  # noqa: E501
    map_render_style,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.position_tracking import (  # noqa: E501
    map_position_identity,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.vector_map import (  # noqa: E501
    position_within_vector_map,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.zone_mowing_history import (  # noqa: E501
    ChargerPosition,
    ZoneMowingHistory,
)

from .test_app_action_live_map import native_maps
from .test_position_tracking import pose
from .test_vector_map import _client


@pytest.mark.parametrize("restored_history", [False, True])
def test_docked_restart_uses_matching_station_without_live_lease(restored_history):
    client = _client()
    maps = native_maps()
    vector = parse_app_map_geometry(maps, 0)
    now = datetime.now(UTC)
    if restored_history:
        last_night = now - timedelta(hours=12)
        client._position_tracker.record(
            pose(last_night, x=800), map_index=0, now=last_night
        )
        client._position_tracker.resolve(
            map_index=0,
            geometry=map_position_identity(vector),
            contains=lambda x, y: position_within_vector_map(vector, (x, y)),
            snapshot=SimpleNamespace(available=True, activity="mowing"),
            now=last_night,
        )
        saved = client._position_tracker.checkpoint(now=now)
        client = _client()
        client._position_tracker.restore_checkpoint(saved, now=now)
    client.runtime_map_identity_expires_at = None
    client._latest_snapshot = SimpleNamespace(
        available=True,
        activity="docked",
        state="charging_completed",
        docked=True,
    )
    client._charger_history = ZoneMowingHistory(
        chargers={
            "summary": ChargerPosition(200, 400, 0, 1, maps["maps"][0]["md5"]),
        }
    )
    checkpoint = client._position_tracker.checkpoint(now=now)
    view = geometry_map_view(
        client,
        vector,
        source="app_action_map",
        app_maps=maps,
        render_image=False,
        geometry_generation=client._runtime_geometry_generation,
    )
    assert (view.details["charger_x"], view.details["charger_y"]) == (200, 400)
    assert (view.details["position_x"], view.details["position_y"]) == (200, 400)
    assert view.details["position_status"] == "known_dock"
    scene = client._mowing_map_scene_from_batch(
        {},
        map_index=0,
        app_maps=maps,
        style=map_render_style(),
        label_scale=1,
        geometry_generation=client._runtime_geometry_generation,
    )
    overlay = client.mowing_map_runtime_overlay(scene)
    assert overlay["position_status"] == "known_dock"
    assert overlay["position"]["x"] == scene.projection.point(200, 400)[0]
    assert overlay["trail"] == []
    assert client._runtime_map_identity_expires_at is None
    assert client._position_tracker.checkpoint(now=now) == checkpoint
