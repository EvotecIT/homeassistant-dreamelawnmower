"""Cached mower state is published through real HA entities and listeners."""

import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.dreame_lawn_mower.binary_sensor import (
    BINARY_SENSORS,
    DreameLawnMowerBinarySensor,
    DreameLawnMowerMaintenanceDueBinarySensor,
    DreameLawnMowerMaintenanceWarningBinarySensor,
    DreameLawnMowerRainDelayActiveBinarySensor,
    DreameLawnMowerRainProtectionEnabledBinarySensor,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerSnapshot,
    descriptor_from_cloud_record,
)


def _coordinator(hass):
    descriptor = descriptor_from_cloud_record(
        {"did": "state-contract", "model": "mova.mower.g2529b", "name": "Mower"},
        account_type="mova",
        country="eu",
    )
    coordinator = DataUpdateCoordinator(
        hass, logging.getLogger(__name__), name="mower-state", config_entry=None
    )
    coordinator.client = SimpleNamespace(descriptor=descriptor)
    coordinator.weather_protection = None
    coordinator.maintenance_status = None
    coordinator.async_set_updated_data(
        DreameLawnMowerSnapshot(
            descriptor=descriptor, available=True, online=True,
            state="docked", state_name="docked", activity="docked",
        )
    )
    return coordinator


@pytest.mark.parametrize(
    ("entity_type", "cache", "flag"),
    [
        (DreameLawnMowerRainProtectionEnabledBinarySensor,
         "weather_protection", "rain_protection_enabled"),
        (DreameLawnMowerRainDelayActiveBinarySensor,
         "weather_protection", "rain_protection_active"),
        (DreameLawnMowerMaintenanceDueBinarySensor, "maintenance_status", "due"),
        (DreameLawnMowerMaintenanceWarningBinarySensor,
         "maintenance_status", "warning"),
    ],
)
async def test_cached_flags_distinguish_unknown_false_offline_and_recovery(
    hass, entity_type, cache, flag,
):
    coordinator = _coordinator(hass)
    entity = entity_type(coordinator)
    component = EntityComponent(logging.getLogger(__name__), "binary_sensor", hass)
    await component.async_add_entities([entity])
    try:
        assert hass.states.get(entity.entity_id).state == STATE_UNAVAILABLE
        for value, expected in [
            ("false", STATE_UNAVAILABLE), (0, STATE_UNAVAILABLE),
            (False, STATE_OFF), (True, STATE_ON),
        ]:
            setattr(coordinator, cache, {flag: value})
            coordinator.async_set_updated_data(replace(coordinator.data))
            await hass.async_block_till_done()
            assert hass.states.get(entity.entity_id).state == expected

        # A stale true flag must not claim an active condition while offline.
        coordinator.async_set_updated_data(
            replace(coordinator.data, available=False)
        )
        await hass.async_block_till_done()
        assert hass.states.get(entity.entity_id).state == STATE_UNAVAILABLE
        coordinator.async_set_updated_data(
            replace(coordinator.data, available=True)
        )
        await hass.async_block_till_done()
        assert hass.states.get(entity.entity_id).state == STATE_ON
    finally:
        await component.async_remove_entity(entity.entity_id)
    unloaded = hass.states.get(entity.entity_id)
    assert unloaded.state == STATE_UNAVAILABLE
    assert unloaded.attributes["restored"] is True
    # HA retains a registry placeholder; cached updates must not resurrect it.
    coordinator.async_set_updated_data(replace(coordinator.data))
    await hass.async_block_till_done()
    assert hass.states.get(entity.entity_id) is unloaded


async def test_connectivity_stays_visible_while_operational_state_is_offline(hass):
    coordinator = _coordinator(hass)
    descriptions = {description.key: description for description in BINARY_SENSORS}
    online = DreameLawnMowerBinarySensor(coordinator, descriptions["online"])
    mowing = DreameLawnMowerBinarySensor(coordinator, descriptions["mowing"])
    component = EntityComponent(logging.getLogger(__name__), "binary_sensor", hass)
    await component.async_add_entities([online, mowing])
    try:
        assert hass.states.get(online.entity_id).state == STATE_ON
        assert hass.states.get(mowing.entity_id).state == STATE_OFF
        coordinator.async_set_updated_data(replace(
            coordinator.data, available=False, online=False, activity="mowing",
        ))
        await hass.async_block_till_done()
        assert hass.states.get(online.entity_id).state == STATE_OFF
        assert hass.states.get(mowing.entity_id).state == STATE_UNAVAILABLE
        coordinator.async_set_updated_data(replace(
            coordinator.data, available=True, online=True, activity="mowing",
        ))
        await hass.async_block_till_done()
        assert hass.states.get(online.entity_id).state == STATE_ON
        assert hass.states.get(mowing.entity_id).state == STATE_ON
    finally:
        await component.async_remove_entity(online.entity_id)
        await component.async_remove_entity(mowing.entity_id)
