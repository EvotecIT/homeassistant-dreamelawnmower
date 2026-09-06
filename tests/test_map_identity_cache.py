"""Prevent old map downloads and saved preferences crossing map identities."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from custom_components.dreame_lawn_mower.control_options import current_zone_entries
from custom_components.dreame_lawn_mower.map_cache import DreameLawnMowerMapCameraCache
from dreame_lawn_mower_client.models import DreameLawnMowerMapView
from tests.test_current_map_controls import _app_maps, _batch_device_data


@pytest.mark.parametrize("zones", [[], [{"zone_id": 1, "name": "Front"}]])
def test_geometry_membership_does_not_resurrect_deleted_preference_zones(zones) -> None:
    entries = current_zone_entries(
        _batch_device_data(),
        _app_maps(),
        {"map_index": 0, "zones": zones},
    )
    assert [entry["area_id"] for entry in entries] == [
        zone["zone_id"] for zone in zones
    ]
    if entries:
        assert entries[0]["preference"]["mowing_height_cm"] == 4.0


def test_map_change_discards_inflight_old_view_and_image() -> None:
    async def run():
        cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
        cache.store_image(b"old-map-jpeg", source_image=b"old-map-png")
        started, finish = asyncio.Event(), asyncio.Event()

        async def refresh():
            started.set()
            await finish.wait()
            return DreameLawnMowerMapView(source="app_action_map", image_png=b"old")

        pending = asyncio.create_task(cache.async_get_view(refresh))
        await started.wait()
        cache.invalidate_view(drop_image=True)
        finish.set()
        await pending
        assert cache.last_view is None
        assert cache.last_image is None
        assert cache.is_fresh() is False

    asyncio.run(run())
