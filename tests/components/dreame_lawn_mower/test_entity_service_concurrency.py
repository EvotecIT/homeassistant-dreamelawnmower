"""HA service dispatch must reach the command and cancellation owners promptly."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import SupportsResponse
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import EntityPlatform

from custom_components.dreame_lawn_mower import button, camera, lawn_mower
from custom_components.dreame_lawn_mower.video_camera import (
    DreameLawnMowerVideoCamera,
)
from tests.test_scheduled_mowing import entity_with_evidence


class _ServiceEntity(Entity):
    """Use real HA dispatch with controllable external operation boundaries."""

    _attr_should_poll = False

    def __init__(self, entered, release):
        self.entered = entered
        self.release = release

    async def async_wait(self):
        self.entered.set()
        await self.release.wait()

    async def async_interrupt(self):
        await self.interrupt(self)

    async_start_scheduled_mowing = (
        lawn_mower.DreameLawnMower.async_start_scheduled_mowing
    )


def _register(hass, module, entity, companion=None):
    domain = module.__name__.rsplit(".", 1)[-1]
    platform = EntityPlatform(
        hass=hass,
        logger=logging.getLogger(__name__),
        domain=domain,
        platform_name="dreame_lawn_mower",
        platform=module,
        scan_interval=timedelta(seconds=30),
        entity_namespace=None,
    )
    for index, item in enumerate([entity] + ([companion] if companion else [])):
        item.hass = hass
        item.platform = platform
        item.entity_id = f"{domain}.service_contract_{index}"
        item.parallel_updates = platform._get_parallel_updates_semaphore(False)
        platform.domain_platform_entities[item.entity_id] = item
    platform.async_register_entity_service("wait", {}, "async_wait")
    platform.async_register_entity_service("interrupt", {}, "async_interrupt")
    platform.async_register_entity_service(
        "start_scheduled_mowing",
        {},
        "async_start_scheduled_mowing",
        supports_response=SupportsResponse.OPTIONAL,
    )


async def _call(hass, entity, service, *, response=False, companion=None):
    return await hass.services.async_call(
        "dreame_lawn_mower",
        service,
        {
            "entity_id": [entity.entity_id, companion.entity_id]
            if companion else entity.entity_id
        },
        blocking=True,
        return_response=response,
    )


@pytest.mark.parametrize("module", [lawn_mower, button, camera])
async def test_interrupt_reaches_owner_during_another_platform_service(hass, module):
    entered, release, interrupted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    entity = _ServiceEntity(entered, release)
    owner = AsyncMock(side_effect=interrupted.set)
    if module is lawn_mower:
        entity.interrupt = lawn_mower.DreameLawnMower.async_pause
        entity.coordinator = SimpleNamespace(
            client=SimpleNamespace(async_pause=owner),
            async_request_refresh=AsyncMock(),
        )
    elif module is button:
        entity.interrupt = button.DreameLawnMowerEndCurrentTaskButton.async_press
        entity.coordinator = SimpleNamespace(async_cancel_current_task=owner)
    else:
        entity.interrupt = DreameLawnMowerVideoCamera.async_turn_off
        entity._attr_is_on = True
        entity._snapshot_request = SimpleNamespace(async_cancel=owner)
        entity._stream_lock = asyncio.Lock()
        entity._async_stop_active_session = AsyncMock()
    # HA 2025.1 bypasses its semaphore for single-entity calls. Multiple targets
    # exercise shared platform serialization on both supported HA versions.
    companion = _ServiceEntity(entered, release)
    companion.interrupt = AsyncMock()
    _register(hass, module, entity, companion)
    first = asyncio.create_task(_call(hass, entity, "wait", companion=companion))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        second = asyncio.create_task(
            _call(hass, entity, "interrupt", companion=companion)
        )
        done, _ = await asyncio.wait([second], timeout=1)
        assert second in done, "HA queued interruption behind the ongoing service"
        await second
        assert interrupted.is_set()
        assert not first.done()
        if module is camera:
            assert entity._attr_is_on is False
            entity._async_stop_active_session.assert_awaited_once_with(reason="turn_off")
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second is not None else []))


async def test_scheduled_start_skips_through_ha_dispatch_without_queueing(hass):
    source, _snapshot, _weather, native = entity_with_evidence()
    entered, release = asyncio.Event(), asyncio.Event()
    entity = _ServiceEntity(entered, release)
    entity.coordinator = source.coordinator
    entity.coordinator.async_update_listeners = Mock()

    async def delayed_evidence():
        entered.set()
        await release.wait()
        return native

    entity.coordinator.client.async_get_schedule_start_evidence.side_effect = (
        delayed_evidence
    )
    _register(hass, lawn_mower, entity)
    first = asyncio.create_task(
        _call(hass, entity, "start_scheduled_mowing", response=True)
    )
    second = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        second = asyncio.create_task(
            _call(hass, entity, "start_scheduled_mowing", response=True)
        )
        done, _ = await asyncio.wait([second], timeout=1)
        assert second in done, "HA queued a concurrent scheduled start"
        result = (await second)[entity.entity_id]
        assert result["status"] == "skipped"
        assert result["reason"] == "scheduled_start_in_progress"
        assert not first.done()
        entity.coordinator.client.async_start_fresh_mowing.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second is not None else []))
    entity.coordinator.client.async_start_fresh_mowing.assert_awaited_once()
