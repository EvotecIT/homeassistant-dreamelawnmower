"""Resolve mower names with real HA translation, device and entity registries."""

import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_lawn_mower import sensor
from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerSnapshot,
    descriptor_from_cloud_record,
)


def _coordinator(hass):
    descriptor = descriptor_from_cloud_record(
        {"did": "name-contract", "model": "mova.mower.g2529b", "name": "Mower"},
        account_type="mova", country="eu",
    )
    coordinator = DataUpdateCoordinator(
        hass, logging.getLogger(__name__), name="names", config_entry=None,
    )
    coordinator.client = SimpleNamespace(descriptor=descriptor)
    coordinator.async_set_updated_data(DreameLawnMowerSnapshot(
        descriptor=descriptor, available=True, online=True,
        state="mowing", state_name="mowing", activity="mowing",
    ))
    return coordinator


async def _add(hass, entry, coordinator, *keys):
    platform = EntityPlatform(
        hass=hass, logger=logging.getLogger(__name__), domain="sensor",
        platform_name=DOMAIN, platform=sensor,
        scan_interval=timedelta(seconds=30), entity_namespace=None,
    )
    entities = [
        sensor.DreameLawnMowerSensor(coordinator, description)
        for description in sensor.SENSORS if description.key in keys
    ]

    async def setup_selected_entities(hass, entry, async_add_entities):
        # Limit fixture state to this contract while HA owns translation and add.
        async_add_entities(entities)

    with patch.object(sensor, "async_setup_entry", setup_selected_entities):
        assert await platform.async_setup_entry(entry)
    await hass.async_block_till_done()
    return platform, entities


@pytest.mark.parametrize(("language", "name"), [
    ("en", "State"), ("cs", "Stav"), ("de", "Status"),
    ("es", "Estado"), ("fr", "État"), ("it", "Stato"),
    ("pl", "Stan"), ("ru", "Состояние"), ("uk", "Стан"),
    ("ja", "State"),  # Unshipped integration locale uses HA's English fallback.
])
async def test_state_sensor_uses_shipped_name_translation(hass, language, name):
    hass.config.language = language
    assert await async_setup_component(hass, "sensor", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id="name-contract")
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass)
    platform, (entity,) = await _add(hass, entry, coordinator, "state_name")
    try:
        registered = er.async_get(hass).async_get(entity.entity_id)
        assert registered.original_name == name
        assert registered.translation_key == "state_name"
        assert registered.has_entity_name
        assert registered.unique_id == (
            f"{coordinator.data.descriptor.unique_id}_state_name"
        )
        device = dr.async_get(hass).async_get(registered.device_id)
        assert device.name == "Mower"
        assert hass.states.get(entity.entity_id).attributes["friendly_name"] == (
            f"Mower {name}"
        )
    finally:
        await platform.async_reset()


async def test_untranslated_sensor_keeps_description_fallback(hass):
    hass.config.language = "de"
    assert await async_setup_component(hass, "sensor", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id="name-contract")
    entry.add_to_hass(hass)
    platform, (entity,) = await _add(hass, entry, _coordinator(hass), "activity")
    try:
        assert er.async_get(hass).async_get(entity.entity_id).original_name == (
            "Activity"
        )
        assert hass.states.get(entity.entity_id).attributes["friendly_name"] == (
            "Mower Activity"
        )
    finally:
        await platform.async_reset()


@pytest.mark.parametrize(("user_name", "friendly_name"), [
    (None, "Back garden Stan"), ("Garden progress", "Garden progress"),
])
async def test_existing_ids_and_user_names_survive_platform_reload(
    hass, user_name, friendly_name,
):
    hass.config.language = "pl"
    assert await async_setup_component(hass, "sensor", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id="name-contract")
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass)
    registry = er.async_get(hass)
    unique_id = f"{coordinator.data.descriptor.unique_id}_state_name"
    old = registry.async_get_or_create(
        "sensor", DOMAIN, unique_id, config_entry=entry,
        suggested_object_id="mower_state_name", original_name="State Name",
        has_entity_name=True,
    )
    registry.async_update_entity(old.entity_id, name=user_name)
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.data.descriptor.unique_id)},
        name="Mower",
    )
    device_registry.async_update_device(device.id, name_by_user="Back garden")
    for _ in range(2):
        platform, (entity,) = await _add(hass, entry, coordinator, "state_name")
        try:
            assert entity.entity_id == old.entity_id
            registered = registry.async_get(old.entity_id)
            assert registered.unique_id == unique_id
            assert registered.original_name == "Stan"
            assert registered.name == user_name
            assert device_registry.async_get(registered.device_id).name_by_user == (
                "Back garden"
            )
            assert hass.states.get(old.entity_id).attributes["friendly_name"] == (
                friendly_name
            )
        finally:
            await platform.async_reset()
