"""Native diagnostic composition and optional legacy-worker ownership."""

from __future__ import annotations

import asyncio
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device as device_module,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import device_types
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerMapView,
)

from .test_async_app_preferences import make_client
from .test_async_cloud_session import cloud_strings, login_response, server


def ready_device(monkeypatch, client):
    monkeypatch.setattr(
        device_module.DreameMowerDevice, "cloud_connected", property(lambda _: True),
    )
    monkeypatch.setattr(
        device_module.DreameMowerDevice, "device_connected", property(lambda _: False),
    )
    device = client._device
    device._ready = True
    device.data = {device_types.DreameMowerProperty.BATTERY_LEVEL.value: 20}
    device._last_settings_request = 10**20
    return device


def refresh_response():
    return web.json_response({"code": 0, "data": {"result": [
        {"did": str(device_types.DreameMowerProperty.BATTERY_LEVEL.value),
         "code": 0, "value": 55},
    ]}})


@pytest.mark.parametrize("sections", [False, True])
def test_operation_snapshot_refreshes_once_and_retains_partial_sections(
    monkeypatch, sections,
):
    strings = cloud_strings("dreame")
    rpc = []
    property_reads = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        if request.path == "/dreame-user-iot/iotstatus/props":
            property_reads.append(await request.json())
            return web.Response(status=400, text="status unavailable")
        rpc.append(await request.json())
        assert rpc[-1]["data"]["method"] == "get_properties"
        return refresh_response()

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            ready_device(monkeypatch, client)
            firmware = {"available": True}
            client.async_get_firmware_update_support = AsyncMock(
                return_value=SimpleNamespace(as_dict=lambda: firmware),
            )
            try:
                result = await client.async_capture_operation_snapshot(
                    label="diagnostic", include_status_blob=sections,
                    include_remote_control=sections, include_firmware=sections,
                )
                assert result["label"] == "diagnostic"
                assert result["snapshot"]["battery_level"] == 55
                assert result["captured_at"]
                assert len(rpc) == 1
                if sections:
                    assert result["status_blob"] is None
                    assert result["errors"][0]["section"] == "status_blob"
                    assert result["remote_control_support"] is not None
                    assert result["firmware_update"] == firmware
                    client.async_get_firmware_update_support.assert_awaited_once_with(
                        refresh=False, include_cloud=True,
                        include_debug_ota_catalog=True, language="en",
                    )
                    assert len(property_reads) == 1
                else:
                    assert result["errors"] == []
                    assert "status_blob" not in result
                    assert "remote_control_support" not in result
                    assert "firmware_update" not in result
                    assert not property_reads
                    client.async_get_firmware_update_support.assert_not_awaited()
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


def test_operation_snapshot_cancellation_drains_map_and_stops_later_sections(
    monkeypatch,
):
    strings = cloud_strings("dreame")
    started = Event()
    release = Event()
    finished = Event()

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return refresh_response()

    def read_map(map_data, **kwargs):
        started.set()
        assert release.wait(5)
        finished.set()
        return DreameLawnMowerMapView(source="legacy_current_map")

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = make_client(session)
            device = ready_device(monkeypatch, client)
            client._render_legacy_map_view = read_map
            client.async_get_app_maps = AsyncMock(return_value={"maps": []})
            client.async_refresh_vector_map_view = AsyncMock(
                return_value=DreameLawnMowerMapView(source="batch_vector_map"),
            )
            client.async_get_firmware_update_support = AsyncMock()
            task = asyncio.create_task(client.async_capture_operation_snapshot(
                include_status_blob=False, include_remote_control=False,
                include_map_view=True, include_firmware=True,
                map_timeout=2, map_interval=0.1,
            ))
            close_task = None
            try:
                assert await asyncio.to_thread(started.wait, 5)
                close_task = asyncio.create_task(client.async_close())
                await asyncio.sleep(0.05)
                assert not close_task.done()
                assert client._device is device
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
                await close_task
                assert finished.is_set()
                client.async_get_firmware_update_support.assert_not_awaited()
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                if close_task is not None:
                    await close_task
                await client.async_close()
            assert client._device is None
            assert not session.closed

    asyncio.run(scenario())
