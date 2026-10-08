"""Native legacy-map refresh, arrival, rendering and cancellation contracts."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import numpy as np
import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerConnectionError,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    MapData,
    MapImageDimensions,
    MapPixelType,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_visuals import (
    map_render_style,
)

from .test_async_app_preferences import make_client
from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_operation_reads import ready_device, refresh_response


def test_legacy_view_refreshes_over_async_http_and_keeps_rendering(monkeypatch):
    strings = cloud_strings("dreame")
    rpc = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        rpc.append(await request.json())
        return refresh_response()

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            device = ready_device(monkeypatch, client)
            data = MapData()
            data.map_id = 1
            data.frame_id = 2
            data.empty_map = False
            data.rotation = 0
            data.dimensions = MapImageDimensions(0, 0, 4, 4, 50)
            data.pixel_type = np.full((4, 4), MapPixelType.FLOOR.value)
            data.data = bytes([MapPixelType.FLOOR.value] * 16)
            data.segments = {}
            data.last_updated = 0
            monkeypatch.setattr(
                type(device.status), "current_map", property(lambda _: data)
            )
            monkeypatch.setattr(device, "get_map_for_render", lambda value: value)

            def sync_update(*args, **kwargs):
                pytest.fail("legacy async view reached synchronous device refresh")

            monkeypatch.setattr(client, "_sync_update_device", sync_update)
            style = map_render_style("dark")
            try:
                expected = client._render_legacy_map_view(
                    data, label_scale=2.0, style=style
                )
                actual = await client._async_refresh_legacy_map_view(
                    0, 0.1, label_scale=2.0, style=style
                )
                assert len(rpc) == 1
                assert actual.image_png and actual.image_png == expected.image_png
                assert actual.summary == expected.summary
                assert actual.source == "legacy_current_map"
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["arrival", "timeout", "cancel", "refresh_error"])
def test_legacy_map_wait_and_error_lifetime(monkeypatch, outcome):
    async def scenario():
        async with ClientSession() as session:
            client = make_client(session)
            device = client._device
            client._async_update_device = AsyncMock(return_value=device)
            current = None
            requested = asyncio.Event()
            loop = asyncio.get_running_loop()
            # The map scheduler is the external boundary here; no device requests.
            monkeypatch.setattr(device, "_map_manager", object())
            monkeypatch.setattr(
                device, "update_map", lambda: loop.call_soon_threadsafe(requested.set)
            )
            monkeypatch.setattr(
                type(device.status), "current_map", property(lambda _: current)
            )
            rendered = []
            original_render = client._render_legacy_map_view

            def render(data, **kwargs):
                rendered.append(data)
                # Empty-result rendering preserves real diagnostics without a bitmap.
                return original_render(None, **kwargs)

            monkeypatch.setattr(client, "_render_legacy_map_view", render)
            if outcome == "refresh_error":
                error = DreameLawnMowerConnectionError("refresh offline")
                client._async_update_device.side_effect = error
            task = asyncio.create_task(client._async_refresh_legacy_map_view(
                0.12 if outcome == "timeout" else 30, 0.1
            ))
            try:
                if outcome != "refresh_error":
                    await asyncio.wait_for(requested.wait(), 2)
                if outcome == "arrival":
                    current = MapData()
                elif outcome == "cancel":
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 1)
                    assert not rendered
                    return
                result = await asyncio.wait_for(task, 2)
                if outcome == "refresh_error":
                    assert result.error == "refresh offline"
                    assert not rendered
                else:
                    assert rendered == [current]
                    assert result.error == (
                        "No map data returned by the legacy current-map path."
                    )
                assert result.source == "legacy_current_map"
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                # Restore the real device owner before ordinary disconnect cleanup.
                monkeypatch.undo()
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())
