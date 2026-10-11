"""Native app payload compatibility and map-source failure contracts."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.control_options import current_zone_entries
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    app_map_geometry,
    client_map_helpers,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerConnectionError,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerMapView,
)
from custom_components.dreame_lawn_mower.map_attributes import map_camera_attributes
from custom_components.dreame_lawn_mower.sensor_map_data import (
    current_app_map_attributes,
)

from .test_app_action_live_map import native_maps, publish, tracked_client
from .test_app_maps import _FakeAppMapCloud
from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_map_objects import make_client


def test_native_polygons_without_ids_keep_stable_distinct_render_identities():
    maps = native_maps()
    payload = maps["maps"][0]["payload"]
    del payload["map"][0]["id"]
    del payload["spot"][0]["id"]
    another = deepcopy(payload["map"][0])
    another["id"] = -1
    payload["map"].append(another)
    geometry = app_map_geometry.parse_app_map_geometry(maps, 0)
    repeated = app_map_geometry.parse_app_map_geometry(deepcopy(maps), 0)
    assert geometry is not None and repeated == geometry
    assert len({zone.zone_id for zone in geometry.zones}) == 2
    assert geometry.zones[1].zone_id == -1
    assert len(geometry.spot_areas) == 1
    client = tracked_client()
    client._sync_get_app_maps = lambda **kwargs: maps
    view = client._sync_refresh_map_view(timeout=0, interval=0)
    assert view.details["runtime_position_valid"] is True
    assert view.details["runtime_track_point_count"] == 3
    assert current_zone_entries({}, maps, client._sync_get_vector_map_details()) == []


def test_duplicate_explicit_lawn_ids_cannot_establish_a_position_frame():
    maps = native_maps()
    payload = maps["maps"][0]["payload"]
    payload["map"].append(deepcopy(payload["map"][0]))
    assert app_map_geometry.parse_app_map_geometry(maps, 0) is None


@pytest.mark.parametrize("valid_sibling", [False, True])
def test_zero_area_lawn_cannot_establish_a_runtime_frame(valid_sibling):
    maps = native_maps()
    lawns = maps["maps"][0]["payload"]["map"]
    invalid = {"id": 2, "data": [[0, 0], [500, 500], [1000, 1000]]}
    if valid_sibling:
        lawns.append(invalid)
    else:
        lawns[0] = invalid
    assert app_map_geometry.parse_app_map_geometry(maps, 0) is None
    client = tracked_client()
    client._sync_get_app_maps = lambda **kwargs: maps
    view = client._sync_refresh_vector_map_view(current_map_index=0)
    assert not view.available
    assert not (view.details or {}).get("runtime_position_valid")


def test_zero_area_optional_spot_cannot_enlarge_the_runtime_frame():
    maps = native_maps()
    maps["maps"][0]["payload"]["spot"] = [
        {"id": 9, "data": [[0, 0], [10000, 10000], [20000, 20000]]},
    ]
    geometry = app_map_geometry.parse_app_map_geometry(maps, 0)
    assert geometry is not None
    assert not geometry.spot_areas
    assert geometry.boundary.x2 == 1000 and geometry.boundary.y2 == 500


@pytest.mark.parametrize("reverse", [False, True])
def test_lawn_winding_and_open_two_point_pathways_remain_supported(reverse):
    maps = native_maps()
    lawns = maps["maps"][0]["payload"]["map"]
    if reverse:
        lawns[0]["data"].reverse()
    lawns[1]["data"] = [[800, 100], [900, 200]]
    geometry = app_map_geometry.parse_app_map_geometry(maps, 0)
    assert geometry is not None
    assert len(geometry.zones) == 1 and len(geometry.paths[0].points) == 2


@pytest.mark.parametrize("object_error", [None, "object lookup failed"])
@pytest.mark.parametrize("changed_slot", [None, 0, 1])
def test_sync_live_camera_preserves_primary_object_metadata(object_error, changed_slot):
    client = tracked_client()

    def maps_with_objects(**kwargs):
        maps = native_maps()
        other = deepcopy(maps["maps"][0])
        other.update(idx=1, current=False)
        maps["maps"].append(other)
        if kwargs.get("include_objects", True):
            maps["objects"] = {
                "object_count": 1, "error": object_error,
                "objects": [{"extension": "bin", "url_present": False,
                             "name": "private-name", "url": "private-url"}],
            }
        elif changed_slot is not None:
            entry = maps["maps"][changed_slot]
            entry["payload"]["name"] = "edited"
            cloud = _FakeAppMapCloud(entry["payload"])
            entry["md5"] = cloud.payload_hash
            entry["info"] = {"hash": cloud.payload_hash,
                             "size": len(cloud.payload_text.encode("utf-8"))}
        return maps

    client._sync_get_app_maps = maps_with_objects
    view = client._sync_refresh_map_view(timeout=0, interval=0)
    assert view.details["runtime_track_point_count"] == 3
    attributes = map_camera_attributes(
        view, image_cached=False, refreshed_at=None, last_error=None,
    )
    if changed_slot is not None:
        assert attributes["app_map_object_count"] is None
        assert attributes["app_map_object_error"] is None
        assert attributes["app_map_objects"] is None
    else:
        assert attributes["app_map_object_count"] == 1
        assert attributes["app_map_object_error"] == object_error
        assert attributes["app_map_objects"] == [
            {"extension": "bin", "url_present": False},
        ]


@pytest.mark.parametrize("generation", ["missing", "unverified", "ambiguous"])
def test_camera_does_not_borrow_objects_without_verified_generation(generation):
    client = tracked_client()
    runtime = client._sync_refresh_vector_map_view(current_map_index=0)
    maps = native_maps()
    maps["objects"] = {"objects": [{"extension": "bin"}], "object_count": 1}
    entry = maps["maps"][0]
    if generation == "missing":
        entry["info"].pop("hash")
    elif generation == "unverified":
        entry["hash_match"] = False
    else:
        maps["map_list_valid"] = False
        maps["maps"].append(deepcopy(entry))
    metadata = client_map_helpers._app_maps_view_metadata(maps)
    primary = DreameLawnMowerMapView(source="app_action_map", app_maps=metadata)
    composed = client._with_fallback_app_maps(runtime, primary)
    assert composed.app_maps["objects"] is None
    assert composed.app_maps["object_count"] is None


@pytest.mark.parametrize("metadata", [
    {"current_map_index": 1},
    {"current_map_index": 0, "objects": [], "object_count": 0},
    {"current_map_index": 0, "object_error": "newer lookup failed"},
])
def test_camera_does_not_replace_newer_or_different_map_object_metadata(metadata):
    client = tracked_client()
    runtime = DreameLawnMowerMapView(source="app_action_map", app_maps=metadata)
    primary = DreameLawnMowerMapView(
        source="app_action_map", app_maps={
            "current_map_index": 0, "objects": [{"extension": "bin"}],
            "object_count": 1, "object_error": None,
        },
    )
    composed = client._with_fallback_app_maps(runtime, primary)
    assert composed.app_maps == metadata


@pytest.mark.parametrize("key,record", [
    ("spot", {"id": 1}),
    ("spot", {"id": 1, "data": [[0, 0], ["invalid", 1], [2, 2]]}),
    ("spot", "unsupported"),
    ("trajectory", "unsupported"),
    ("trajectory", {"data": [[0, 0], ["invalid", 1]]}),
])
def test_incomplete_optional_records_preserve_verified_lawn_and_live_track(key, record):
    maps = native_maps()
    maps["maps"][0]["payload"][key] = [record]
    geometry = app_map_geometry.parse_app_map_geometry(maps, 0)
    assert geometry is not None and len(geometry.zones) == 1
    client = tracked_client()
    client._sync_get_app_maps = lambda **kwargs: maps
    view = client._sync_refresh_vector_map_view(current_map_index=0)
    assert view.image_png and view.details["runtime_track_point_count"] == 3


@pytest.mark.parametrize("key", ["spot", "trajectory"])
def test_invalid_optional_geometry_still_consumes_the_shared_budget(monkeypatch, key):
    maps = native_maps()
    payload = maps["maps"][0]["payload"]
    payload["spot"] = []
    payload[key] = [{"id": 9, "data": [["invalid", 0]] * 4}]
    # Seven required lawn/pathway points plus four invalid optional points.
    monkeypatch.setattr(app_map_geometry, "MAX_APP_MAP_POINTS", 10)
    assert app_map_geometry.parse_app_map_geometry(maps, 0) is None


@pytest.mark.parametrize("stale", [False, True])
def test_saved_trajectory_without_fresh_runtime_keeps_primary_app_render(stale):
    maps = native_maps()
    maps["maps"][0]["payload"]["trajectory"] = [{"data": [[0, 0], [100, 100]]}]
    client = tracked_client()
    client._latest_runtime_status_blob = None
    client._runtime_live_track_segments = ()
    client._runtime_session_active = None
    client._position_tracker.invalidate_current()
    if stale:
        publish(client, at=datetime.now(UTC) - timedelta(minutes=10), hex="stale")
    client._sync_get_app_maps = lambda **kwargs: maps
    geometry = app_map_geometry.parse_app_map_geometry(maps, 0)
    assert geometry.mow_paths[0].segments == (((0, 0), (100, 100)),)
    vector = client._sync_refresh_vector_map_view(current_map_index=0)
    assert vector.details["has_live_path"] is False
    assert vector.details["trajectory_point_count"] == 2
    assert "runtime_track_point_count" not in vector.details
    assert client._sync_get_vector_map_details()["has_live_path"] is False
    app = client._sync_refresh_app_map_view(
        legacy_error=None, legacy_reason="test", label_scale=1, style=None,
    )
    assert app.details["has_live_path"] is False
    maps["maps"][0]["summary"] = {"trajectory_point_count": 2}
    attributes = current_app_map_attributes(maps)
    assert attributes["current_app_map"]["trajectory_point_count"] == 2
    assert attributes["current_app_map"]["has_live_path"] is False
    normal = client._sync_refresh_map_view(timeout=0, interval=0)
    assert normal.image_png == app.image_png


@pytest.mark.parametrize("error_type", [DreameLawnMowerConnectionError, ValueError])
def test_sync_app_read_failure_preserves_legacy_fallback(error_type):
    client = tracked_client()

    def unavailable(**kwargs):
        raise error_type("app map read failed")

    client._sync_get_app_maps = unavailable
    legacy = DreameLawnMowerMapView(source="legacy_current_map", image_png=b"legacy")
    client._sync_refresh_legacy_map_view = lambda *args, **kwargs: legacy
    assert client._sync_get_vector_map_details()["available"] is False
    view = client._sync_refresh_vector_map_view(current_map_index=0)
    assert not view.available and view.error == "app map read failed"
    assert client._sync_refresh_map_view(timeout=0, interval=0) == legacy


@pytest.mark.parametrize("error_type", [
    DreameLawnMowerConnectionError, ValueError, asyncio.CancelledError,
])
def test_native_app_read_failure_preserves_fallback_or_cancellation(
    monkeypatch, error_type,
):
    strings = cloud_strings("dreame")

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response({"code": 0, "data": {}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)

            async def unavailable(**kwargs):
                raise error_type("app map read failed")

            legacy = DreameLawnMowerMapView(
                source="legacy_current_map", image_png=b"legacy",
            )

            async def legacy_view(*args, **kwargs):
                return legacy

            client.async_get_app_maps = unavailable
            client._async_refresh_legacy_map_view = legacy_view
            try:
                if error_type is asyncio.CancelledError:
                    for read in (
                        client.async_get_vector_map_details,
                        client.async_refresh_vector_map_view,
                        lambda: client.async_refresh_map_view(timeout=0, interval=0),
                    ):
                        with pytest.raises(asyncio.CancelledError):
                            await read()
                    assert not client._cloud_read_tasks
                    return
                details = await client.async_get_vector_map_details()
                assert details["available"] is False
                view = await client.async_refresh_vector_map_view(current_map_index=0)
                assert not view.available and view.error == "app map read failed"
                normal = await client.async_refresh_map_view(timeout=0, interval=0)
                assert normal == legacy
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()

    asyncio.run(scenario())
