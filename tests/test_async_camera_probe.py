"""Native camera probes preserve diagnostics and response-application ownership."""

from __future__ import annotations

import asyncio
from threading import Event
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_camera_probe,
)

from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_photo_info import photo_client

async_camera_property_probe = client_camera_probe.async_camera_property_probe


def test_source_probe_preserves_diagnostics_after_property_timeout(monkeypatch):
    monkeypatch.setattr(client_camera_probe, "_PROPERTY_PROBE_TIMEOUT", 0.1)
    strings = cloud_strings("dreame")

    async def scenario():
        release = asyncio.Event()
        calls = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            calls.append(await request.json())
            await release.wait()
            return web.json_response({"code": 0, "data": {"result": []}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = photo_client(session)
            client._async_update_device = AsyncMock(return_value=client._device)
            client.async_get_cloud_user_features = AsyncMock(return_value={})
            client.async_scan_cloud_properties = AsyncMock(return_value={
                "requested_key_count": 1, "entries": []})
            applied = []
            client._device._handle_properties = lambda value: applied.append(value)
            try:
                result = await client.async_probe_camera_sources()
                assert result["support"]["supported"] is True
                assert result["device_properties"]["error"]
                assert result["device_properties"]["handled"] is False
                assert not applied
                assert len(calls) == 1
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("reply", ["success", "missing", "disconnect", "apply_error"])
def test_native_camera_property_probe(monkeypatch, account, reply):
    strings = cloud_strings(account)
    requests = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        body = (await request.json())["data"]
        assert body["method"] == "get_properties"
        requests.append(body["params"])
        if reply == "disconnect":
            request.transport.close()
            return web.Response()
        result = None if reply == "missing" else [
            {**row, "code": 0, "value": "observed"} for row in body["params"]]
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = photo_client(session, account)
            device = client._device
            original = device._handle_properties

            def apply(response):
                assert device._state_lock._is_owned()
                assert device._protocol.cloud._operation_lock()._is_owned()
                if reply == "apply_error":
                    raise ValueError("invalid property value")
                return original(response)

            device._handle_properties = apply
            try:
                result = await async_camera_property_probe(client)
                assert result["requested_property_count"] == 10
                assert requests and all(r == result["requested_properties"]
                                        for r in requests)
                assert result["handled"] == (reply == "success")
                assert bool(result["error"]) == (reply != "success")
                if reply == "success":
                    assert result["values"]["stream_status"] == "observed"
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["network", "apply"])
@pytest.mark.parametrize("close_client", [False, True])
def test_camera_probe_cancellation(monkeypatch, phase, close_client):
    strings = cloud_strings("dreame")
    apply_entered, apply_release, apply_finished = Event(), Event(), Event()

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            entered.set()
            if phase == "network":
                await release.wait()
            return web.json_response({"code": 0, "data": {"result": []}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = photo_client(session)
            device = client._device

            def apply(response):
                apply_entered.set()
                assert apply_release.wait(5)
                apply_finished.set()
                return True

            device._handle_properties = apply
            task = asyncio.create_task(async_camera_property_probe(client))
            close = None
            try:
                await asyncio.wait_for(entered.wait(), 3)
                if phase == "apply":
                    assert await asyncio.to_thread(apply_entered.wait, 3)
                if close_client:
                    close = asyncio.create_task(client.async_close())
                else:
                    task.cancel()
                if phase == "apply":
                    await asyncio.sleep(0.03)
                    assert not task.done()
                    assert client._device is device
                    apply_release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                if close is not None:
                    await close
                assert apply_finished.is_set() == (phase == "apply")
                assert not client._cloud_read_tasks
                assert not session.closed
            finally:
                release.set()
                apply_release.set()
                await asyncio.gather(task, return_exceptions=True)
                if close is not None:
                    await close
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("request_properties", [False, True])
def test_source_probe_preserves_optional_device_read(monkeypatch, request_properties):
    strings = cloud_strings("dreame")
    calls = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        calls.append(await request.json())
        return web.json_response({"code": 0, "data": {"result": []}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = photo_client(session)
            client._async_update_device = AsyncMock(return_value=client._device)
            client.async_get_cloud_user_features = AsyncMock(
                return_value={"video": True})
            client.async_scan_cloud_properties = AsyncMock(return_value={
                "requested_key_count": 1, "returned_entry_count": 0,
                "displayed_entry_count": 0, "entries": []})
            try:
                result = await client.async_probe_camera_sources(
                    language="pl", request_device_properties=request_properties)
                assert result["support"]["supported"] is True
                assert result["cloud_property_summary"]["requested_key_count"] == 1
                if request_properties:
                    assert result["device_properties"]["handled"] is True
                else:
                    assert result["device_properties"] == {"skipped": True}
                assert len(calls) == int(request_properties)
                client._async_update_device.assert_awaited_once()
                client.async_get_cloud_user_features.assert_awaited_once_with(language="pl")
            finally:
                await client.async_close()

    asyncio.run(scenario())
