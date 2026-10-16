"""Backup reads use indexed getters and never publish active-map identity."""

import asyncio
import hashlib
import json

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerConnectionError,
)

from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_map_objects import make_client


@pytest.mark.parametrize("index", [-1, True, "0"])
def test_invalid_backup_index_is_rejected_before_any_transport(index):
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client import (
        DreameLawnMowerClient,
    )

    with pytest.raises(ValueError, match="non-negative integer"):
        asyncio.run(DreameLawnMowerClient.async_get_app_backup_map(None, index))


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("include_payload", [False, True])
def test_native_backup_is_verified_private_and_separate_from_active_map(
    monkeypatch,
    account_type,
    include_payload,
):
    strings = cloud_strings(account_type)
    payload = {"map": [{"id": 2, "data": [[123, 456], [789, 101]]}]}
    text = json.dumps(payload, separators=(",", ":"))
    calls = []

    async def scenario():
        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            (action,) = body["data"]["params"]["in"]
            calls.append(action)
            assert action["m"] == "g"
            assert action["d"]["idx"] == 1
            if action["t"] == "MAPBI":
                data = {
                    "idx": 1,
                    "size": len(text),
                    "hash": hashlib.md5(text.encode()).hexdigest(),
                    "private_metadata": {"coordinates": [123, 456]},
                }
            else:
                assert action["t"] == "MAPBD"
                offset = action["d"]["start"]
                chunk = text[offset : offset + action["d"]["size"]]
                data = {"data": chunk, "size": len(chunk)}
            return web.json_response(
                {"code": 0, "data": {"result": {"out": [{"d": data}]}}}
            )

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session, account_type)
            client._app_map_payload_cache[0] = {"sentinel": "active"}
            identity = client._latest_app_map_inventory_identity
            try:
                result = await client.async_get_app_backup_map(
                    1, chunk_size=32, include_payload=include_payload
                )
                assert result["source"] == "app_action_backup_map"
                assert result["backup_index"] == 1
                assert result["available"] is True
                assert result["hash_match"] is True
                assert set(result["info"]) == {"idx", "size", "hash"}
                assert "private_metadata" not in json.dumps(result)
                assert ("payload" in result) is include_payload
                if include_payload:
                    assert result["payload"] == payload
                assert client._app_map_payload_cache == {0: {"sentinel": "active"}}
                assert client._latest_app_map_inventory_identity == identity
                assert not client._app_map_download_lock.locked()
                assert [call["t"] for call in calls][0] == "MAPBI"
                assert all(call["t"] == "MAPBD" for call in calls[1:])
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("problem", ["missing_hash", "wrong_hash", "oversized"])
def test_backup_rejects_unverified_or_oversized_data_and_releases_lock(
    monkeypatch, problem
):
    strings = cloud_strings("dreame")
    text = '{"map":[]}'

    async def scenario():
        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            (action,) = body["data"]["params"]["in"]
            if action["t"] == "MAPBI":
                data = {"size": 5_000_000 if problem == "oversized" else len(text)}
                if problem != "missing_hash":
                    data["hash"] = "0" * 32
            else:
                assert problem == "wrong_hash"
                data = {"data": text, "size": len(text)}
            return web.json_response(
                {"code": 0, "data": {"result": {"out": [{"d": data}]}}}
            )

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            try:
                with pytest.raises(DreameLawnMowerConnectionError):
                    await client.async_get_app_backup_map(0)
                assert not client._app_map_download_lock.locked()
                assert client._app_map_payload_cache == {}
            finally:
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["MAPBI", "MAPBD"])
def test_cancelled_backup_releases_shared_map_transaction(monkeypatch, phase):
    strings = cloud_strings("dreame")
    text = '{"map":[]}'

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            (action,) = body["data"]["params"]["in"]
            if action["t"] == phase:
                entered.set()
                await release.wait()
            data = (
                {"size": len(text), "hash": hashlib.md5(text.encode()).hexdigest()}
                if action["t"] == "MAPBI"
                else {"data": text, "size": len(text)}
            )
            return web.json_response(
                {"code": 0, "data": {"result": {"out": [{"d": data}]}}}
            )

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            task = asyncio.create_task(client.async_get_app_backup_map(0))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not client._app_map_download_lock.locked()
                assert not client._app_map_payload_cache
            finally:
                release.set()
                await client.async_close()

    asyncio.run(scenario())
