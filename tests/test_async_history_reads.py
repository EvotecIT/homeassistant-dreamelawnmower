"""Authenticated history payload, reauthentication and absent-data contracts."""

from __future__ import annotations

import asyncio
import time

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.cloud_history import (
    history_params,
)

from .test_async_cloud_session import (
    OPTIONS,
    DreameCloudSession,
    cloud_strings,
    login_response,
    server,
)


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("renew", [False, True])
def test_history_uses_authenticated_uid_and_rebuilds_after_401(
    monkeypatch, account_type, renew
):
    strings = cloud_strings(account_type)
    logins = []
    requests = []
    records = [{"value": "map-file", "time": 123}]

    async def handler(request):
        if request.path == strings[17]:
            uid = str(len(logins) + 41)
            logins.append(uid)
            return web.json_response({**login_response(strings), "uid": uid})
        assert request.path == "/" + "/".join(strings[i] for i in (23, 25, 43))
        requests.append(await request.json())
        if renew and len(requests) == 1:
            return web.Response(status=401, text="expired")
        return web.json_response({"code": 0, "data": {strings[33]: records}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(
                session, **{**OPTIONS, "account_type": account_type}
            )
            result = await cloud.async_get_property_history(
                "99", "6.7", limit=3, time_start=0, deadline=time.monotonic() + 3
            )
            assert result == records
            assert requests == [
                history_params(strings, uid, "99", "eu", "6.7", "prop", 3, 0)
                for uid in logins
            ]
            assert len(logins) == (2 if renew else 1)
            assert requests[-1]["uid"] == ("42" if renew else "41")
            assert requests[-1]["from"] == 1687019188

    asyncio.run(scenario())


@pytest.mark.parametrize("data", [None, {}, []])
def test_absent_history_is_none(monkeypatch, data):
    strings = cloud_strings("dreame")

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response({**login_response(strings), "uid": "41"})
        return web.json_response({"code": 0, "data": data})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **OPTIONS)
            assert await cloud.async_get_property_history("42", "6.7") is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind,field", [("prop", "piid"), ("event", "eiid"), ("action", "aiid")]
)
def test_shared_history_wire_fields_retain_vendor_contract(kind, field):
    strings = cloud_strings("dreame")
    params = history_params(strings, "7", "8", "eu", "6.9", kind, 4, 123)
    assert params == {
        "uid": "7",
        "did": "8",
        "from": 123,
        "limit": 4,
        "siid": "6",
        strings[21]: "eu",
        strings[42]: 3,
        field: "9",
    }
