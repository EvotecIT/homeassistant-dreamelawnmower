"""Native vector transport, rendered output and worker lifetime contracts."""

from __future__ import annotations

import asyncio
from threading import Event

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_vector_map_view,
)

from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_map_objects import make_client
from .test_vector_map import _batch_payload


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("explicit_hint", [False, True])
def test_native_vector_view_matches_synchronous_render(
    monkeypatch, account_type, explicit_hint
):
    strings = cloud_strings(account_type)
    payload = _batch_payload()
    batches = []
    hints = []

    async def scenario():
        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            if request.path == "/" + "/".join(strings[i] for i in (23, 26, 44)):
                batches.append(body)
                return web.json_response({"code": 0, "data": payload})
            (action,) = body["data"]["params"]["in"]
            hints.append(action)
            assert action == {"m": "g", "t": "MAPL"}
            return web.json_response(
                {"code": 0, "data": {"result": {"out": [{"d": [[0, 1, 1, 1, 0]]}]}}}
            )

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session, account_type)
            client._sync_get_vector_map_batch_data = lambda: payload
            client._sync_get_current_app_map_index = lambda: 0
            expected = client._sync_refresh_vector_map_view(current_map_index=0)
            expected_details = client._sync_get_vector_map_details()
            try:
                result = await client.async_refresh_vector_map_view(
                    current_map_index=0 if explicit_hint else None
                )
                assert result.image_png and result.image_png == expected.image_png
                assert result.summary == expected.summary
                assert (
                    result.details["last_updated"] >= expected.details["last_updated"]
                )
                assert {
                    k: v for k, v in result.details.items() if k != "last_updated"
                } == {k: v for k, v in expected.details.items() if k != "last_updated"}
                assert len(hints) == (0 if explicit_hint else 1)
                details = await client.async_get_vector_map_details()
                assert details["last_updated"] >= expected_details["last_updated"]
                assert {k: v for k, v in details.items() if k != "last_updated"} == {
                    k: v for k, v in expected_details.items() if k != "last_updated"
                }
                assert len(batches) == 2
                assert batches == [{"did": "42", strings[35]: []}] * 2
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["batch", "hint"])
def test_vector_read_cancellation_stops_network_sequence(monkeypatch, phase):
    strings = cloud_strings("dreame")

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            is_batch = request.path == "/" + "/".join(strings[i] for i in (23, 26, 44))
            calls.append("batch" if is_batch else "hint")
            if calls[-1] == phase:
                entered.set()
                await release.wait()
            return web.json_response(
                {
                    "code": 0,
                    "data": _batch_payload()
                    if is_batch
                    else {"result": {"out": [{"d": []}]}},
                }
            )

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            task = asyncio.create_task(client.async_get_vector_map_details())
            try:
                await asyncio.wait_for(entered.wait(), 3)
                await asyncio.wait_for(client.async_close(), 3)
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert calls == (["batch"] if phase == "batch" else ["batch", "hint"])
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                await client.async_close()

    asyncio.run(scenario())


def test_vector_close_drains_started_renderer_before_device_cleanup(monkeypatch):
    strings = cloud_strings("dreame")
    entered = Event()
    release = Event()
    finished = Event()
    original = client_vector_map_view.render_vector_map_png

    def render(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        result = original(*args, **kwargs)
        finished.set()
        return result

    monkeypatch.setattr(client_vector_map_view, "render_vector_map_png", render)

    async def scenario():
        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            return web.json_response({"code": 0, "data": _batch_payload()})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            device = client._device
            task = asyncio.create_task(
                client.async_refresh_vector_map_view(current_map_index=0)
            )
            close = None
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                close = asyncio.create_task(client.async_close())
                await asyncio.sleep(0.04)
                assert not close.done()
                assert client._device is device
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await close
                assert finished.is_set()
                assert client._device is None
                assert not session.closed
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                if close is not None:
                    await close
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["vector", "app", "known_failed", "unknown"])
def test_native_composed_map_keeps_source_and_current_lawn(monkeypatch, mode):
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
        DreameLawnMowerMapView,
    )

    from .test_app_maps import _FakeAppMapCloud

    strings = cloud_strings("dreame")
    fake = _FakeAppMapCloud({"map": [{"data": [[0, 0], [20, 0], [20, 20]]}]})
    legacy_calls = []

    def legacy(timeout, interval, **kwargs):
        legacy_calls.append((timeout, interval))
        return DreameLawnMowerMapView(
            source="legacy_current_map", image_png=b"legacy-image"
        )

    async def scenario():
        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            if request.path == "/" + "/".join(strings[i] for i in (23, 26, 44)):
                return web.json_response(
                    {"code": 0, "data": _batch_payload() if mode == "vector" else {}}
                )
            body = await request.json()
            (action,) = body["data"]["params"]["in"]
            if mode == "unknown" and action["t"] == "MAPL":
                response = {"out": [{"d": []}]}
            elif mode == "known_failed" and action["t"] == "MAPI":
                response = {"out": [{"d": {"size": 0}}]}
            else:
                response = fake.call_app_action(
                    action, redact_response=action["t"] == "OBJ"
                )
            return web.json_response({"code": 0, "data": {"result": response}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            client._sync_refresh_legacy_map_view = legacy
            try:
                view = await client.async_refresh_map_view(timeout=2, interval=0.1)
                if mode == "vector":
                    assert view.source == "batch_vector_map"
                    assert view.image_png.startswith(b"\x89PNG")
                    assert view.app_maps["current_map_index"] == 0
                elif mode == "app":
                    assert view.source == "app_action_map"
                    assert view.image_png.startswith(b"\x89PNG")
                elif mode == "known_failed":
                    assert view.source == "app_action_map"
                    assert view.image_png is None
                    assert view.app_maps["current_map_index"] == 0
                else:
                    assert view.source == "legacy_current_map"
                    assert view.image_png == b"legacy-image"
                assert legacy_calls == ([(2, 0.1)] if mode == "unknown" else [])
            finally:
                await client.async_close()

    asyncio.run(scenario())
