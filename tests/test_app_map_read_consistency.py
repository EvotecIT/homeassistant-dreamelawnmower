"""Coherent live views and usable geometry across concurrent map readers."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib import import_module
from threading import Event
from types import SimpleNamespace

import pytest

from .test_app_action_live_map import (
    map_render_style,
    native_maps,
    publish,
    tracked_client,
)
from .test_vector_map import _batch_payload


def _native_reads(monkeypatch, client, batch, maps, *, map_hint=lambda: 0):
    owner = import_module(
        f"{client._mowing_map_scene_from_batch.__module__.rsplit('.', 1)[0]}"
        ".client_vector_reads"
    )

    async def async_batch(*args):
        return await asyncio.to_thread(batch)

    async def app_maps(**kwargs):
        return maps()

    async def cloud_read(read):
        return await read(SimpleNamespace(async_get_batch_device_datas=async_batch))

    async def state_read(_client, read, **kwargs):
        return await asyncio.to_thread(read, None)

    async def hint(_client):
        return map_hint()

    client._async_cloud_read = cloud_read
    client.async_get_app_maps = app_maps
    monkeypatch.setattr(owner, "async_read_device_state", state_read)
    monkeypatch.setattr(owner, "_map_hint", hint)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("edited", [False, True])
def test_identical_overlapping_reads_keep_the_winning_frame_live(
    monkeypatch, native, edited,
):
    client = tracked_client()
    maps = native_maps()
    if edited:
        client._sync_refresh_vector_map_view()
        maps["maps"][0]["payload"]["map"][0]["data"][1][0] = 1100
    client._sync_get_app_maps = lambda **kwargs: maps
    entered, release = Event(), Event()

    def batch():
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return {}

    client._sync_get_vector_map_batch_data = batch
    _native_reads(monkeypatch, client, batch, lambda: maps)

    async def scenario():
        older = asyncio.create_task(
            client.async_refresh_vector_map_view(current_map_index=0)
            if native else asyncio.to_thread(
                client._sync_refresh_vector_map_view, current_map_index=0,
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            client._sync_refresh_vector_map_view(current_map_index=0)
            if edited:
                publish(client, hex="winning-frame")
            release.set()
            view = await asyncio.wait_for(older, 5)
            assert view.details["runtime_position_valid"] is True
            assert view.details["runtime_track_point_count"] == 3
            assert view.summary.robot_present
            assert view.image_png is not None
        finally:
            release.set()
            await asyncio.gather(older, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("map_switch", [False, True])
def test_view_keeps_position_telemetry_and_trail_from_one_observation(
    monkeypatch, map_switch,
):
    client = tracked_client()
    owner = import_module(
        f"{client._mowing_map_scene_from_batch.__module__.rsplit('.', 1)[0]}"
        ".client_vector_map_view"
    )
    summarize = owner.vector_map_to_summary

    def publish_during_composition(vector_map):
        blob = publish(
            client, hex="newer-packet", candidate_runtime_pose_x=200,
            candidate_runtime_pose_y=200,
            candidate_runtime_track_segments=(((200, 200), (300, 200)),),
        )
        if map_switch:
            client.update_runtime_live_tracking(blob, active=True, map_index=1)
        client._latest_snapshot = SimpleNamespace(
            available=True, activity="charging", docked=True,
        )
        return summarize(vector_map)

    monkeypatch.setattr(owner, "vector_map_to_summary", publish_during_composition)
    view = client._sync_refresh_vector_map_view(current_map_index=0)
    assert view.details["runtime_position_valid"] is True
    assert view.details["position_x"] == view.details["runtime_pose_x"] == 100
    assert view.details["position_y"] == view.details["runtime_pose_y"] == 100
    assert view.details["runtime_track_point_count"] == 3
    assert view.details["docked"] is False
    assert client._latest_runtime_status_blob.candidate_runtime_pose_x == 200
    assert client._runtime_live_map_index == (1 if map_switch else 0)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("reader", ["camera", "details", "scene"])
@pytest.mark.parametrize(
    "metadata_kind", ["no_boundary", "boundary_only", "empty_shapes"],
)
def test_batch_metadata_uses_verified_app_geometry(
    monkeypatch, native, reader, metadata_kind,
):
    client = tracked_client()
    metadata = {"mapIndex": 0, "name": "Metadata"}
    if metadata_kind != "no_boundary":
        metadata["boundary"] = {"x1": 0, "y1": 0, "x2": 1000, "y2": 500}
    if metadata_kind == "empty_shapes":
        for field in ("mowingAreas", "paths", "spotAreas"):
            metadata[field] = {"dataType": "Map", "value": [[1, {"path": []}]]}
    batch = {"MAP.0": json.dumps([json.dumps(metadata)])}
    if metadata_kind == "empty_shapes":
        batch["M_PATH.0"] = "[[1,1]]"
    client._sync_get_vector_map_batch_data = lambda: batch
    _native_reads(monkeypatch, client, lambda: batch, native_maps)
    options = {}
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
        read = client.async_get_vector_map_details if native else (
            client._sync_get_vector_map_details
        )
    result = asyncio.run(read(**options)) if native else read(**options)
    if reader == "scene":
        overlay = client.mowing_map_runtime_overlay(result)
        assert overlay["position"] is not None
        assert overlay["trail"]
    else:
        details = result if reader == "details" else result.details
        if reader == "details":
            assert details["source"] == "app_action_map"
        else:
            assert result.source == "app_action_map"
        assert details["runtime_position_valid"] is True
        assert details["runtime_track_point_count"] == 3
        if reader == "camera":
            assert result.image_png is not None


def test_drawable_batch_stays_preferred():
    client = tracked_client()
    client._sync_get_vector_map_batch_data = _batch_payload

    def unexpected(**kwargs):
        pytest.fail("Drawable batch geometry must not fetch the app fallback")

    client._sync_get_app_maps = unexpected
    assert client._sync_refresh_vector_map_view().source == "batch_vector_map"


@pytest.mark.parametrize("shape", ["mowingAreas", "paths", "spotAreas", "M_PATH"])
def test_individual_drawable_batch_shapes_keep_the_existing_source(shape):
    client = tracked_client()
    full_batch = _batch_payload()
    raw_map = "".join(full_batch[f"MAP.{index}"] for index in range(3))
    entries = json.loads(raw_map[:int(full_batch["MAP.info"])])
    record = json.loads(entries[0])
    for field in ("mowingAreas", "paths", "spotAreas"):
        if field != shape:
            record.pop(field, None)
    batch = {"MAP.0": json.dumps([json.dumps(record)])}
    if shape == "M_PATH":
        batch.update({
            key: value for key, value in full_batch.items() if key.startswith("M_PATH")
        })
    client._sync_get_vector_map_batch_data = lambda: batch

    def unexpected(**kwargs):
        pytest.fail("Usable partial batch geometry must retain its existing source")

    client._sync_get_app_maps = unexpected
    result = client._sync_refresh_vector_map_view()
    assert result.source == "batch_vector_map"
    assert result.summary.available
    assert result.image_png is not None


def test_missing_app_geometry_cannot_make_batch_metadata_available():
    client = tracked_client()
    client._sync_get_vector_map_batch_data = lambda: {
        "MAP.0": json.dumps([json.dumps({"mapIndex": 0})]),
    }
    client._sync_get_app_maps = lambda **kwargs: {}
    assert client._sync_get_vector_map_details()["available"] is False


def test_batch_runtime_trail_expires_consistently_across_views(monkeypatch):
    client = tracked_client()
    client._sync_get_vector_map_batch_data = _batch_payload
    client.update_runtime_live_tracking(None, active=False, map_index=0)
    observed_at = datetime.now(UTC)
    publish(
        client, at=observed_at, hex="fresh-batch", candidate_runtime_pose_x=50,
        candidate_runtime_pose_y=40,
        candidate_runtime_track_segments=(((10, 20), (30, 40), (50, 40)),),
    )
    fresh = client._sync_refresh_vector_map_view()
    assert fresh.details["runtime_position_valid"] is True
    assert fresh.details["runtime_track_point_count"] == 3
    scene = client._sync_get_mowing_map_scene(
        map_index=0, style=map_render_style(), label_scale=1,
    )
    assert client.mowing_map_runtime_overlay(scene)["trail"]

    class AgedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return observed_at + timedelta(seconds=91)

    package = client._mowing_map_scene_from_batch.__module__.rsplit(".", 1)[0]
    for name in ("position_tracking", "mowing_map"):
        monkeypatch.setattr(import_module(f"{package}.{name}"), "datetime", AgedClock)
    assert client.mowing_map_runtime_overlay(scene)["trail"] == []
    aged = client._sync_refresh_vector_map_view()
    details = client._sync_get_vector_map_details()
    for result in (aged.details, details):
        assert result["position_status"] == "last_known"
        assert result["runtime_position_valid"] is False
        assert result.get("runtime_track_point_count", 0) == 0
        # Saved batch M_PATH history is independent of the runtime trail.
        assert result["mow_path_point_count"] == fresh.details["mow_path_point_count"]
    assert aged.image_png is not None


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("reader", ["camera", "details", "scene"])
def test_map_switch_fences_old_read_before_new_geometry_arrives(
    monkeypatch, native, reader,
):
    client = tracked_client()
    maps = native_maps()
    client._sync_get_app_maps = lambda **kwargs: maps
    client._sync_get_current_app_map_index = lambda: maps["current_map_index"]
    _native_reads(
        monkeypatch, client, lambda: {}, lambda: maps,
        map_hint=lambda: maps["current_map_index"],
    )
    package = client._mowing_map_scene_from_batch.__module__.rsplit(".", 1)[0]
    owner = import_module(f"{package}.client_mowing_map" if reader == "scene" else (
        f"{package}.client_vector_map_view"
    ))
    target = "_mowing_scene_geometry" if reader == "scene" else "parse_app_map_geometry"
    parse = getattr(owner, target)
    entered = (Event(), Event())
    release = (Event(), Event())

    def pause(*args, **kwargs):
        app_maps = args[2] if reader == "scene" else args[0]
        index = app_maps["current_map_index"]
        entered[index].set()
        assert release[index].wait(10)
        return parse(*args, **kwargs)

    monkeypatch.setattr(owner, target, pause)

    async def read(index):
        if reader == "scene":
            method = client.async_get_mowing_map_scene if native else (
                client._sync_get_mowing_map_scene
            )
            options = dict(map_index=index, style=map_render_style(), label_scale=1)
        elif reader == "camera":
            method = client.async_refresh_vector_map_view if native else (
                client._sync_refresh_vector_map_view
            )
            options = dict(current_map_index=index)
        else:
            method = client.async_get_vector_map_details if native else (
                client._sync_get_vector_map_details
            )
            options = {}
        return await method(**options) if native else await asyncio.to_thread(
            method, **options,
        )

    async def scenario():
        old = asyncio.create_task(read(0))
        current = None
        try:
            assert await asyncio.to_thread(entered[0].wait, 10)
            maps = native_maps()
            maps["current_map_index"] = maps["maps"][0]["idx"] = 1
            client._sync_get_app_maps = lambda **kwargs: maps
            client._sync_get_current_app_map_index = lambda: 1
            _native_reads(
                monkeypatch, client, lambda: {}, lambda: maps, map_hint=lambda: 1,
            )
            blob = replace(
                client._latest_runtime_status_blob, hex="map-switch",
                received_at=datetime.now(UTC).isoformat(),
            )
            client.update_runtime_live_tracking(blob, active=True, map_index=1)
            current = asyncio.create_task(read(1))
            assert await asyncio.to_thread(entered[1].wait, 10)
            release[0].set()
            await asyncio.wait_for(old, 10)
            release[1].set()
            result = await asyncio.wait_for(current, 10)
            if reader == "scene":
                overlay = client.mowing_map_runtime_overlay(result)
                assert overlay["position"] is not None
                assert overlay["trail"]
            else:
                details = result if reader == "details" else result.details
                assert details["runtime_position_valid"] is True
                assert details["runtime_track_point_count"] == 3
        finally:
            for event in release:
                event.set()
            await asyncio.gather(
                old, *([current] if current is not None else []),
                return_exceptions=True,
            )

    asyncio.run(scenario())
