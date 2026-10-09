"""Native map-object privacy, identity and signing contracts."""

from __future__ import annotations

import asyncio
import json
import time
from threading import Event

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DeviceException,
    DreameLawnMowerCloudAPIError,
)

from .test_async_cloud_session import (
    OPTIONS,
    DreameCloudSession,
    DreameLawnMowerClient,
    DreameLawnMowerDescriptor,
    cloud_strings,
    login_response,
    server,
)


def make_client(session, account_type="dreame"):
    client = DreameLawnMowerClient(
        **{**OPTIONS, "account_type": account_type},
        session=session,
        descriptor=DreameLawnMowerDescriptor(
            did="42",
            name="Garden",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type=account_type,
            country="eu",
        ),
    )
    client._ensure_device()._protocol.cloud._host = "hub.example.invalid"
    set_inventory(client, "old")
    return client


def set_inventory(client, identity):
    client._sync_update_app_map_inventory_identity(
        [
            {
                "idx": 0,
                "created": True,
                "current": True,
                "info": {"hash": identity, "size": 10},
            },
        ]
    )


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("include_urls", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_native_objects_keep_privacy_and_inventory_identity(
    monkeypatch,
    caplog,
    account_type,
    include_urls,
    changed,
):
    strings = cloud_strings(account_type)
    signed = []
    private_name = "private/map.bin"

    async def scenario():
        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            signer = "/" + "/".join(strings[i] for i in (23, 39, 55))
            if request.path == signer:
                signed.append(body)
                assert body == {
                    "did": "42",
                    strings[35]: "dreame.mower.g2408",
                    strings[40]: private_name,
                    strings[21]: "eu",
                }
                return web.json_response(
                    {"code": 0, "data": "https://cdn.invalid/signed"}
                )
            assert body["data"]["params"]["in"] == [
                {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
            ]
            if changed:
                set_inventory(client, "replacement")
            return web.json_response(
                {
                    "code": 0,
                    "data": {
                        "result": {
                            "out": [
                                {"r": 0, "d": {"name": [private_name]}},
                            ]
                        }
                    },
                }
            )

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session, account_type)
            try:
                result = await client.async_get_app_map_objects(
                    include_urls=include_urls
                )
                assert result["object_count"] == result["named_object_count"] == 1
                assert result["urls_included"] is include_urls
                item = result["objects"][0]
                assert item["extension"] == "bin"
                assert item["url_checked"] is include_urls
                if include_urls:
                    assert item["name"] == private_name
                    assert item["url"] == "https://cdn.invalid/signed"
                    assert "raw" in result
                    assert len(signed) == 1
                else:
                    assert private_name not in json.dumps(result)
                    assert "raw" not in result
                    assert not signed
                assert client._latest_app_map_object_names == (
                    () if changed else (private_name,)
                )
                assert client._latest_app_map_object_inventory_identity == (
                    None if changed else client._latest_app_map_inventory_identity
                )
                assert private_name not in caplog.text
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "response,expected",
    [
        (
            {"code": 0, "data": "https://cdn.invalid/object"},
            "https://cdn.invalid/object",
        ),
        ({"code": 10007}, None),
        ({"code": 0, "data": None}, None),
        ({"code": 50001}, "rejected"),
        ({"code": False, "data": None}, "invalid"),
    ],
)
def test_native_signer_preserves_strict_cloud_results(monkeypatch, response, expected):
    strings = cloud_strings("dreame")

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response(response)

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **OPTIONS)
            operation = cloud.async_get_interim_file_url(
                "42",
                "dreame.mower.g2408",
                "private/map.bin",
                deadline=time.monotonic() + 5,
                require_response=True,
            )
            if expected == "rejected":
                with pytest.raises(DreameLawnMowerCloudAPIError) as error:
                    await operation
                assert error.value.code == 50001
            elif expected == "invalid":
                with pytest.raises(DeviceException, match="invalid code"):
                    await operation
            else:
                assert await operation == expected
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_native_object_read_stops_before_cache_commit_and_signing(monkeypatch, stop):
    strings = cloud_strings("dreame")

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        requests = []

        async def handler(request):
            requests.append(request.path)
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            entered.set()
            await release.wait()
            return web.json_response(
                {
                    "code": 0,
                    "data": {
                        "result": {
                            "out": [
                                {"r": 0, "d": {"name": ["private/map.bin"]}},
                            ]
                        }
                    },
                }
            )

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            task = asyncio.create_task(
                client.async_get_app_map_objects(include_urls=True)
            )
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if stop == "close":
                    await asyncio.wait_for(client.async_close(), 2)
                else:
                    task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
                assert client._latest_app_map_object_names == ()
                assert client._latest_app_map_object_inventory_identity is None
                assert len(requests) == 2
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


def test_native_object_cache_wait_is_cancellable_without_foreign_unlock():
    acquired = Event()
    release = Event()

    async def scenario():
        async with ClientSession() as session:
            client = make_client(session)

            def holder():
                with client._app_map_object_cache_lock:
                    acquired.set()
                    assert release.wait(5)

            owner = asyncio.create_task(asyncio.to_thread(holder))
            assert await asyncio.to_thread(acquired.wait, 5)
            task = asyncio.create_task(client.async_get_app_map_objects())
            try:
                await asyncio.sleep(0.05)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
                assert not owner.done()
                assert client._app_map_object_cache_lock.locked()
                assert client._latest_app_map_object_names == ()
            finally:
                release.set()
                await owner
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


def test_native_object_signing_keeps_partial_results(monkeypatch):
    strings = cloud_strings("dreame")
    signed = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        body = await request.json()
        if strings[40] in body:
            signed.append(body[strings[40]])
            if len(signed) == 1:
                return web.Response(status=400)
            return web.json_response({"code": 0, "data": "https://cdn.invalid/map"})
        return web.json_response({"code": 0, "data": {"result": {"out": [
            {"r": 0, "d": {"name": ["private/first.bin", "private/second.bin"]}},
        ]}}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            try:
                result = await client.async_get_app_map_objects(include_urls=True)
                first, second = result["objects"]
                assert first["url_checked"] is True
                assert first["url_present"] is False
                assert "HTTP 400" in first["error"]
                assert second["url_present"] is True
                assert second["url"] == "https://cdn.invalid/map"
                assert signed == ["private/first.bin", "private/second.bin"]
                assert client._latest_app_map_object_names == tuple(signed)
            finally:
                await client.async_close()

    asyncio.run(scenario())
