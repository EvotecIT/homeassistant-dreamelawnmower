"""Execute the supported Schedule trigger, including adjacent mowing blocks."""

from __future__ import annotations

import pytest
from homeassistant.core import SupportsResponse
from homeassistant.setup import async_setup_component

from tests.test_scheduled_mowing import substituted_blueprint

pytest.importorskip("homeassistant.components.schedule.trigger")


@pytest.mark.asyncio
async def test_schedule_blocks_start_once_without_startup_or_end_catchup(hass):
    calls = []

    async def start(call):
        calls.append(dict(call.data))
        return {
            "lawn_mower.garden": {"status": "skipped", "reason": "rain_delay_active"}
        }

    async def notify(call):
        return None

    hass.services.async_register(
        "dreame_lawn_mower",
        "start_scheduled_mowing",
        start,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register("persistent_notification", "create", notify)
    # An active helper discovered at startup must not replay the block.
    hass.states.async_set(
        "schedule.garden", "on", {"next_event": "2026-10-03T10:00:00Z"}
    )
    config = substituted_blueprint()
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {"id": "guarded_mowing_test", "alias": "Guarded mowing", **config}
            ],
        },
    )
    await hass.async_start()
    await hass.async_block_till_done()
    assert calls == []

    hass.states.async_set(
        "schedule.garden", "off", {"next_event": "2026-10-03T11:00:00Z"}
    )
    await hass.async_block_till_done()
    assert calls == []
    hass.states.async_set(
        "schedule.garden", "on", {"next_event": "2026-10-03T12:00:00Z"}
    )
    await hass.async_block_till_done()
    assert len(calls) == 1
    # Touching blocks stay on; the helper advances its next event instead.
    hass.states.async_set(
        "schedule.garden", "on", {"next_event": "2026-10-03T13:00:00Z"}
    )
    await hass.async_block_till_done()
    assert len(calls) == 2
    hass.states.async_set(
        "schedule.garden",
        "on",
        {"next_event": "2026-10-03T13:00:00Z", "friendly_name": "Renamed helper"},
    )
    await hass.async_block_till_done()
    assert len(calls) == 2
    hass.states.async_set(
        "schedule.garden", "off", {"next_event": "2026-10-04T12:00:00Z"}
    )
    await hass.async_block_till_done()
    assert len(calls) == 2
    assert all(call["task_type"] == "all" for call in calls)
