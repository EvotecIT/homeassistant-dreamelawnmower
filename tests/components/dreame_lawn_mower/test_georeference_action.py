"""Calibration actions run through HA dispatch and never reload the entry."""

from __future__ import annotations

import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import EntityPlatform
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_lawn_mower import _async_update_listener, lawn_mower
from custom_components.dreame_lawn_mower.const import CONF_GEOREFERENCE, DOMAIN
from custom_components.dreame_lawn_mower.georeference import (
    register_georeference_services,
)
from custom_components.dreame_lawn_mower.option_updates import EntryUpdateSnapshot
from tests.test_zone_mowing_history import HASH, _maps

CALIBRATION = {
    "anchor_x": 1160,
    "anchor_y": -20,
    "latitude": 45.317421,
    "longitude": -73.837131,
    "heading": -132.67,
    "map_index": 0,
    "map_revision": HASH,
}


class _Mower(Entity):
    """Real mower action handlers on a minimal HA entity."""

    _attr_should_poll = False
    async_set_georeference = lawn_mower.DreameLawnMower.async_set_georeference
    async_clear_georeference = lawn_mower.DreameLawnMower.async_clear_georeference


async def _setup(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data={"did": "geo"}, options={"map_theme": "emerald"}
    )
    entry.add_to_hass(hass)
    coordinator = SimpleNamespace(
        entry=entry, app_maps=_maps(), async_update_listeners=Mock()
    )
    coordinator.applied_entry_update = EntryUpdateSnapshot.capture(entry)
    entry.runtime_data = coordinator
    entry.add_update_listener(_async_update_listener)
    platform = EntityPlatform(
        hass=hass,
        logger=logging.getLogger(__name__),
        domain="lawn_mower",
        platform_name=DOMAIN,
        platform=lawn_mower,
        scan_interval=timedelta(seconds=30),
        entity_namespace=None,
    )
    mower = _Mower()
    mower.hass = hass
    mower.platform = platform
    mower.entity_id = "lawn_mower.geo"
    mower.coordinator = coordinator
    mower.parallel_updates = platform._get_parallel_updates_semaphore(False)
    platform.domain_platform_entities[mower.entity_id] = mower
    register_georeference_services(platform)
    return entry, coordinator


async def test_set_and_clear_georeference_without_reloading(hass):
    entry, coordinator = await _setup(hass)
    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        await hass.services.async_call(
            DOMAIN,
            "set_georeference",
            {"entity_id": "lawn_mower.geo", **CALIBRATION},
            blocking=True,
        )
        await hass.async_block_till_done()
        stored = entry.options[CONF_GEOREFERENCE]["maps"]["0"]
        assert stored["heading"] == pytest.approx(227.33)
        assert stored["scale"] == 0.001
        assert entry.options["map_theme"] == "emerald"
        coordinator.async_update_listeners.assert_called_once_with()

        await hass.services.async_call(
            DOMAIN,
            "clear_georeference",
            {"entity_id": "lawn_mower.geo"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert CONF_GEOREFERENCE not in entry.options
        assert coordinator.async_update_listeners.call_count == 2
    reload.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"latitude": 91},
        {"longitude": -180.5},
        {"scale": 0},
        {"heading": "east"},
    ],
)
async def test_invalid_georeference_is_rejected_before_storage(hass, change):
    entry, _ = await _setup(hass)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "set_georeference",
            {"entity_id": "lawn_mower.geo", **CALIBRATION, **change},
            blocking=True,
        )
    assert CONF_GEOREFERENCE not in entry.options


@pytest.mark.parametrize("service", ["set_georeference", "clear_georeference"])
async def test_stale_map_revision_is_rejected_by_dispatch(hass, service):
    entry, _ = await _setup(hass)
    fields = CALIBRATION if service == "set_georeference" else {"map_index": 0}
    with pytest.raises(HomeAssistantError, match="map changed"):
        await hass.services.async_call(
            DOMAIN, service,
            {"entity_id": "lawn_mower.geo", **fields, "map_revision": "c" * 32},
            blocking=True,
        )
    assert entry.options == {"map_theme": "emerald"}
