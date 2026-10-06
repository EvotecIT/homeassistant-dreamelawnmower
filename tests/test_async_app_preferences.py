"""Native preference and lifetime-total reads through the public client."""

from __future__ import annotations

import asyncio
import time

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_app_reads,
)

from .test_async_cloud_session import (
    OPTIONS,
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    DreameLawnMowerDescriptor,
    cloud_strings,
    login_response,
    server,
)


def make_client(session):
    client = DreameLawnMowerClient(
        **OPTIONS, session=session,
        descriptor=DreameLawnMowerDescriptor(
            did="42", name="Garden", model="dreame.mower.g2408",
            display_model="A2", account_type="dreame", country="eu",
        ),
    )
    client._ensure_device()._protocol.cloud._host = "hub.example.invalid"
    return client


@pytest.mark.parametrize("case", ["valid", "partial", "discovery_fails"])
def test_native_preferences_preserve_area_evidence(monkeypatch, case):
    strings = cloud_strings("dreame")
    seen = []

    deadlines = []
    original_read = client_app_reads.async_read_app_action

    async def read(client, action, *, deadline):
        deadlines.append(deadline)
        return await original_read(client, action, deadline=deadline)

    monkeypatch.setattr(client_app_reads, "async_read_app_action", read)

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        body = await request.json()
        action = body["data"]["params"]["in"][0]
        seen.append(action)
        assert action["m"] == "g"
        if action["t"] == "MAPL":
            result = {"r": -1} if case == "discovery_fails" else {
                "r": 0, "d": [[0, 1, 1, 1, 0]],
            }
        elif action["t"] == "PREI":
            result = {"r": 0, "d": {"type": 1, "ver": [[11, 8], [12, 9]]}}
        else:
            idx = action["d"]["idx"]
            area = action["d"]["region"]
            version = 8 if area == 11 else 9
            if case == "partial" and area == 12:
                version = 100
            result = {"r": 0, "d": [
                version, idx, area, 1, 40, 2, 90, 1, 0, 1, 1, 2, 1, 15, 20, 7, 1,
            ]}
        return web.json_response({"code": 0, "data": {"result": {"out": [result]}}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            try:
                started = time.monotonic()
                result = await client.async_get_mowing_preferences(include_raw=True)
                finished = time.monotonic()
                assert len(set(deadlines[1:])) == 1
                assert started + 20 <= deadlines[-1] <= finished + 20
                assert result["available"] is True
                assert [item["idx"] for item in result["maps"]] == (
                    [0, 1] if case == "discovery_fails" else [0]
                )
                first = result["maps"][0]
                assert first["advertised_area_ids"] == [11, 12]
                assert [item["area_id"] for item in first["preferences"]] == (
                    [11] if case == "partial" else [11, 12]
                )
                assert first["preferences"][0]["raw_payload"][0] == 8
                assert bool(result["errors"]) is (case == "partial")
                assert seen[0]["t"] == "MAPL"
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("response", [
    {"r": 0, "d": {"area": 12.5, "time": 30, "count": 2}}, None,
    {"r": 0, "d": {"area": 12.5, "time": -1, "count": 2}},
])
def test_native_work_log_uses_typed_decoder(monkeypatch, response):
    strings = cloud_strings("dreame")

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        body = await request.json()
        assert body["data"]["params"]["in"] == [{"m": "g", "t": "MIHIS"}]
        return web.json_response({"code": 0, "data": {"result": {
            "out": [] if response is None else [response],
        }}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            try:
                if response is None or response["d"]["time"] < 0:
                    with pytest.raises(DreameLawnMowerConnectionError):
                        await client.async_get_work_log_totals()
                else:
                    totals = await client.async_get_work_log_totals()
                    assert totals.total_mowed_area_sqm == 12.5
                    assert totals.total_mowing_time_minutes == 30
                    assert totals.total_mowing_sessions == 2
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("close", [False, True])
def test_native_preferences_cancel_before_next_area(monkeypatch, close):
    strings = cloud_strings("dreame")

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        seen = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            action = body["data"]["params"]["in"][0]
            seen.append(action)
            result = {"r": 0, "d": {"type": 1, "ver": [[11, 8], [12, 9]]}}
            if action["t"] == "PRE":
                entered.set()
                await release.wait()
                result = {"r": -1}
            return web.json_response({"code": 0, "data": {"result": {"out": [result]}}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            task = asyncio.create_task(client.async_get_mowing_preferences(
                map_indices=[-1, 0, 0],
            ))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if close:
                    await asyncio.wait_for(client.async_close(), 2)
                else:
                    task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 2)
                assert [action["t"] for action in seen] == ["PREI", "PRE"]
                assert seen[-1]["d"] == {"idx": 0, "region": 11}
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())
