"""Station coordinates require the current map and accepted geometry."""

import json
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
    ZoneGeometry,
    ZoneMowingHistory,
    reported_charger_for_map,
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


FRONT_YARD = ((0, 0), (100, 0), (100, 100), (0, 100))
RESAVED = "c" * 32


def lineage_history(boundary=FRONT_YARD):
    """Two summarized revisions; the newest reported this lawn-zone boundary."""
    return ZoneMowingHistory(
        chargers={
            "old": ChargerPosition(20, 40, 0, 1, HASH),
            "new": ChargerPosition(100, 60, 0, 2, OTHER_HASH),
        },
        geometries={"new": ZoneGeometry(OTHER_HASH, {1: boundary}, 2)},
    )


def resaved_view(client):
    maps = _maps()
    maps["maps"][0].update(md5=RESAVED, info={"hash": RESAVED})
    view = geometry_map_view(
        client,
        parse_batch_vector_map(_batch_payload()),
        source="batch_vector_map",
        app_maps=maps,
        render_image=False,
    )
    return maps, view


def test_resaved_revision_inherits_the_station_of_its_verified_ancestor():
    client = docked_client()
    client._charger_history = lineage_history()
    maps, view = resaved_view(client)
    assert (view.details["charger_x"], view.details["charger_y"]) == (100, 60)
    assert view.details["position_source"] == "reported_charging_station"
    assert client._map_lineage == {RESAVED: OTHER_HASH}
    # Paths without geometry (camera context, background key) read the same answer.
    assert reported_charger_for_map(
        client._charger_history, maps, 0, client._map_lineage
    ) == ChargerPosition(100, 60, 0, 2, OTHER_HASH)


def test_recreated_map_with_nearby_station_coordinates_stays_unknown():
    client = docked_client()
    # Same station coordinates, but no lawn zone kept its exact boundary.
    client._charger_history = lineage_history(
        tuple((x + 10, y) for x, y in FRONT_YARD)
    )
    maps, view = resaved_view(client)
    assert view.details["charger_x"] is None
    assert view.details["position_x"] is None
    assert client._map_lineage == {}
    assert reported_charger_for_map(
        client._charger_history, maps, 0, client._map_lineage
    ) is None


def test_lineage_is_withdrawn_when_the_geometry_no_longer_matches():
    client = docked_client()
    client._charger_history = lineage_history()
    resaved_view(client)
    assert client._map_lineage == {RESAVED: OTHER_HASH}
    client._charger_history = lineage_history(((0, 0), (90, 0), (90, 90)))
    _, view = resaved_view(client)
    assert client._map_lineage == {}
    assert view.details["charger_x"] is None


@pytest.mark.parametrize("surface", ["view", "scene"])
@pytest.mark.parametrize("stale_matches", [False, True])
def test_overtaken_geometry_cannot_change_the_accepted_lineage(
    surface, stale_matches,
):
    client = docked_client()
    client._charger_history = lineage_history()
    maps = _maps()
    maps["maps"][0].update(md5=RESAVED, info={"hash": RESAVED})
    batch = _batch_payload()
    matching = parse_batch_vector_map(batch)
    raw = "".join(batch[f"MAP.{index}"] for index in range(3))
    payload = json.loads(json.loads(raw[:int(batch["MAP.info"])])[0])
    for point in payload["mowingAreas"]["value"][0][1]["path"]:
        point["x"] += 1
    encoded = json.dumps([json.dumps(payload)])
    different_batch = {"MAP.0": encoded, "MAP.info": str(len(encoded))}
    different = parse_batch_vector_map(different_batch)
    generation = client._runtime_geometry_generation
    geometry_map_view(
        client, different if stale_matches else matching,
        source="batch_vector_map", app_maps=maps, render_image=False,
    )
    accepted = dict(client._map_lineage)
    station = reported_charger_for_map(
        client._charger_history, maps, 0, client._map_lineage
    )
    stale = matching if stale_matches else different
    if surface == "view":
        geometry_map_view(
            client, stale, source="batch_vector_map", app_maps=maps,
            render_image=False, geometry_generation=generation,
        )
    else:
        client._mowing_map_scene_from_batch(
            batch if stale_matches else different_batch,
            map_index=0, style=map_render_style(), label_scale=1,
            app_maps=maps, geometry_generation=generation,
        )
    assert client._map_lineage == accepted
    assert reported_charger_for_map(
        client._charger_history, maps, 0, client._map_lineage
    ) == station


