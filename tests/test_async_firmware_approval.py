"""Native firmware approval preserves evidence and never replays a command."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession, web

from .test_async_app_commands import client_for
from .test_async_cloud_session import (
    DreameLawnMowerConnectionError,
    cloud_strings,
    login_response,
    server,
)


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize(
    "reply", ["success", "rejected", "inner_rejected", "disconnect", "401", "503"]
)
def test_firmware_approval_once(monkeypatch, account_type, reply):
    strings = cloud_strings(account_type)
    seen = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        assert request.path == "/dreame-user-iot/iotuserbind/manualFirmwareUpdate"
        seen.append(await request.json())
        if reply == "disconnect":
            request.transport.close()
            return web.Response()
        if reply in {"401", "503"}:
            return web.Response(status=int(reply))
        return web.json_response(
            {
                "code": 0 if reply != "rejected" else 5,
                "success": reply != "rejected",
                "data": {
                    "code": 9 if reply == "inner_rejected" else 0,
                    "success": reply != "inner_rejected",
                },
            }
        )

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account_type)
            try:
                operation = client.async_approve_firmware_update(language="en")
                if reply in {"disconnect", "401", "503"}:
                    with pytest.raises(DreameLawnMowerConnectionError):
                        await operation
                else:
                    result = await operation
                    assert result["accepted"] is (reply != "rejected")
                    assert result["success"] is (reply != "rejected")
                    assert result["inner_success"] is (reply != "inner_rejected")
                    assert result["inner_code"] == (
                        9 if reply == "inner_rejected" else 0
                    )
                    assert result["source"] == "cloud_manual_firmware_update"
                assert seen == [{"did": "42", "lang": "en"}]
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_firmware_approval_cancellation(monkeypatch, stop):
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
            return web.json_response({"code": 0})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            operation = asyncio.create_task(client.async_approve_firmware_update())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if stop == "close":
                    await asyncio.wait_for(client.async_close(), 2)
                else:
                    operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(operation, 2)
                assert seen == [{"did": "42"}]
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await asyncio.gather(operation, return_exceptions=True)
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())
