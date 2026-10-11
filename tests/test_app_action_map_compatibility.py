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
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerConnectionError,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerMapView,
)
from custom_components.dreame_lawn_mower.sensor_map_data import (
    current_app_map_attributes,
)

from .test_app_action_live_map import native_maps, publish, tracked_client
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
