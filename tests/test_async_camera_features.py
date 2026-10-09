"""Native camera discovery preserves capability policy without device actions."""

from __future__ import annotations

import asyncio
from threading import RLock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device_plan_cleanup,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.cloud_wire import (
    DEVICE_METADATA_PATHS,
)

from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_map_objects import make_client
from .test_camera_features import _FakeCameraDevice


def camera_client(session, account):
    client = make_client(session, account)
    original = client._device
    device = _FakeCameraDevice()
    device._state_lock = RLock()
    device._plan_cleanup = device_plan_cleanup._DevicePlanCleanup()
    device.disconnect = original.disconnect
    device.listen = original.listen
    client._device = device
    client._ensure_device = lambda **kwargs: device
    return client, device


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("mode", ["success", "offline", "cached"])
def test_native_camera_feature_summary(monkeypatch, account, mode):
    strings = cloud_strings(account)
    features = {"video": True, "permit": "video", "private": "not-in-summary"}
    calls = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        assert request.path == DEVICE_METADATA_PATHS["features"]
        calls.append(await request.json())
        if mode == "offline":
            request.transport.close()
            return web.Response()
        return web.json_response({"code": 0, "data": features})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client, device = camera_client(session, account)
            client._sync_get_cloud_user_features = lambda language: features
            try:
                expected = client._sync_get_camera_feature_support(
                    include_cloud=mode == "success", language="pl")
                result = await client.async_get_camera_feature_support(
                    include_cloud=mode != "cached", language="pl")
                if mode == "offline":
                    assert result.supported == expected.supported
                    assert result.cloud_user_features is None
                    assert result.cloud_user_features_error
                else:
                    assert result == expected
                assert bool(calls) == (mode != "cached")
                assert not device.actions
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("close_client", [False, True])
def test_camera_discovery_network_cancellation(monkeypatch, close_client):
    strings = cloud_strings("dreame")

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            entered.set()
            await release.wait()
            return web.json_response({"code": 0, "data": {"video": True}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client, device = camera_client(session, "dreame")
            task = asyncio.create_task(client.async_get_camera_feature_support())
            try:
                await asyncio.wait_for(entered.wait(), 3)
                if close_client:
                    await asyncio.wait_for(client.async_close(), 3)
                else:
                    task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not device.actions
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())
