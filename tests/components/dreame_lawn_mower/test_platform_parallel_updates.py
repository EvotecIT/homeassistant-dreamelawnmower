"""Exercise the action limits through Home Assistant's scheduling owners."""

from __future__ import annotations

import asyncio
from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.dreame_lawn_mower import time as time_platform
from custom_components.dreame_lawn_mower import update
from tests.components.dreame_lawn_mower.test_entity_service_concurrency import (
    _register,
)


def _coordinator(**kwargs):
    """Supply unavailable device boundaries while using real platform entities."""
    return SimpleNamespace(
        client=SimpleNamespace(descriptor=SimpleNamespace(unique_id="synthetic")),
        data=SimpleNamespace(available=True),
        last_update_success=True,
        **kwargs,
    )


async def test_charging_time_batch_serializes_settings_actions(hass):
    """HA must finish one charging-window write before starting its companion."""
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def set_charging_period(**kwargs):
        calls.append(kwargs)
        entered.set()
        await release.wait()

    coordinator = _coordinator(
        device_settings={"available": True, "charging_settings_available": True},
        async_set_charging_period=set_charging_period,
    )
    start = time_platform.DreameLawnMowerChargingPeriodStartTime(coordinator)
    end = time_platform.DreameLawnMowerChargingPeriodEndTime(coordinator)
    _register(hass, time_platform, start, end)
    start.platform.async_register_entity_service(
        "apply_charging_time", {"value": time}, "async_set_value"
    )
    operation = asyncio.create_task(
        hass.services.async_call(
            "dreame_lawn_mower",
            "apply_charging_time",
            {"entity_id": [start.entity_id, end.entity_id], "value": time(8, 30)},
            blocking=True,
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        # Let the companion dispatch run without completing the first device call.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(calls) == 1
        assert not operation.done()
    finally:
        release.set()
        await operation
    assert len(calls) == 2
    assert {tuple(call.items()) for call in calls} == {
        (("start_minutes", 510),),
        (("end_minutes", 510),),
    }


async def test_firmware_actions_use_ha_platform_request_limit(hass):
    """HA's request owner serializes approval plus its metadata refreshes."""
    entered, release = asyncio.Event(), asyncio.Event()
    approvals = []

    async def approve(**kwargs):
        approvals.append(kwargs)
        entered.set()
        await release.wait()
        return {"success": True}

    coordinator = _coordinator(
        firmware_update_support=SimpleNamespace(latest_version="synthetic-2"),
        async_refresh_firmware_update_support=AsyncMock(),
        async_refresh_batch_device_data=AsyncMock(),
        async_request_refresh=AsyncMock(),
    )
    coordinator.client.async_approve_firmware_update = approve
    entity = update.DreameLawnMowerFirmwareUpdateEntity(coordinator)
    _register(hass, update, entity)
    first = asyncio.create_task(
        entity.async_request_call(entity.async_install(None, False))
    )
    second = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        second = asyncio.create_task(
            entity.async_request_call(entity.async_install(None, False))
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert approvals == [{"language": "en"}]
        assert not second.done()
        coordinator.async_refresh_firmware_update_support.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second is not None else []))
    assert approvals == [{"language": "en"}, {"language": "en"}]
    assert coordinator.async_refresh_firmware_update_support.await_count == 2
    assert coordinator.async_refresh_batch_device_data.await_count == 2
    assert coordinator.async_request_refresh.await_count == 2
