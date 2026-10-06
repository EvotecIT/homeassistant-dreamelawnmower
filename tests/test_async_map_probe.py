"""Native probe composition retains partial evidence and owns legacy work."""

from __future__ import annotations

import asyncio
from threading import Event
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerConnectionError,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_probe import (
    MAP_HISTORY_PROPERTY_KEYS,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerMapView,
)

from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_map_objects import make_client


def prepare_client(session):
    client = make_client(session)
    client.async_refresh_map_view = AsyncMock(
        return_value=DreameLawnMowerMapView(source="app_action_map")
    )
    client.async_refresh_vector_map_view = AsyncMock(
        return_value=DreameLawnMowerMapView(source="batch_vector_map")
    )
    client.async_get_cloud_device_info = AsyncMock(return_value={"did": "42"})
    client.async_get_cloud_device_list_page = AsyncMock(return_value={})
    client.async_get_cloud_properties = AsyncMock(return_value=[])
    client.async_get_cloud_user_features = AsyncMock(
        side_effect=DreameLawnMowerConnectionError("features offline")
    )
    client.async_get_cloud_device_otc_info = AsyncMock(return_value={"supported": True})
    client.async_get_app_maps = AsyncMock(return_value={"maps": []})
    client._sync_refresh_legacy_map_view = lambda *args: DreameLawnMowerMapView(
        source="legacy_current_map"
    )
    return client


def test_probe_reuses_metadata_and_retains_history_errors(monkeypatch):
    strings = cloud_strings("dreame")
    calls = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response({**login_response(strings), "uid": "41"})
        body = await request.json()
        calls.append(body)
        if len(calls) == 1:
            return web.Response(status=400, text="unavailable")
        return web.json_response({"code": 0, "data": {strings[33]: []}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = prepare_client(session)
            try:
                result = await client.async_probe_map_sources(timeout=0, interval=0.1)
                assert len(calls) == len(MAP_HISTORY_PROPERTY_KEYS)
                assert (
                    "error"
                    in result["cloud_property_history"][MAP_HISTORY_PROPERTY_KEYS[0]]
                )
                assert result["selected_map_view"]["source"] == "app_action_map"
                assert result["legacy_current_map"]["source"] == "legacy_current_map"
                assert result["batch_vector_map"]["source"] == "batch_vector_map"
                assert result["cloud_user_features"] == {"error": "features offline"}
                client.async_get_cloud_device_info.assert_awaited_once()
                client.async_get_cloud_device_list_page.assert_awaited_once()
                client.async_get_cloud_device_otc_info.assert_awaited_once()
            finally:
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["history", "legacy"])
def test_probe_close_stops_later_sections_and_drains_legacy(monkeypatch, phase):
    strings = cloud_strings("dreame")
    worker_entered = Event()
    worker_release = Event()

    async def scenario():
        history_entered = asyncio.Event()
        history_release = asyncio.Event()
        calls = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response({**login_response(strings), "uid": "41"})
            calls.append(await request.json())
            if phase == "history":
                history_entered.set()
                await history_release.wait()
            return web.json_response({"code": 0, "data": {strings[33]: []}})

        def legacy(*args):
            worker_entered.set()
            assert worker_release.wait(5)
            return DreameLawnMowerMapView(source="legacy_current_map")

        async with server(monkeypatch, handler), ClientSession() as session:
            client = prepare_client(session)
            client._sync_refresh_legacy_map_view = legacy
            task = asyncio.create_task(client.async_probe_map_sources())
            close = None
            try:
                if phase == "history":
                    await asyncio.wait_for(history_entered.wait(), 3)
                else:
                    assert await asyncio.to_thread(worker_entered.wait, 3)
                close = asyncio.create_task(client.async_close())
                if phase == "legacy":
                    await asyncio.sleep(0.04)
                    assert not close.done()
                    assert client._device is not None
                worker_release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await close
                client.async_refresh_vector_map_view.assert_not_awaited()
                if phase == "history":
                    assert len(calls) == 1
                    client.async_get_cloud_user_features.assert_not_awaited()
                assert not client._cloud_read_tasks and not session.closed
            finally:
                worker_release.set()
                history_release.set()
                await asyncio.gather(task, return_exceptions=True)
                if close is not None:
                    await close
                await client.async_close()

    asyncio.run(scenario())
