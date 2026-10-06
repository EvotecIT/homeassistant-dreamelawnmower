"""Native startup contract through the real client and local HTTP transport."""

import asyncio
import time
from unittest.mock import Mock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    protocol_cloud,
)
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
    DreameLawnMowerConnectionError,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
)
from tests.test_async_cloud_session import (
    OPTIONS,
    DreameCloudSession,
    login_response,
    server,
)


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize(
    "fallback", [False, True, "empty", "vendor_error", "http_error"],
)
def test_startup_uses_native_metadata_and_initial_properties(
    monkeypatch, account_type, fallback,
):
    strings = cloud_strings(account_type)
    info = {
        "did": "42", strings[8]: "device-owner",
        strings[35]: "dreame.mower.g2408",
        strings[9]: "mqtt.example.invalid:8883", strings[10]: "{}",
        "mac": "00:11:22:33:44:55",
    }
    firmware = {"fw_ver": "4.3.6_1200", "model": "must-not-override-identity"}
    seen = []
    rpc = []
    battery = DreameMowerProperty.BATTERY_LEVEL
    mqtt = Mock()
    monkeypatch.setattr(protocol_cloud.mqtt_client, "Client", Mock(return_value=mqtt))

    async def handler(request):
        seen.append(request.path)
        if request.path == strings[17]:
            return web.json_response({**login_response(strings), "uid": "account"})
        assert request.headers[strings[46]] == "access-secret"
        if request.path == DEVICE_INFO_PATH:
            data = info
        elif request.path == "/" + "/".join(strings[i] for i in (23, 25, 30)):
            if fallback == "vendor_error":
                return web.json_response({"code": 10001, "data": None})
            if fallback == "http_error":
                return web.json_response({"error": "unavailable"}, status=503)
            data = ({"other": True} if fallback else {
                strings[31]: {strings[32]: firmware},
            })
            if fallback == "empty":
                data = {}
        elif request.path == "/" + "/".join(strings[i] for i in (23, 24, 27, 28)):
            data = {strings[34]: {strings[36]: [
                {"did": "other"}, {**firmware, **info},
            ]}}
        else:
            payload = await request.json()
            assert payload["data"]["method"] == "get_properties"
            rpc.append(payload)
            data = {"result": [{"did": str(battery.value), "code": 0, "value": 55}]}
        return web.json_response({"code": 0, "data": data})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **{**OPTIONS, "account_type": account_type}, session=session,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type=account_type, country="eu",
                ),
            )
            device = client._ensure_device()
            # Map maintenance has a separate lifecycle and transport migration.
            device._map_manager = None
            owner = device._protocol.cloud
            owner.login = Mock(side_effect=AssertionError("Synchronous login"))
            owner.get_device_info = Mock(side_effect=AssertionError("Synchronous info"))
            device._protocol.get_properties = Mock(
                side_effect=AssertionError("Synchronous initial properties"),
            )
            try:
                assert await client._async_update_device() is device
                assert device._ready and device.available
                assert device.info.model == info["model"]
                assert device.info.firmware_version == (
                    None if fallback in {"vendor_error", "http_error"} else "4.3.6_1200"
                )
                assert device.data[battery.value] == 55
                assert owner._uuid == "account"
                assert owner._uid == "device-owner"
                assert owner._key == "access-secret"
                assert seen.count(strings[17]) == 1
                assert len(rpc) == 2
                assert rpc[0]["id"] < rpc[1]["id"]
                assert any(
                    row["did"] == str(battery.value)
                    for row in rpc[0]["data"]["params"]
                )
                mqtt.username_pw_set.assert_called_once_with("account", "access-secret")
                mqtt.connect.assert_called_once_with("mqtt.example.invalid", 8883, 50)
                owner.login.assert_not_called()
                owner.get_device_info.assert_not_called()
                device._protocol.get_properties.assert_not_called()
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["cancel", "close", "offline", "invalid"])
def test_failed_initial_properties_cannot_publish_ready_device(monkeypatch, outcome):
    strings = cloud_strings("dreame")
    mqtt = Mock()
    monkeypatch.setattr(protocol_cloud.mqtt_client, "Client", Mock(return_value=mqtt))

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response({**login_response(strings), "uid": "account"})
            if request.path == DEVICE_INFO_PATH:
                return web.json_response({"code": 0, "data": {
                    "did": "42", strings[8]: "device-owner",
                    strings[35]: "dreame.mower.g2408",
                    strings[9]: "mqtt.example.invalid:8883", strings[10]: "{}",
                }})
            if request.path == "/" + "/".join(strings[i] for i in (23, 25, 30)):
                return web.json_response({"code": 0, "data": {
                    strings[31]: {strings[32]: {"fw_ver": "4.3.6_1200"}},
                }})
            started.set()
            await release.wait()
            return web.json_response({
                "code": 80001 if outcome == "offline" else 0,
                "data": {"result": [{"code": 0, "value": 55}]},
            })

        async with server(monkeypatch, handler), ClientSession() as session:
            client = DreameLawnMowerClient(
                **OPTIONS, session=session,
                descriptor=DreameLawnMowerDescriptor(
                    did="42", name="Garden", model="dreame.mower.g2408",
                    display_model="A2", account_type="dreame", country="eu",
                ),
            )
            device = client._ensure_device()
            device._map_manager = None
            initial_ready = device._ready
            refresh = asyncio.create_task(client._async_update_device())
            try:
                await asyncio.wait_for(started.wait(), 2)
                assert not device._ready
                if outcome == "close":
                    await client.async_close()
                elif outcome == "cancel":
                    refresh.cancel()
                release.set()
                expected = (
                    asyncio.CancelledError if outcome in {"cancel", "close"}
                    else DreameLawnMowerConnectionError
                )
                with pytest.raises(expected):
                    await asyncio.wait_for(refresh, 2)
                assert device._ready == initial_ready
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                await asyncio.gather(refresh, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["timeout", "cancel"])
def test_optional_firmware_failure_does_not_swallow_deadline_or_cancel(
    monkeypatch, stop,
):
    strings = cloud_strings("dreame")

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            if request.path == DEVICE_INFO_PATH:
                return web.json_response({"code": 0, "data": {"did": "42"}})
            entered.set()
            await release.wait()
            return web.json_response({"code": 10001})

        async with server(monkeypatch, handler), ClientSession() as session:
            cloud = DreameCloudSession(session, **OPTIONS)
            read = asyncio.create_task(cloud.async_get_connection_info(
                "42", deadline=time.monotonic() + (0.2 if stop == "timeout" else 5),
            ))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                if stop == "cancel":
                    read.cancel()
                expected = (
                    asyncio.CancelledError if stop == "cancel"
                    else DreameLawnMowerConnectionError
                )
                with pytest.raises(expected):
                    await asyncio.wait_for(read, 1)
                assert not cloud._lock.locked()
                assert not session.closed
            finally:
                release.set()
                await asyncio.gather(read, return_exceptions=True)

    asyncio.run(scenario())
