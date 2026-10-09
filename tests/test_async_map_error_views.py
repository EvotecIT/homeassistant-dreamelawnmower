"""Source failures do not require device-state diagnostics to return."""

from __future__ import annotations

import asyncio
from threading import Event, Thread
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.cloud_session import (
    DreameCloudSession,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerConnectionError,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerMapSummary,
    DreameLawnMowerMapView,
)

from .test_async_map_objects import make_client


@pytest.mark.parametrize("source", ["app", "vector"])
def test_source_error_does_not_require_contended_state(monkeypatch, source):
    async def scenario():
        async with ClientSession() as session:
            client = make_client(session)
            error = DreameLawnMowerConnectionError("map source offline")
            if source == "app":
                client.async_get_app_maps = AsyncMock(side_effect=error)
                ready = DreameLawnMowerMapView(
                    source="batch_vector_map",
                    summary=DreameLawnMowerMapSummary(available=True),
                    image_png=b"ready vector image",
                )
                client.async_refresh_vector_map_view = AsyncMock(return_value=ready)
            else:
                monkeypatch.setattr(
                    DreameCloudSession, "async_get_batch_device_datas",
                    AsyncMock(side_effect=error),
                )
            locked, release = Event(), Event()

            def hold_state():
                with client._device._state_lock:
                    locked.set()
                    release.wait(timeout=5)

            worker = Thread(target=hold_state, name="map-error-state-holder")
            worker.start()
            try:
                assert await asyncio.to_thread(locked.wait, 1)
                operation = (
                    client.async_refresh_map_view(timeout=0, interval=0.1)
                    if source == "app"
                    else client.async_refresh_vector_map_view(current_map_index=0)
                )
                result = await asyncio.wait_for(operation, 0.5)
                if source == "app":
                    assert result.source == ready.source
                    assert result.image_png == ready.image_png
                    client.async_refresh_vector_map_view.assert_awaited_once()
                else:
                    assert result.source == "batch_vector_map"
                    assert result.error == str(error)
                    assert result.diagnostics is None
            finally:
                release.set()
                await asyncio.to_thread(worker.join, 2)
                assert not worker.is_alive()
                await client.async_close()

    asyncio.run(scenario())
