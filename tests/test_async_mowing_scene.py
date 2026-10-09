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

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        assert request.path == "/" + "/".join(strings[i] for i in (23, 26, 44))
        calls.append(await request.json())
        return web.json_response({"code": 0, "data": payload})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session, account)
            client._sync_get_vector_map_batch_data = lambda: payload
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
