"""Contracts for retained synchronous device/map transport operations."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from aiohttp import ClientSession

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    MapData,
)

from .test_async_app_preferences import make_client


def test_synchronous_map_fallback_reads_the_device_status_map(monkeypatch):
    """A native device publishes its current map through its status owner."""
    async def scenario():
        async with ClientSession() as session:
            client = make_client(session)
            try:
                device = client._device
                current = MapData()
                current.map_id = 7
                manager = SimpleNamespace(get_map=lambda: current)
                monkeypatch.setattr(device, "_map_manager", manager)
                monkeypatch.setattr(device, "update_map", lambda: None)
                monkeypatch.setattr(client, "_sync_update_device", lambda: device)
                assert client._sync_wait_for_map(timeout=0, interval=0.1) is current
            finally:
                monkeypatch.undo()
                await client.async_close()

    asyncio.run(scenario())
