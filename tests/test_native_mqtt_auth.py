"""MQTT reconnect authentication uses the owned native HTTP client."""

import asyncio
from unittest.mock import Mock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_mqtt_auth,
    protocol_cloud,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import cloud_strings, login_response, server


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("close", [False, True])
def test_reconnect_login_is_coalesced_and_owned(monkeypatch, account, close):
    strings = cloud_strings(account)

    async def scenario():
        entered, release, applied = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(request):
            calls.append(request.path)
            assert request.path == strings[17]
            entered.set()
            await release.wait()
            return web.json_response(login_response(strings))

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            device = client._ensure_device()
            protocol = device._protocol.cloud
            protocol._key_expire = 1
            protocol._client = mqtt = Mock()
            protocol.login = Mock(side_effect=AssertionError("Blocking login"))
            owner = client_mqtt_auth.NativeMqttAuthentication(client, device, protocol)
            protocol._native_authentication_request = owner.request
            original_apply = protocol._apply_authentication
            loop = asyncio.get_running_loop()

            def apply(authentication):
                original_apply(authentication)
                loop.call_soon_threadsafe(applied.set)

            protocol._apply_authentication = apply

            def notify():
                protocol_cloud.DreameMowerDreameHomeCloudProtocol._on_client_disconnect(
                    mqtt, protocol, 5,
                )

            try:
                await asyncio.to_thread(notify)
                await asyncio.wait_for(entered.wait(), 3)
                await asyncio.to_thread(notify)
                await asyncio.sleep(0)
                assert len(calls) == 1
                if close:
                    await asyncio.wait_for(client.async_close(), 3)
                    assert not applied.is_set()
                    assert not client._cloud_read_tasks
                    await asyncio.to_thread(notify)
                    await asyncio.sleep(0)
                    assert len(calls) == 1
                else:
                    release.set()
                    await asyncio.wait_for(applied.wait(), 3)
                    await asyncio.wait_for(owner._task, 3)
                    assert protocol._key == client._async_cloud.authentication.token
                    mqtt.username_pw_set.assert_called_with(
                        protocol._uuid, protocol._key,
                    )
                protocol.login.assert_not_called()
                assert not session.closed
            finally:
                release.set()
                await client.async_close()

    asyncio.run(scenario())
