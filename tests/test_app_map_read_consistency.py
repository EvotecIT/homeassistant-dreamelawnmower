"""Coherent live views and usable geometry across concurrent map readers."""

from __future__ import annotations

import asyncio
import json
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


def _native_reads(monkeypatch, client, batch, maps):
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
        return 0

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
def test_batch_metadata_without_boundary_uses_verified_app_geometry(
    monkeypatch, native, reader,
):
    client = tracked_client()
    batch = {"MAP.0": json.dumps([json.dumps({"mapIndex": 0, "name": "Metadata"})])}
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


def test_missing_app_geometry_cannot_make_batch_metadata_available():
    client = tracked_client()
    client._sync_get_vector_map_batch_data = lambda: {
        "MAP.0": json.dumps([json.dumps({"mapIndex": 0})]),
    }
    client._sync_get_app_maps = lambda **kwargs: {}
    assert client._sync_get_vector_map_details()["available"] is False
