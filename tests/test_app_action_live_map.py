"""A1 Pro app-action-only geometry and framed runtime telemetry contracts."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib import import_module
from io import BytesIO
from threading import Event
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web
from PIL import Image

from custom_components.dreame_lawn_mower.camera import (
    DreameLawnMowerLivePathMapCamera,
    DreameLawnMowerMapCamera,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    app_map_geometry,
    client_mowing_map,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.app_protocol import (
    decode_mower_status_blob,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (
    map_render_style,
)
from custom_components.dreame_lawn_mower.map_attributes import map_camera_attributes
from custom_components.dreame_lawn_mower.map_cache import DreameLawnMowerMapCameraCache
from custom_components.dreame_lawn_mower.sensor_map_data import (
    _available_vector_map_count,
    _current_vector_map_runtime_track_length_m,
    _current_vector_map_runtime_track_point_count,
    _current_vector_map_runtime_track_segment_count,
)
from custom_components.dreame_lawn_mower.sensor_runtime import (
    DreameLawnMowerRuntimeTrackLengthSensor,
    DreameLawnMowerRuntimeTrackPointCountSensor,
    DreameLawnMowerRuntimeTrackSegmentCountSensor,
)

from .test_app_maps import _FakeAppMapCloud
from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_map_objects import make_client
from .test_vector_map import _batch_payload, _client

MAX_APP_MAP_POINTS = app_map_geometry.MAX_APP_MAP_POINTS
parse_app_map_geometry = app_map_geometry.parse_app_map_geometry
mowing_scene_from_batch = client_mowing_map.mowing_scene_from_batch


def a1_pro_packet(*, progress=800, size=33):
    """Synthetic #259 shape: 20-bit pose, three deltas, hundredths progress.

    Coordinates and area values are synthetic; no diagnostic secrets or original
    garden coordinates are retained in the fixture.
    """
    raw = bytearray(size)
    raw[0] = raw[-1] = 0xCE
    raw[1:6] = (10 | (10 << 20)).to_bytes(5, "little")
    raw[6] = 64
    raw[7:10] = (1).to_bytes(3, "little")
    for offset, (dx, dy) in zip((10, 14, 18), ((-8, -8), (0, 0), (10, 0)), strict=True):
        raw[offset:offset + 2] = dx.to_bytes(2, "little", signed=True)
        raw[offset + 2:offset + 4] = dy.to_bytes(2, "little", signed=True)
    raw[22:24] = bytes((1, 100))
    raw[24:26] = progress.to_bytes(2, "little")
    raw[26:29] = (10000).to_bytes(3, "little")
    raw[29:32] = (800).to_bytes(3, "little")
    return decode_mower_status_blob(list(raw), source="realtime")


def native_payload():
    return {
        "map": [
            {"id": 1, "name": "Garden", "area": 50,
             "data": [[0, 0], [1000, 0], [1000, 500], [0, 500]]},
            {"id": 200, "data": [[800, 100], [900, 200], [800, 300]]},
        ],
        "spot": [{"id": 9, "data": [[600, 100], [700, 100], [700, 200]]}],
        "point": [[500, 100]],
    }


def native_maps():
    payload = native_payload()
    cloud = _FakeAppMapCloud(payload)
    return {
        "map_list_valid": True, "current_map_index": 0,
        "source": "app_action_map", "available": True,
        "maps": [{"idx": 0, "current": True, "created": True,
                  "available": True, "hash_match": True, "payload": payload,
                  "md5": cloud.payload_hash,
                  "info": {"hash": cloud.payload_hash,
                           "size": len(cloud.payload_text.encode("utf-8"))}}],
    }


def tracked_client():
    client = _client()
    client._descriptor = replace(
        client.descriptor, model="dreame.mower.g2422", display_model="A1 Pro",
    )
    client._latest_snapshot = SimpleNamespace(available=True, activity="mowing")
    client._sync_get_vector_map_batch_data = lambda: {}
    client._sync_get_current_app_map_index = lambda: 0
    client._sync_get_app_maps = lambda **kwargs: native_maps()
    client._safe_map_diagnostics = lambda **kwargs: None
    publish(client)
    return client


def publish(client, *, at=None, **changes):
    blob = replace(
        a1_pro_packet(), received_at=(at or datetime.now(UTC)).isoformat(), **changes,
    )
    client.update_runtime_live_tracking(blob, active=True, map_index=0)
    return blob


@pytest.mark.parametrize("size", [33, 44])
@pytest.mark.parametrize("raw,expected", [
    (0, 0), (1, 0.01), (800, 8), (989, 9.89), (1500, 15),
    (9999, 99.99), (10000, 100), (10001, None), (65535, None),
])
def test_runtime_progress_uses_hundredths_on_both_framed_packets(size, raw, expected):
    blob = a1_pro_packet(progress=raw, size=size)
    assert blob.frame_valid
    assert blob.candidate_runtime_progress_percent == expected
    assert blob.candidate_runtime_area_progress_percent == 8


def test_a1_pro_app_action_only_map_reaches_cameras_scene_and_sensor_details():
    client = tracked_client()
    normal = client._sync_refresh_map_view(timeout=0, interval=0)
    live = client._sync_refresh_vector_map_view(current_map_index=0)
    assert normal.image_png == live.image_png
    assert live.source == "app_action_map" and live.available
    assert live.details["runtime_position_valid"] is True
    assert live.details["position_status"] == "current"
    assert live.details["position_source"] == "runtime_telemetry"
    assert live.details["runtime_track_point_count"] == 3
    assert live.details["runtime_track_segment_count"] == 1
    assert live.details["runtime_track_length_m"] == 2.13
    assert live.details["created"] is True and live.details["current"] is True
    assert live.details["map_area_total"] == 50
    assert live.details["cut_relation_count"] == 0
    assert live.app_maps["current_map_index"] == 0
    with Image.open(BytesIO(live.image_png)) as image:
        assert min(image.size) > 100
    details = client._sync_get_vector_map_details()
    assert _available_vector_map_count(details) == 0
    assert _current_vector_map_runtime_track_point_count(details, native_maps()) == 3
    assert _current_vector_map_runtime_track_segment_count(details, native_maps()) == 1
    assert _current_vector_map_runtime_track_length_m(details, native_maps()) == 2.13
    scene = client._sync_get_mowing_map_scene(
        map_index=0, style=map_render_style(), label_scale=1,
    )
    overlay = client.mowing_map_runtime_overlay(scene)
    assert overlay["position_status"] == "current"
    assert len(overlay["trail"][0]) == 3
    assert overlay["position"]["x"] == scene.projection.point(100, 100)[0]


def test_app_and_batch_geometry_keep_one_live_frame_across_all_consumers():
    client = tracked_client()
    client._sync_get_vector_map_batch_data = _batch_payload
    # The batch frame has an explicit boundary different from the app frame's
    # derived extents. Both describe the same current slot legitimately.
    for _ in range(2):
        normal = client._sync_refresh_map_view(timeout=0, interval=0)
        assert normal.source == "batch_vector_map"
        assert normal.details["position_status"] == "current"
        # Only two synthetic trail points are inside the batch boundary.
        assert normal.details["runtime_track_point_count"] == 2
        details = client._sync_get_vector_map_details()
        point_count = _current_vector_map_runtime_track_point_count(
            details, native_maps(),
        )
        assert point_count == 2
        scene = client._sync_get_mowing_map_scene(
            map_index=0, style=map_render_style(), label_scale=1,
        )
        overlay = client.mowing_map_runtime_overlay(scene)
        assert overlay["position_status"] == "current"
        assert len(overlay["trail"][0]) == 2


@pytest.mark.parametrize("flag", [
    "map_list_valid", "created", "available", "hash_match",
])
def test_unverified_app_geometry_cannot_validate_a_runtime_position(flag):
    maps = native_maps()
    target = maps if flag == "map_list_valid" else maps["maps"][0]
    target[flag] = False
    assert parse_app_map_geometry(maps, 0) is None
    with pytest.raises(ValueError, match="No geometry"):
        mowing_scene_from_batch(
            {}, map_index=0, style=map_render_style(), label_scale=1, app_maps=maps,
        )


def test_app_geometry_requires_current_unique_slot_and_preserves_open_pathways():
    maps = native_maps()
    geometry = parse_app_map_geometry(maps, 0)
    assert len(geometry.zones) == 1
    assert geometry.paths[0].points == ((800, 100), (900, 200), (800, 300))
    assert len(geometry.spot_areas) == 1
    assert geometry.clean_points == ((500, 100),)
    assert parse_app_map_geometry(maps, 1) is None
    maps["maps"].append(deepcopy(maps["maps"][0]))
    assert parse_app_map_geometry(maps, 0) is None


def test_native_maintenance_and_unknown_point_records_do_not_reject_the_lawn():
    maps = native_maps()
    maps["maps"][0]["payload"]["point"] = [
        {"id": 301, "param": 0, "point": [500, 100, 90], "time": 0, "type": 1},
        {"id": 302, "param": 0, "point": [700, 100], "time": 0, "type": 2},
        {"id": 303, "type": "future"},
    ]
    geometry = parse_app_map_geometry(maps, 0)
    assert geometry.clean_points == ((500, 100),)
    assert geometry.clean_point_ids == (301,)
    client = tracked_client()
    client._sync_get_app_maps = lambda **kwargs: maps
    assert client._sync_refresh_vector_map_view().details["runtime_position_valid"]


@pytest.mark.parametrize("points", [
    [[0, 0], [1, 1]], [[0, 0], [100, 0], [float("nan"), 10]],
    [[0, 0], [100, 0], [True, 10]], [[0, 0]] * (MAX_APP_MAP_POINTS + 1),
])
def test_invalid_or_excessive_app_geometry_is_not_a_position_frame(points):
    maps = native_maps()
    maps["maps"][0]["payload"]["map"][0]["data"] = points
    assert parse_app_map_geometry(maps, 0) is None


@pytest.mark.parametrize("case", [
    "stale", "out_of_bounds", "map_switch", "expired", "inactive",
])
def test_app_live_path_does_not_publish_invalid_or_retired_telemetry(case):
    client = tracked_client()
    if case == "stale":
        # Start with an already stale packet; it must not establish a position.
        client = tracked_client()
        client._position_tracker.invalidate_current()
        client._latest_runtime_status_blob = publish(
            client, at=datetime.now(UTC) - timedelta(minutes=10), hex="stale",
        )
    elif case == "out_of_bounds":
        publish(client, hex="outside", candidate_runtime_pose_x=100000)
    elif case == "map_switch":
        client.update_runtime_live_tracking(a1_pro_packet(), active=True, map_index=1)
    elif case == "expired":
        client.runtime_map_identity_expires_at = (
            datetime.now(UTC) - timedelta(seconds=1)
        )
    else:
        client.update_runtime_live_tracking(a1_pro_packet(), active=False, map_index=0)
    view = client._sync_refresh_vector_map_view(current_map_index=0)
    if case != "inactive":
        assert not view.details.get("runtime_position_valid")
    assert "runtime_track_point_count" not in view.details


@pytest.mark.parametrize("reader", ["camera", "scene"])
def test_replaced_app_geometry_and_new_task_cannot_reuse_an_old_trail(reader):
    client = tracked_client()
    scene = client._sync_get_mowing_map_scene(
        map_index=0, style=map_render_style(), label_scale=1,
    )
    initial = client._sync_refresh_vector_map_view()
    assert initial.details["runtime_track_point_count"] == 3
    maps = native_maps()
    maps["maps"][0]["payload"]["map"][0]["data"][1][0] = 1100
    client._sync_get_app_maps = lambda **kwargs: maps
    if reader == "camera":
        replaced = client._sync_refresh_vector_map_view()
        assert not replaced.details["runtime_position_valid"]
        assert "runtime_track_point_count" not in replaced.details
    else:
        current_scene = client._sync_get_mowing_map_scene(
            map_index=0, style=map_render_style(), label_scale=1,
        )
        assert client.mowing_map_runtime_overlay(current_scene)["position"] is None
    publish(client, hex="new-geometry", candidate_runtime_track_segments=(
        ((100, 100), (200, 100)),
    ))
    updated = client._sync_refresh_vector_map_view()
    assert updated.details["runtime_track_point_count"] == 2
    stale = client.mowing_map_runtime_overlay(scene)
    assert stale["position"] is None and stale["trail"] == []
    # Projecting a cached A scene must preserve both B's fresh trail and pose.
    after_stale = client._sync_refresh_vector_map_view()
    assert after_stale.details["runtime_track_point_count"] == 2
    assert after_stale.details["runtime_position_valid"] is True
    publish(client, hex="new-task", candidate_runtime_task_id=101,
            candidate_runtime_track_segments=(((200, 100), (300, 100)),))
    assert client._sync_get_vector_map_details()["runtime_track_point_count"] == 2
    client.runtime_map_identity_expires_at = None
    assert client.mowing_map_runtime_overlay(scene)["position"] is None
    # Identity retirement leaves history owned by B, even if cached A is revisited.
    retained = client._sync_refresh_vector_map_view()
    assert retained.details["position_status"] == "last_known"


@pytest.mark.parametrize("reader,native,retired", [
    ("scene", False, False), ("scene", True, False), ("scene", True, True),
    ("camera", False, False), ("camera", True, False), ("details", True, False),
])
def test_older_inflight_read_cannot_retire_a_newer_camera_frame(
    monkeypatch, reader, native, retired,
):
    client = tracked_client()
    initial = client._sync_refresh_vector_map_view()
    assert initial.details["runtime_track_point_count"] == 3
    entered, release = Event(), Event()
    scene_owner = import_module(client._mowing_map_scene_from_batch.__module__)
    vector_owner = import_module(f"{scene_owner.__package__}.client_vector_map_view")
    read_owner = import_module(f"{scene_owner.__package__}.client_vector_reads")
    owner = scene_owner if reader == "scene" else vector_owner
    target = "_mowing_scene_geometry" if reader == "scene" else "parse_app_map_geometry"
    original = getattr(owner, target)

    def paused(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, target, paused)

    async def scenario():
        # Load the executor before starting the controlled interleaving.
        await asyncio.to_thread(lambda: None)

        async def batch(*args):
            return {}

        async def app_maps(**kwargs):
            return native_maps()

        async def cloud_read(read):
            return await read(SimpleNamespace(async_get_batch_device_datas=batch))

        async def state_read(_client, read, **kwargs):
            return await asyncio.to_thread(read, None)

        async def map_hint(_client):
            return 0

        monkeypatch.setattr(read_owner, "async_read_device_state", state_read)
        monkeypatch.setattr(read_owner, "_map_hint", map_hint)
        client._async_cloud_read = cloud_read
        client.async_get_app_maps = app_maps
        if reader == "scene":
            options = dict(map_index=0, style=map_render_style(), label_scale=1)
            read = client.async_get_mowing_map_scene if native else (
                client._sync_get_mowing_map_scene
            )
        elif reader == "camera":
            options = dict(current_map_index=0)
            read = client.async_refresh_vector_map_view if native else (
                client._sync_refresh_vector_map_view
            )
        else:
            options = {}
            read = client.async_get_vector_map_details
        task = asyncio.create_task(
            read(**options) if native else asyncio.to_thread(read, **options)
        )
        try:
            if not await asyncio.to_thread(entered.wait, 5):
                if task.done():
                    await task
                pytest.fail("The older scene did not reach its geometry observation")
            replacement = native_maps()
            replacement["maps"][0]["payload"]["map"][0]["data"][1][0] = 1100
            client._sync_get_app_maps = lambda **kwargs: replacement
            client._sync_refresh_vector_map_view()
            publish(client, hex="newer-camera", candidate_runtime_track_segments=(
                ((100, 100), (200, 100)),
            ))
            assert client._sync_refresh_vector_map_view().details[
                "runtime_track_point_count"
            ] == 2
            if retired:
                client.runtime_map_identity_expires_at = None
            release.set()
            old_result = await asyncio.wait_for(task, 5)
            if reader == "scene":
                assert client.mowing_map_runtime_overlay(old_result)["position"] is None
            current = client._sync_refresh_vector_map_view()
            if retired:
                assert current.details["position_status"] == "last_known"
            else:
                assert current.details["runtime_track_point_count"] == 2
                assert current.details["runtime_position_valid"] is True
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("object_error", [False, True])
@pytest.mark.parametrize("map_changed", [False, True])
@pytest.mark.parametrize("batch_failed", [False, True])
def test_native_async_app_only_uses_verified_download_and_cache(
    monkeypatch, object_error, map_changed, batch_failed,
):
    strings = cloud_strings("dreame")
    fake = _FakeAppMapCloud(native_payload())

    async def scenario():
        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            if request.path == "/" + "/".join(strings[i] for i in (23, 26, 44)):
                return web.json_response({
                    "code": 500 if batch_failed else 0, "data": {},
                })
            (action,) = body["data"]["params"]["in"]
            if action["t"] == "OBJ" and object_error:
                response = {"out": [{"m": "r", "r": 1}]}
            else:
                response = fake.call_app_action(
                    action, redact_response=action["t"] == "OBJ",
                )
            if action["t"] == "OBJ" and map_changed:
                # A device-side edit between the primary and fallback reads
                # changes the verified generation while retaining the slot.
                replacement = _FakeAppMapCloud({**native_payload(), "name": "edited"})
                fake.payload_text = replacement.payload_text
                fake.payload_hash = replacement.payload_hash
            return web.json_response({"code": 0, "data": {"result": response}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            client._descriptor = replace(
                client.descriptor, model="dreame.mower.g2422", display_model="A1 Pro",
            )
            client._latest_snapshot = SimpleNamespace(available=True, activity="mowing")
            publish(client)
            try:
                live = await client.async_refresh_vector_map_view(current_map_index=0)
                assert live.source == "app_action_map" and live.image_png
                assert live.details["runtime_track_point_count"] == 3
                fake.calls.clear()
                details = await client.async_get_vector_map_details()
                assert details["runtime_track_point_count"] == 3
                assert details["available_map_count"] == 0
                scene = await client.async_get_mowing_map_scene(
                    map_index=0, style=map_render_style(),
                )
                assert client.mowing_map_runtime_overlay(scene)["trail"]
                coordinator = SimpleNamespace(
                    client=client, data=client._latest_snapshot,
                    vector_map_details=details, app_maps=native_maps(),
                    batch_device_data={}, selected_map_index=0,
                    entry=SimpleNamespace(options={}),
                    runtime_status_blob=client._latest_runtime_status_blob,
                )
                for sensor_type, expected in (
                    (DreameLawnMowerRuntimeTrackPointCountSensor, 3),
                    (DreameLawnMowerRuntimeTrackLengthSensor, 2.13),
                    (DreameLawnMowerRuntimeTrackSegmentCountSensor, 1),
                ):
                    entity = object.__new__(sensor_type)
                    entity.coordinator = coordinator
                    assert entity.available and entity.native_value == expected
                for camera_type in (
                    DreameLawnMowerMapCamera, DreameLawnMowerLivePathMapCamera,
                ):
                    monkeypatch.setattr(
                        camera_type, "_map_marker_image", property(lambda self: None),
                    )
                    camera = object.__new__(camera_type)
                    camera.coordinator = coordinator
                    camera._map_cache = DreameLawnMowerMapCameraCache(
                        ttl=timedelta(seconds=60),
                    )
                    camera.async_write_ha_state = lambda: None
                    camera._restart_preview = None
                    camera.hass = SimpleNamespace(
                        async_add_executor_job=asyncio.to_thread,
                    )
                    jpeg = await camera._async_refresh_and_render_map_image()
                    camera_view = camera._map_cache.last_view
                    assert camera_view.source == "app_action_map"
                    assert camera_view.details["runtime_position_valid"] is True
                    assert camera_view.details["runtime_track_point_count"] == 3
                    assert jpeg.startswith(b"\xff\xd8")
                    assert not camera._map_cache.last_image_is_placeholder
                    if camera_type is DreameLawnMowerMapCamera:
                        attributes = map_camera_attributes(
                            camera_view, image_cached=True,
                            refreshed_at=None, last_error=None,
                        )
                        if map_changed:
                            assert attributes["app_map_object_count"] is None
                            assert attributes["app_map_object_error"] is None
                            assert attributes["app_map_objects"] is None
                        elif object_error:
                            assert attributes["app_map_object_error"]
                        else:
                            assert attributes["app_map_object_count"] == 1
                            assert len(attributes["app_map_objects"]) == 1
                assert ("MAPD" in [action["t"] for action in fake.calls]) is map_changed
                assert all(action["m"] == "g" for action in fake.calls)
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())
