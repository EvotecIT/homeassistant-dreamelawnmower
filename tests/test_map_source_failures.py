"""An optional map transport failure must not discard another verified frame."""

import asyncio
from importlib import import_module

import pytest

from dreame_lawn_mower_client import DreameLawnMowerConnectionError

from .test_app_action_live_map import map_render_style, native_maps, tracked_client
from .test_app_map_read_consistency import _native_reads


async def read_map(client, native, reader):
    if reader == "normal":
        return (
            await client.async_refresh_map_view(timeout=0, interval=0) if native else
            client._sync_refresh_map_view(timeout=0, interval=0)
        )
    if reader == "vector":
        return await client.async_refresh_vector_map_view() if native else (
            client._sync_refresh_vector_map_view()
        )
    if reader == "details":
        return await client.async_get_vector_map_details() if native else (
            client._sync_get_vector_map_details()
        )
    return await client.async_get_mowing_map_scene(
        map_index=0, style=map_render_style(), label_scale=1,
    ) if native else client._sync_get_mowing_map_scene(
        map_index=0, style=map_render_style(), label_scale=1,
    )


def configure_reads(monkeypatch, client, batch, maps):
    client._sync_get_vector_map_batch_data = batch
    client._sync_get_app_maps = lambda **kwargs: maps()
    _native_reads(monkeypatch, client, batch, maps)
    views = import_module(
        client._mowing_map_scene_from_batch.__module__.rsplit(".", 1)[0]
        + ".client_map_views"
    )

    async def state_read(_client, read, **kwargs):
        return await asyncio.to_thread(read, None)

    monkeypatch.setattr(views, "async_read_device_state", state_read)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("reader", ["normal", "vector", "details", "scene"])
def test_failed_batch_retains_verified_app_position_and_trail(
    monkeypatch, native, reader,
):
    client = tracked_client()

    def unavailable():
        raise DreameLawnMowerConnectionError("Transient batch failure")

    async def scenario():
        configure_reads(monkeypatch, client, lambda: {}, native_maps)
        healthy = await read_map(client, native, reader)
        configure_reads(monkeypatch, client, unavailable, native_maps)
        fallback = await read_map(client, native, reader)
        if reader == "scene":
            assert fallback.position_identity == healthy.position_identity
            overlay = client.mowing_map_runtime_overlay(fallback)
            assert overlay["position_status"] == "current"
            assert len(overlay["trail"][0]) == 3
        else:
            details = fallback if reader == "details" else fallback.details
            assert details["runtime_position_valid"] is True
            assert details["runtime_track_point_count"] == 3
            if reader != "details":
                assert fallback.source == "app_action_map"
                assert fallback.image_png == healthy.image_png
                assert fallback.error is None

    asyncio.run(scenario())


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("reader", ["vector", "details", "scene"])
@pytest.mark.parametrize("app_failed", [False, True, "unverified"])
def test_failed_sources_preserve_batch_error_without_state_work(
    monkeypatch, native, reader, app_failed,
):
    client = tracked_client()

    def unavailable():
        raise DreameLawnMowerConnectionError("Original batch failure")

    def maps():
        if app_failed == "unverified":
            maps = native_maps()
            maps["maps"][0]["hash_match"] = False
            return maps
        if app_failed:
            raise DreameLawnMowerConnectionError("App fallback failure")
        return {}

    configure_reads(monkeypatch, client, unavailable, maps)

    def no_diagnostics(**kwargs):
        pytest.fail("Failed sources must not reacquire device-state diagnostics")

    if native:
        client._safe_map_diagnostics = no_diagnostics
    owner = import_module(
        client._mowing_map_scene_from_batch.__module__.rsplit(".", 1)[0]
        + ".client_vector_reads"
    )

    async def no_state(*args, **kwargs):
        pytest.fail("Failed sources must not wait for device state")

    monkeypatch.setattr(owner, "async_read_device_state", no_state)
    if reader == "scene":
        with pytest.raises(
            DreameLawnMowerConnectionError, match="Original batch failure",
        ):
            asyncio.run(read_map(client, native, reader))
        return
    result = asyncio.run(read_map(client, native, reader))
    error = result["error"] if reader == "details" else result.error
    assert error == "Original batch failure"


@pytest.mark.parametrize("reader", ["vector", "details", "scene"])
def test_cancelled_batch_read_does_not_start_app_fallback(monkeypatch, reader):
    client = tracked_client()

    def cancelled():
        raise asyncio.CancelledError

    def maps():
        pytest.fail("Cancellation must not start another transport")

    configure_reads(monkeypatch, client, cancelled, maps)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(read_map(client, True, reader))
