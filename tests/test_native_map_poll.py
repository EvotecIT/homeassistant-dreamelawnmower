"""Native polling owns response-dependent recovery and interrupted work."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_map_application,
    client_map_poll,
    cloud_session,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import cloud_strings, login_response, server
from .test_native_map_application import _encoded_map_frame


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("scheduled", [False, True])
@pytest.mark.parametrize("rpc_code", [0, 80001])
def test_native_poll_applies_fallback_or_drains_cancelled_request(
    monkeypatch, account, cancel, scheduled, rpc_code
):
    strings = cloud_strings(account)
    properties = AsyncMock(
        return_value=[{"value": "map-object", "updateDate": 1700000000000}]
    )
    monkeypatch.setattr(
        cloud_session.DreameCloudSession, "async_get_properties", properties
    )
    monkeypatch.setattr(
        cloud_session.DreameCloudSession,
        "async_get_interim_file_url",
        AsyncMock(return_value="https://example.invalid/map"),
    )
    monkeypatch.setattr(
        cloud_session.DreameCloudSession,
        "async_get_public_file",
        AsyncMock(return_value=_encoded_map_frame(None, 2).encode()),
    )

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            started.set()
            await release.wait()
            return web.json_response(
                {"code": rpc_code, "data": {"result": {"code": 0, "out": []}}}
            )

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            device = client._ensure_device()
            manager = device._map_manager
            manager._connected = False
            manager._map_request_time = 1000
            manager._map_request_count = 1
            application = client_map_application.NativeMapApplication(
                client,
                device,
                manager,
                deadline=time.monotonic() + 10,
            )
            if scheduled:
                owner = client_map_poll.NativeMapPolling(client, device)
                manager._native_update_request = owner.request
                await asyncio.to_thread(manager.update)
                await asyncio.wait_for(started.wait(), 3)
                task = owner._task
                assert task is not None
                manager.update()
                await asyncio.sleep(0)
                assert owner._task is task
            else:
                task = asyncio.create_task(client_map_poll.async_poll_maps(application))
            try:
                await asyncio.wait_for(started.wait(), 3)
                assert manager._update_running
                if cancel:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    properties.assert_not_awaited()
                    assert manager._map_data is None
                else:
                    release.set()
                    await task
                    properties.assert_awaited_once()
                    assert manager._current_frame_id == 2
                    assert manager._map_data.timestamp_ms == 1700000000000
                assert not manager._update_running
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())
