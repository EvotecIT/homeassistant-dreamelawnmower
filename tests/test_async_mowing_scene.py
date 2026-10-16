"""Scene transport preserves rendered output and owns cancellation through rendering."""

from __future__ import annotations

import asyncio
from threading import Event

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_mowing_map,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (
    map_render_style,
)

from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_map_objects import make_client
from .test_vector_map import _batch_payload


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("empty", [False, True])
def test_native_scene_matches_existing_policy(monkeypatch, account, empty):
    strings = cloud_strings(account)
    payload = {} if empty else _batch_payload()
    calls = []
    app_calls = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        if request.path != "/" + "/".join(strings[i] for i in (23, 26, 44)):
            (action,) = (await request.json())["data"]["params"]["in"]
            app_calls.append(action)
            assert action == {"m": "g", "t": "MAPL"}
            return web.json_response(
                {"code": 0, "data": {"result": {"out": [{"d": []}]}}}
            )
        assert request.path == "/" + "/".join(strings[i] for i in (23, 26, 44))
        calls.append(await request.json())
        return web.json_response({"code": 0, "data": payload})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session, account)
            client._sync_get_vector_map_batch_data = lambda: payload
            client._sync_get_app_maps = lambda **kwargs: {
                "map_list_valid": True, "current_map_index": None, "maps": [],
            }
            options = dict(map_index=0, style=map_render_style(), label_scale=1.0)
            try:
                if empty:
                    with pytest.raises(ValueError, match="No geometry"):
                        client._sync_get_mowing_map_scene(**options)
                    with pytest.raises(ValueError, match="No geometry"):
                        await client.async_get_mowing_map_scene(**options)
                else:
                    expected = client._sync_get_mowing_map_scene(**options)
                    actual = await client.async_get_mowing_map_scene(**options)
                    assert actual.image_png == expected.image_png
                    assert actual.revision == expected.revision
                    assert actual.projection == expected.projection
                assert calls == [{"did": "42", strings[35]: []}]
                assert app_calls == ([{"m": "g", "t": "MAPL"}] if empty else [])
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["network", "render"])
@pytest.mark.parametrize("close_client", [False, True])
def test_scene_cancellation_owns_network_and_started_render(
    monkeypatch, phase, close_client
):
    strings = cloud_strings("dreame")
    render_entered, render_release, render_finished = Event(), Event(), Event()
    original = client_mowing_map.build_mowing_map_scene

    def render(*args, **kwargs):
        render_entered.set()
        assert render_release.wait(5)
        result = original(*args, **kwargs)
        render_finished.set()
        return result

    monkeypatch.setattr(client_mowing_map, "build_mowing_map_scene", render)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            entered.set()
            if phase == "network":
                await release.wait()
            return web.json_response({"code": 0, "data": _batch_payload()})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            task = asyncio.create_task(client.async_get_mowing_map_scene(
                map_index=0, style=map_render_style()))
            close = None
            try:
                await asyncio.wait_for(entered.wait(), 3)
                if phase == "render":
                    assert await asyncio.to_thread(render_entered.wait, 3)
                if close_client:
                    close = asyncio.create_task(client.async_close())
                else:
                    task.cancel()
                if phase == "render":
                    await asyncio.sleep(0.03)
                    assert not task.done()
                    if close is not None:
                        assert not close.done()
                    render_release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
                if close is not None:
                    await asyncio.wait_for(close, 3)
                assert render_finished.is_set() == (phase == "render")
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                render_release.set()
                await asyncio.gather(task, return_exceptions=True)
                if close is not None:
                    await close
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("account", ["dreame", "mova"])
def test_native_scene_uses_verified_station_metadata_without_extra_cloud_reads(
    monkeypatch, account,
):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_inventory import (  # noqa: E501
        _app_map_inventory_identity,
    )
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.zone_mowing_history import (  # noqa: E501
        ChargerPosition,
        ZoneMowingHistory,
    )

    from .test_zone_mowing_history import HASH, _maps

    strings = cloud_strings(account)
    calls = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        calls.append(request.path)
        assert request.path == "/" + "/".join(strings[i] for i in (23, 26, 44))
        return web.json_response({"code": 0, "data": _batch_payload()})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session, account)
            maps = _maps()
            for entry in maps["maps"]:
                entry["info"]["size"] = 100
            client._latest_app_map_inventory_identity = _app_map_inventory_identity(
                maps["maps"]
            )
            client._charger_history = ZoneMowingHistory(
                chargers={"summary": ChargerPosition(20, 40, 0, 1, HASH)}
            )
            client._latest_snapshot = SimpleNamespace(
                available=True, docked=True, activity="docked",
            )
            client.update_runtime_map_identity(
                0, expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            try:
                scene = await client.async_get_mowing_map_scene(
                    map_index=0, style=map_render_style(), app_maps=maps,
                )
                assert scene.charger == (20, 40)
                overlay = client.mowing_map_runtime_overlay(scene)
                assert overlay["position_status"] == "known_dock"
                assert overlay["position"]["x"] == scene.projection.point(20, 40)[0]
                client._latest_app_map_inventory_identity = "replacement-inventory"
                stale = await client.async_get_mowing_map_scene(
                    map_index=0, style=map_render_style(), app_maps=maps,
                )
                assert stale.charger is None
                assert len(calls) == 2
            finally:
                await client.async_close()

    asyncio.run(scenario())
