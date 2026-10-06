"""Public voice setters preserve confirmations without replaying mutations."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerCommandRejectedError,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import (
    DreameLawnMowerConnectionError,
    cloud_strings,
    login_response,
    server,
)

CASES = [
    (
        "language",
        2,
        {"type": "voice", "value": 2},
        {"voice": 3, "text": 1},
        "voice_language_index",
        3,
    ),
    ("volume", 35, {"value": 35}, {"value": 40}, "volume", 40),
    (
        "prompts",
        [1, 0, 1, 0],
        {"value": [1, 0, 1, 0]},
        {"value": [0, 1, 0, 1]},
        "voice_prompts",
        [0, 1, 0, 1],
    ),
]


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("kind,value,payload,confirmation,key,expected", CASES)
@pytest.mark.parametrize("reply", ["success", "disconnect", "rejected", "missing_ack"])
def test_public_voice_write(
    monkeypatch, account_type, kind, value, payload, confirmation, key, expected, reply
):
    strings = cloud_strings(account_type)
    seen = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        body = await request.json()
        seen.append(body["data"]["params"]["in"][0])
        if reply == "disconnect":
            request.transport.close()
            return web.Response()
        result = {"r": 0, "d": confirmation}
        if reply == "rejected":
            result["r"] = 1
        elif reply == "missing_ack":
            result.pop("r")
        return web.json_response({"code": 0, "data": {"result": {"out": [result]}}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account_type)
            try:
                operation = getattr(client, "async_set_voice_" + kind)(value)
                if reply == "success":
                    result = await operation
                    assert result[key] == expected
                    assert result["action"] == "set_voice_" + kind
                    assert result["request"]["d"] == payload
                else:
                    error = (
                        DreameLawnMowerCommandRejectedError
                        if reply == "rejected"
                        else DreameLawnMowerConnectionError
                    )
                    with pytest.raises(error):
                        await operation
                assert seen == [
                    {
                        "m": "s",
                        "t": {"language": "LANG", "volume": "VOL", "prompts": "VOICE"}[
                            kind
                        ],
                        "d": payload,
                    }
                ]
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("volume", [-1, 101])
def test_invalid_volume_does_not_contact_cloud(monkeypatch, volume):
    async def handler(request):
        pytest.fail("Invalid settings must be rejected before authentication")

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            try:
                with pytest.raises(ValueError, match="between 0 and 100"):
                    await client.async_set_voice_volume(volume)
            finally:
                await client.async_close()

    asyncio.run(scenario())
