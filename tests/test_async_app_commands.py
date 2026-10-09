"""Native app mutations keep RPC ownership and never replay uncertain requests."""

from __future__ import annotations

import asyncio
import time

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_app_reads,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerCloudAPIError,
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


def client_for(session, account_type="dreame"):
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
    return client


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize(
    "reply", ["success", "disconnect", "401", "503", "invalid_json", "rejected"]
)
def test_native_command_is_dispatched_once(monkeypatch, account_type, reply):
    strings = cloud_strings(account_type)
    seen = []
    dispatched = []
    logins = []
    action = {"m": "a", "p": 0, "o": 10, "d": {"idx": 1}}

    async def handler(request):
        if request.path == strings[17]:
            logins.append(True)
            assert not dispatched
            return web.json_response(login_response(strings))
        assert dispatched == [True]
        body = await request.json()
        seen.append(body)
        assert body["data"]["params"]["in"] == [action]
        if reply == "disconnect":
            request.transport.close()
            return web.Response()
        if reply in {"401", "503"}:
            return web.Response(status=int(reply))
        if reply == "invalid_json":
            return web.Response(text="private response")
        if reply == "rejected":
            return web.json_response({"code": 80001, "message": "private reason"})
        return web.json_response({"code": 0, "data": {"result": {"out": [{"r": 0}]}}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account_type)
            protocol = client._device._protocol.cloud
            protocol._id = 100
            try:
                operation = client_app_reads.async_command_app_action(
                    client,
                    action,
                    deadline=time.monotonic() + 3,
                    on_dispatch=lambda: dispatched.append(True),
                )
                if reply == "success":
                    assert await operation == {"r": 0}
                else:
                    error = (
                        DreameLawnMowerCloudAPIError
                        if reply == "rejected"
                        else DreameLawnMowerConnectionError
                    )
                    with pytest.raises(error) as captured:
                        await operation
                    assert "private" not in str(captured.value)
                    if reply == "rejected":
                        assert captured.value.code == 80001
                assert len(seen) == len(dispatched) == len(logins) == 1
                assert protocol._id == 101
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "close", "deadline"])
def test_native_command_cancellation_releases_ownership(monkeypatch, stop):
    strings = cloud_strings("dreame")

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        seen = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            seen.append(await request.json())
            entered.set()
            await release.wait()
            return web.json_response(
                {"code": 0, "data": {"result": {"out": [{"r": 0}]}}}
            )

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            request = asyncio.create_task(
                client_app_reads.async_command_app_action(
                    client,
                    {"m": "a", "p": 0, "o": 10, "d": {"idx": 0}},
                    deadline=time.monotonic() + (0.1 if stop == "deadline" else 3),
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if stop == "close":
                    await asyncio.wait_for(client.async_close(), 2)
                elif stop == "cancel":
                    request.cancel()
                with pytest.raises(
                    DreameLawnMowerConnectionError
                    if stop == "deadline"
                    else asyncio.CancelledError
                ):
                    await request
                assert len(seen) == 1
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await asyncio.gather(request, return_exceptions=True)
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())
