"""Stored mowers load from authenticated presence and recover through real RPC."""

import asyncio
from unittest.mock import Mock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import protocol_cloud
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client import (
    DreameLawnMowerClient,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.cloud_wire import (
    DEVICE_INFO_PATH,
    cloud_strings,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerProperty,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerAuthError,
    DreameLawnMowerConnectionError,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
)
from tests.test_async_cloud_session import OPTIONS, login_response, server


@pytest.mark.parametrize(
    "account,model,presence",
    [("dreame", "g2422", False), ("dreame", "g2408", 0),
     ("mova", "g2529c", "false")],
)
def test_powered_off_refresh_is_bounded_and_recovers_native_properties(
    monkeypatch, account, model, presence,
):
    strings = cloud_strings(account)
    mqtt = Mock()
    monkeypatch.setattr(protocol_cloud.mqtt_client, "Client", Mock(return_value=mqtt))
    online = presence
    seen = []
    rpc = []
    battery = DreameMowerProperty.BATTERY_LEVEL

    async def handler(request):
        seen.append(request.path)
        if request.path == strings[17]:
            return web.json_response({**login_response(strings), "uid": "account"})
        assert request.headers[strings[46]] == "access-secret"
        if request.path == DEVICE_INFO_PATH:
            data = {
                "did": "42", "online": online,
                strings[8]: "device-owner", strings[35]: f"dreame.mower.{model}",
                strings[9]: "mqtt.example.invalid:8883", strings[10]: "{}",
            }
        elif request.path == "/" + "/".join(strings[i] for i in (23, 25, 30)):
            data = {strings[31]: {strings[32]: {"fw_ver": "4.3.6_1200"}}}
        elif request.path == "/" + "/".join(strings[i] for i in (23, 26, 44)):
            data = {}
        else:
            payload = await request.json()
            assert payload["data"]["method"] == "get_properties"
            rpc.append(payload)
            data = {"result": [
                {"did": str(battery.value), "code": 0, "value": 55},
            ]} if online is True else {"result": []}
        return web.json_response({"code": 0, "data": data})

    async def scenario():
        nonlocal online
        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **{**OPTIONS, "account_type": account}, session=session,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model=f"dreame.mower.{model}",
                    display_model=model, account_type=account, country="eu",
                ),
            )
            # Disable optional map creation, keeping real startup/state owners.
            client._ensure_device()._map_manager = None
            try:
                snapshot = await client.async_refresh()
                assert snapshot.online is False and snapshot.available is False
                assert snapshot.state == snapshot.activity == "unknown"
                assert snapshot.battery_level is None
                assert snapshot.device_connected is False
                assert client._device._ready is False
                assert rpc == []
                assert seen == [strings[17], DEVICE_INFO_PATH]
                mqtt.connect.assert_not_called()

                requests = len(seen)
                for _ in range(3):
                    assert (await client.async_refresh()).online is False
                assert len(seen) == requests

                online = True
                client._cloud_device_info_refreshed_at -= 61
                recovered = await client.async_refresh()
                assert recovered.available is True and recovered.online is True
                assert recovered.battery_level == 55
                assert client._device._ready is True
                assert rpc
            finally:
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure",
    ["unknown", "malformed", "missing_flag", "missing", "authentication", "transport"],
)
@pytest.mark.parametrize("stage", ["initial", "after_offline"])
def test_offline_startup_does_not_hide_cloud_or_unknown_device_failures(
    monkeypatch, failure, stage,
):
    strings = cloud_strings("dreame")
    monkeypatch.setattr(protocol_cloud.mqtt_client, "Client", Mock())
    stored = stage == "after_offline"

    async def handler(request):
        if request.path == strings[17]:
            if failure == "authentication" and not stored:
                return web.json_response({"error": "invalid_grant"}, status=401)
            return web.json_response({**login_response(strings), "uid": "account"})
        if request.path == DEVICE_INFO_PATH:
            if failure == "authentication" and not stored:
                return web.json_response({"error": "invalid_token"}, status=401)
            if failure == "transport" and not stored:
                return web.json_response({"error": "unavailable"}, status=503)
            data = None if failure == "missing" else {
                "did": "42", "online": "unknown", strings[8]: "device-owner",
                strings[35]: "dreame.mower.g2408",
                strings[9]: "mqtt.example.invalid:8883", strings[10]: "{}",
            }
            if failure == "malformed":
                data["online"] = []
            if failure == "missing_flag":
                data.pop("online")
            if stored:
                data = {
                    "did": "42", "online": False,
                    strings[35]: "dreame.mower.g2408",
                }
        elif request.path == "/" + "/".join(strings[i] for i in (23, 25, 30)):
            data = {strings[31]: {strings[32]: {"fw_ver": "4.3.6_1200"}}}
        else:
            data = {"result": []}
        return web.json_response({"code": 0, "data": data})

    async def scenario():
        nonlocal stored
        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **OPTIONS, session=session,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type="dreame", country="eu",
                ),
            )
            client._ensure_device()._map_manager = None
            try:
                previous = None
                if stored:
                    previous = await client.async_refresh()
                    assert previous.online is False
                    stored = False
                    client._cloud_device_info_refreshed_at -= 61
                error = (DreameLawnMowerAuthError if failure == "authentication"
                         else DreameLawnMowerConnectionError)
                for _ in range(2):
                    with pytest.raises(error):
                        await client.async_refresh()
                assert client._latest_snapshot is previous
                assert client._device._ready is False
            finally:
                await client.async_close()

    asyncio.run(scenario())
