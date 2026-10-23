"""Station coordinates require the current map and accepted geometry."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client_vector_map_view import (  # noqa: E501
    geometry_map_view,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (  # noqa: E501
    map_render_style,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.mowing_map import (  # noqa: E501
    build_mowing_map_scene,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.vector_map import (  # noqa: E501
    parse_batch_vector_map,
    with_charger,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.zone_mowing_history import (  # noqa: E501
    ChargerPosition,
    ZoneMowingHistory,
)

from .test_vector_map import _batch_payload, _client
from .test_zone_mowing_history import _maps

HASH = "a" * 32
OTHER_HASH = "b" * 32


def docked_client():
    client = _client()
    client._latest_snapshot = SimpleNamespace(
        available=True, docked=True, activity="docked"
    )
    client._runtime_map_identity_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    client._charger_history = ZoneMowingHistory(
        chargers={
            "old": ChargerPosition(20, 40, 0, 1, HASH),
            "new": ChargerPosition(100, 60, 0, 2, OTHER_HASH),
        }
    )
    return client


def test_cached_history_selects_station_for_each_current_revision():
    client = docked_client()
    maps = _maps()
    vector = parse_batch_vector_map(_batch_payload())
    first = geometry_map_view(
        client, vector, source="batch_vector_map", app_maps=maps, render_image=False
    )
    assert (first.details["charger_x"], first.details["charger_y"]) == (20, 40)
    maps["current_map_index"] = 1
    second = geometry_map_view(
        client,
        replace(vector, map_index=1),
        source="batch_vector_map",
        app_maps=maps,
        render_image=False,
    )
    assert (second.details["charger_x"], second.details["charger_y"]) == (100, 60)
    assert second.details["position_source"] == "reported_charging_station"


@pytest.mark.parametrize("runtime_index", [None, 1])
def test_rejected_interactive_geometry_cannot_manufacture_docked_position(
    runtime_index,
):
    client = docked_client()
    vector = with_charger(
        parse_batch_vector_map(_batch_payload()), ChargerPosition(20, 40, 0, 1, HASH)
    )
    scene = build_mowing_map_scene(vector, style=map_render_style())
    client._runtime_live_geometry_identity = "different-verified-geometry"
    client._runtime_live_map_index = runtime_index
    overlay = client.mowing_map_runtime_overlay(scene)
    assert overlay["position"] is None
    assert overlay["position_status"] != "known_dock"


@pytest.mark.parametrize("revision_state", ["missing", "unverified", "other"])
def test_unverified_or_unmatched_revision_has_no_station(revision_state):
    client = docked_client()
    # A station that moved between summaries is not carried to a new revision.
    client._charger_history = ZoneMowingHistory(
        chargers={
            "old": ChargerPosition(20, 40, 0, 1, HASH),
            "new": ChargerPosition(2000, 60, 0, 2, OTHER_HASH),
        }
    )
    maps = _maps()
    if revision_state == "missing":
        maps = None
    elif revision_state == "unverified":
        maps["maps"][0]["hash_match"] = False
    else:
        maps["maps"][0].update(md5="c" * 32, info={"hash": "c" * 32})
    view = geometry_map_view(
        client,
        parse_batch_vector_map(_batch_payload()),
        source="batch_vector_map",
        app_maps=maps,
        render_image=False,
    )
    assert view.details["charger_x"] is None
    assert view.details["position_x"] is None


def test_new_revision_keeps_a_station_that_never_moved():
    client = docked_client()
    maps = _maps()
    maps["maps"][0].update(md5="c" * 32, info={"hash": "c" * 32})
    view = geometry_map_view(
        client,
        parse_batch_vector_map(_batch_payload()),
        source="batch_vector_map",
        app_maps=maps,
        render_image=False,
    )
    assert (view.details["charger_x"], view.details["charger_y"]) == (100, 60)
    assert view.details["position_source"] == "reported_charging_station"


@pytest.mark.parametrize("rejected", ["foreign_map", "overtaken"])
def test_camera_cannot_replace_rejected_frame_with_docked_position(rejected):
    client = docked_client()
    vector = parse_batch_vector_map(_batch_payload())
    generation = client._runtime_geometry_generation
    if rejected == "foreign_map":
        client._runtime_geometry_map_index = 1
        client._runtime_live_map_index = 1
    elif rejected == "overtaken":
        client._runtime_geometry_revision += 1
        client._observed_map_geometry = (0, "newer-frame")
    else:
        client._runtime_map_identity_expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
    view = geometry_map_view(
        client,
        vector,
        source="batch_vector_map",
        app_maps=_maps(),
        render_image=False,
        geometry_generation=generation,
    )
    assert view.details["position_x"] is None
    assert view.details["position_status"] != "known_dock"


def test_camera_and_interactive_scene_share_current_station_and_docked_point():
    client = docked_client()
    maps = _maps()
    batch = _batch_payload()
    scene = client._mowing_map_scene_from_batch(
        batch,
        map_index=0,
        style=map_render_style(),
        label_scale=1,
        app_maps=maps,
        geometry_generation=client._runtime_geometry_generation,
    )
    overlay = client.mowing_map_runtime_overlay(scene)
    view = geometry_map_view(
        client,
        parse_batch_vector_map(batch),
        source="batch_vector_map",
        app_maps=maps,
        render_image=False,
    )
    assert scene.charger == (20, 40)
    assert overlay["position"]["x"] == scene.projection.point(20, 40)[0]
    assert overlay["position_status"] == view.details["position_status"] == "known_dock"
    assert overlay["position_source"] == view.details["position_source"]
    assert (view.details["position_x"], view.details["position_y"]) == (20, 40)
    client._runtime_map_identity_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    expired = client.mowing_map_runtime_overlay(scene)
    assert expired["position_status"] == "known_dock"
    assert expired["trail"] == []
    assert client._runtime_live_map_index is None


def test_cached_history_refresh_keeps_per_revision_evidence_without_cloud_read():
    import asyncio

    from custom_components.dreame_lawn_mower.coordinator import (
        DreameLawnMowerCoordinator,
    )

    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.client = docked_client()
    coordinator.zone_mowing_sessions = coordinator.client._charger_history
    coordinator.zone_mowing_sessions_refreshed_at = datetime.now(UTC)
    coordinator.client._charger_history = None
    retained = asyncio.run(coordinator.async_refresh_zone_mowing_sessions())
    assert coordinator.client._charger_history is retained
    assert retained.latest_charger(HASH).x == 20
    assert retained.latest_charger(OTHER_HASH).x == 100


def test_interactive_background_context_tracks_current_station_evidence():
    from .test_mowing_map_api import environment

    _, coordinator, api = environment(None)
    coordinator.app_maps = _maps()
    for entry in coordinator.app_maps["maps"]:
        entry["info"]["size"] = 100
    coordinator.client._charger_history = docked_client()._charger_history
    first = api.context(coordinator)
    assert first[-1].map_hash == HASH
    coordinator.app_maps["current_map_index"] = 1
    second = api.context(coordinator)
    assert second[-1].map_hash == OTHER_HASH
    assert first != second


@pytest.mark.parametrize("native", [False, True])
def test_normal_camera_keeps_station_without_a_mower_pose(monkeypatch, native):
    import asyncio

    from .test_app_action_live_map import native_maps
    from .test_map_source_failures import configure_reads

    maps = native_maps()
    revision = maps["maps"][0]["md5"]
    client = docked_client()
    client._latest_snapshot = SimpleNamespace(available=True, activity="idle")
    client._charger_history = ZoneMowingHistory(
        chargers={"summary": ChargerPosition(200, 400, 0, 1, revision)}
    )
    configure_reads(monkeypatch, client, lambda: {}, lambda: maps)
    view = (
        asyncio.run(client.async_refresh_map_view(timeout=0, interval=0))
        if native
        else (client._sync_refresh_map_view(timeout=0, interval=0))
    )
    assert view.image_png
    assert (view.details.get("charger_x"), view.details.get("charger_y")) == (200, 400)
    assert view.details["position_x"] is None
    assert view.summary.charger_present


def test_live_path_camera_passes_cached_inventory_into_public_vector_read(monkeypatch):
    import asyncio

    from custom_components.dreame_lawn_mower.camera import (
        DreameLawnMowerLivePathMapCamera,
    )
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_inventory import (  # noqa: E501
        _app_map_inventory_identity,
    )

    from .test_map_camera_identity import _camera
    from .test_map_source_failures import configure_reads

    client = docked_client()
    maps = _maps()
    for entry in maps["maps"]:
        entry["info"]["size"] = 100
    client._latest_app_map_inventory_identity = _app_map_inventory_identity(
        maps["maps"]
    )
    configure_reads(monkeypatch, client, _batch_payload, lambda: maps)
    entity = _camera()
    entity.coordinator = SimpleNamespace(client=client, app_maps=maps)
    view = asyncio.run(DreameLawnMowerLivePathMapCamera._async_refresh_map_view(entity))
    assert view.image_png
    assert (view.details.get("charger_x"), view.details.get("charger_y")) == (20, 40)


def test_camera_render_context_changes_when_current_station_arrives():
    import json

    from custom_components.dreame_lawn_mower.camera import DreameLawnMowerMapCamera

    from .test_map_camera_identity import _camera

    entity = _camera()
    entity.coordinator = SimpleNamespace(
        client=docked_client(), app_maps=_maps(), runtime_status_blob=None,
        entry=SimpleNamespace(options={}),
    )
    initial = DreameLawnMowerMapCamera._map_refresh_context.fget(entity)
    assert initial[-1] == (20, 40, HASH)
    entity.coordinator.client._charger_history = ZoneMowingHistory()
    missing = DreameLawnMowerMapCamera._map_refresh_context.fget(entity)
    assert missing[-1] is None
    assert initial != missing
    # Camera preview scope remains portable, primitive data.
    assert json.loads(json.dumps(initial))[-1] == [20, 40, HASH]
