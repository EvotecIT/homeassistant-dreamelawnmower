"""Native app-map transaction, integrity, cache and lifetime contracts."""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import ClientSession, web

from .test_app_maps import _FakeAppMapCloud
from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_map_objects import make_client


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("bad_first_chunk", [False, True])
def test_native_maps_verify_retry_cache_and_private_objects(
    monkeypatch, account_type, bad_first_chunk
):
    strings = cloud_strings(account_type)
    fake = _FakeAppMapCloud({"map": [{"data": [[0, 0], [20, 0], [20, 20]]}]})
    failed = False

    async def scenario():
        async def handler(request):
            nonlocal failed
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            (action,) = body["data"]["params"]["in"]
            response = fake.call_app_action(
                action, redact_response=action["t"] == "OBJ"
            )
            if action["t"] == "MAPD" and bad_first_chunk and not failed:
                failed = True
                response = {"out": [{"d": {"data": "{", "size": action["d"]["size"]}}]}
            return web.json_response({"code": 0, "data": {"result": response}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session, account_type)
            try:
                first = await client.async_get_app_maps(include_payload=True)
                entry = first["maps"][0]
                assert entry["available"] and entry["hash_match"]
                assert entry["download_attempts"] == (2 if bad_first_chunk else 1)
                assert entry["payload"] == json.loads(fake.payload_text)
                assert not first["objects"]["urls_included"]
                assert all("name" not in item for item in first["objects"]["objects"])
                assert client._latest_app_map_inventory_identity is not None
                entry["payload"]["map"].clear()
                fake.calls.clear()
                cached = await client.async_get_app_maps(
                    include_payload=True, include_objects=False
                )
                assert cached["maps"][0]["payload"] == json.loads(fake.payload_text)
                assert cached["maps"][0]["payload_cached"]
                assert [c["t"] for c in fake.calls] == ["MAPL", "MAPI"]
                hidden = await client.async_get_app_maps(include_objects=False)
                assert "payload" not in hidden["maps"][0]
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "close"])
@pytest.mark.parametrize("phase", ["MAPL", "MAPI", "MAPD"])
def test_native_map_stop_releases_transaction_without_cache(monkeypatch, stop, phase):
    strings = cloud_strings("dreame")
    fake = _FakeAppMapCloud({"map": []})

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            (action,) = body["data"]["params"]["in"]
            calls.append(action["t"])
            if action["t"] == phase:
                entered.set()
                await release.wait()
            response = fake.call_app_action(action)
            return web.json_response({"code": 0, "data": {"result": response}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            task = asyncio.create_task(client.async_get_app_maps())
            try:
                await asyncio.wait_for(entered.wait(), 3)
                if stop == "close":
                    await asyncio.wait_for(client.async_close(), 3)
                else:
                    task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert calls[-1] == phase
                assert "OBJ" not in calls
                assert not client._app_map_payload_cache
                assert not client._app_map_download_lock.locked()
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                await client.async_close()

    asyncio.run(scenario())


def test_native_map_waiter_cannot_release_foreign_lock(monkeypatch):
    async def scenario():
        async with ClientSession() as session:
            client = make_client(session)
            client._app_map_download_lock.acquire()
            task = asyncio.create_task(client.async_get_app_maps())
            try:
                await asyncio.sleep(0.03)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert client._app_map_download_lock.locked()
                assert not client._cloud_read_tasks
            finally:
                client._app_map_download_lock.release()
                await client.async_close()

    asyncio.run(scenario())


def test_native_map_readers_keep_selected_cursor_transaction(monkeypatch):
    strings = cloud_strings("dreame")
    fake = _FakeAppMapCloud({"map": []})

    async def scenario():
        chunk_entered = asyncio.Event()
        release_chunk = asyncio.Event()
        calls = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            (action,) = body["data"]["params"]["in"]
            calls.append(action["t"])
            if action["t"] == "MAPD":
                chunk_entered.set()
                await release_chunk.wait()
            return web.json_response(
                {"code": 0, "data": {"result": fake.call_app_action(action)}}
            )

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            first = asyncio.create_task(
                client.async_get_app_maps(include_objects=False)
            )
            second = None
            try:
                await asyncio.wait_for(chunk_entered.wait(), 3)
                second = asyncio.create_task(
                    client.async_get_app_maps(include_objects=False)
                )
                await asyncio.sleep(0.04)
                assert calls == ["MAPL", "MAPI", "MAPD"]
                release_chunk.set()
                a, b = await asyncio.wait_for(asyncio.gather(first, second), 3)
                assert a["available"] and b["available"]
                assert calls == ["MAPL", "MAPI", "MAPD", "MAPL", "MAPI"]
                assert b["maps"][0]["payload_cached"]
            finally:
                release_chunk.set()
                await client.async_close()
                await asyncio.gather(
                    first, *([second] if second else []), return_exceptions=True
                )

    asyncio.run(scenario())