@pytest.mark.parametrize("history_changes", [False, True])
@pytest.mark.parametrize("station_x", [100, -200])
def test_first_api_scene_accepts_only_its_own_lineage_discovery(
    history_changes, station_x,
):
    import asyncio

    from aiohttp import web

    from .test_mowing_map_api import environment

    async def run():
        client = docked_client()
        client._charger_history = lineage_history()
        client._charger_history.chargers["new"] = ChargerPosition(
            station_x, 60, 0, 2, OTHER_HASH,
        )
        maps = _maps()
        maps["maps"][0].update(md5=RESAVED, info={"hash": RESAVED, "size": 100})
        for item in maps["maps"]:
            item["info"]["size"] = 100
        calls = 0

        async def load(**kwargs):
            nonlocal calls
            calls += 1
            scene = client._mowing_map_scene_from_batch(
                _batch_payload(),
                geometry_generation=client._runtime_geometry_generation,
                **kwargs,
            )
            if history_changes:
                # Even an unchanged x/y is new evidence, not lineage discovery.
                client._charger_history = ZoneMowingHistory(chargers={
                    "current": ChargerPosition(station_x, 60, 0, 3, RESAVED),
                })
            return scene

        _, coordinator, api = environment(load)
        coordinator.app_maps = maps
        coordinator.client = client
        client.async_get_mowing_map_scene = load
        if history_changes:
            with pytest.raises(web.HTTPConflict):
                await api.scene("entry")
            assert not api._cache
        else:
            entry = await api.scene("entry")
            assert entry.scene.charger == (max(-10, station_x), 60)
            assert await api.scene("entry") is entry
            assert calls == 1

    asyncio.run(run())


@pytest.mark.parametrize("history_changes", [False, True])
@pytest.mark.parametrize("station_x", [100, -200])
def test_first_camera_render_accepts_only_its_own_lineage_discovery(
    monkeypatch, history_changes, station_x,
):
    import asyncio
    from io import BytesIO

    from PIL import Image

    from custom_components.dreame_lawn_mower.camera import DreameLawnMowerMapCamera

    from .test_map_camera_identity import _camera

    client = docked_client()
    client._charger_history = lineage_history()
    client._charger_history.chargers["new"] = ChargerPosition(
        station_x, 60, 0, 2, OTHER_HASH,
    )
    maps = _maps()
    maps["maps"][0].update(md5=RESAVED, info={"hash": RESAVED})
    entity = _camera()
    monkeypatch.setattr(type(entity), "_map_refresh_context",
                        DreameLawnMowerMapCamera._map_refresh_context)
    entity.coordinator = SimpleNamespace(
        client=client, app_maps=maps, runtime_status_blob=None,
        entry=SimpleNamespace(options={}),
    )
    entity._last_refresh_context = entity._map_refresh_context

    async def refresh():
        view = geometry_map_view(
            client, parse_batch_vector_map(_batch_payload()),
            source="batch_vector_map", app_maps=maps,
        )
        if history_changes:
            client._charger_history = ZoneMowingHistory(chargers={
                "current": ChargerPosition(station_x, 60, 0, 3, RESAVED),
            })
        return view

    async def executor(func, *args):
        return func(*args)

    entity._async_refresh_map_view = refresh
    entity.hass.async_add_executor_job = executor
    result = asyncio.run(entity._async_refresh_and_render_map_image())
    if history_changes:
        assert result is None
        assert entity._map_cache.last_image is None
    else:
        assert result is not None
        with Image.open(BytesIO(result)) as image:
            assert image.format == "JPEG"
        assert entity._map_cache.last_image == result
        assert not entity._map_refresh_pending
        assert entity._last_refresh_context == entity._map_refresh_context


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
