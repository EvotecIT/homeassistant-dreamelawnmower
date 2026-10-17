"""Derived coordinates and stationary write suppression through real HA setup."""

import logging
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_lawn_mower import device_tracker
from custom_components.dreame_lawn_mower.const import CONF_GEOREFERENCE, DOMAIN
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.position_tracking import (  # noqa: E501
    MowerPosition,
)
from tests.components.dreame_lawn_mower.test_binary_sensor_state import _coordinator
from tests.test_georeference import REFERENCE, _stored
from tests.test_zone_mowing_history import _maps


async def test_tracker_platform_publishes_updates_and_releases_its_listener(hass):
    assert await async_setup_component(hass, "device_tracker", {})
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_GEOREFERENCE: _stored()})
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass)
    coordinator.entry = entry
    coordinator.app_maps = _maps()
    reader = coordinator.client.current_map_position = Mock(
        return_value=MowerPosition(1160, -20, None, 1000, 0, "known_dock")
    )
    entry.runtime_data = coordinator
    platform = EntityPlatform(
        hass=hass,
        logger=logging.getLogger(__name__),
        domain="device_tracker",
        platform_name=DOMAIN,
        platform=device_tracker,
        scan_interval=timedelta(seconds=30),
        entity_namespace=None,
    )
    assert await platform.async_setup_entry(entry)
    await hass.async_block_till_done()
    (entity,) = platform.entities.values()
    try:
        initial = hass.states.get(entity.entity_id)
        assert initial.attributes["latitude"] == REFERENCE["latitude"]
        assert initial.attributes["longitude"] == REFERENCE["longitude"]
        assert initial.attributes["position_status"] == "known_dock"
        # New dock assertions at the same place must not create recorder churn.
        for observed in (1030, 1060, 1090):
            reader.return_value = MowerPosition(
                1160, -20, None, observed, 0, "known_dock"
            )
            coordinator.async_set_updated_data(replace(coordinator.data))
            await hass.async_block_till_done()
        assert hass.states.get(entity.entity_id).last_reported == initial.last_reported
        reader.return_value = MowerPosition(1160, -20, None, 1090, 0, "last_known")
        coordinator.async_set_updated_data(replace(coordinator.data))
        await hass.async_block_till_done()
        historical = hass.states.get(entity.entity_id)
        assert historical.state == STATE_UNKNOWN
        assert "latitude" not in historical.attributes
        coordinator.async_set_updated_data(replace(coordinator.data, available=False))
        await hass.async_block_till_done()
        assert hass.states.get(entity.entity_id).state == STATE_UNAVAILABLE
    finally:
        await platform.async_reset()
    removed = hass.states.get(entity.entity_id)
    coordinator.async_set_updated_data(replace(coordinator.data, available=True))
    await hass.async_block_till_done()
    assert hass.states.get(entity.entity_id) is removed
