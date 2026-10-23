"""Import and execute the shipped automation examples in Home Assistant."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
import yaml
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util.yaml.loader import load_yaml
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.dreame_lawn_mower.mowing_permission import permission_block
from tests.test_scheduled_mowing import substituted_blueprint

EXAMPLES = Path(__file__).parents[3] / "examples" / "automations"


@pytest.fixture(autouse=True)
async def close_example_entities(hass):
    """Remove YAML entities so their owned time/template listeners are drained."""
    yield
    for domain in ("automation", "binary_sensor", "input_datetime", "input_boolean"):
        component = hass.data.get(DATA_INSTANCES, {}).get(domain)
        if component:
            for entity in list(component.entities):
                await component.async_remove_entity(entity.entity_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_soil_example_imports_and_blocks_wet_missing_and_drying_readings(
    hass, freezer
):
    config = load_yaml(EXAMPLES / "soil_moisture_permission.yaml")
    docs = (EXAMPLES.parents[1] / "docs" / "automation-permission.md").read_text()
    combined = next(
        yaml.safe_load(block)
        for block in re.findall(r"```yaml\n(.*?)```", docs, re.DOTALL)
        if "unique_id: garden_mowing_permitted" in block
    )
    config["template"].extend(combined["template"])
    hass.states.async_set("binary_sensor.irrigation_mowing_permitted", "on")
    hass.states.async_set("sensor.garden_soil_moisture", "70")
    assert await async_setup_component(hass, "binary_sensor", config)
    assert await async_setup_component(hass, "template", config)
    await hass.async_start()
    await hass.async_block_till_done()
    assert hass.states.is_state("binary_sensor.lawn_soil_too_wet", "on")
    assert hass.states.is_state("binary_sensor.soil_mowing_permitted", "off")
    hass.states.async_set("sensor.garden_soil_moisture", "50")
    await hass.async_block_till_done()
    assert hass.states.is_state("binary_sensor.lawn_soil_too_wet", "off")
    assert hass.states.is_state("binary_sensor.soil_mowing_permitted", "off")
    refreshed = dt_util.utcnow() + timedelta(minutes=29)
    freezer.move_to(refreshed)
    hass.states.async_set("sensor.garden_soil_moisture", "50")
    async_fire_time_changed(hass, refreshed)
    await hass.async_block_till_done()
    later = refreshed + timedelta(minutes=2)
    freezer.move_to(later)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done()
    assert hass.states.is_state("binary_sensor.soil_mowing_permitted", "on")
    # Unchanged healthy input must not age the permission into a false block.
    for _ in range(4):
        later += timedelta(minutes=10)
        freezer.move_to(later)
        hass.states.async_set("sensor.garden_soil_moisture", "50")
        async_fire_time_changed(hass, later)
        await hass.async_block_till_done()
    assert permission_block(hass, "binary_sensor.soil_mowing_permitted", 1800) is None
    assert permission_block(hass, "binary_sensor.garden_mowing_permitted", 1800) is None
    later += timedelta(minutes=31)
    freezer.move_to(later)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done()
    assert hass.states.is_state("binary_sensor.soil_mowing_permitted", "off")
    assert hass.states.is_state("binary_sensor.garden_mowing_permitted", "off")
    assert "stale" in hass.states.get(
        "binary_sensor.soil_mowing_permitted"
    ).attributes["reason"]
    hass.states.async_set("sensor.garden_soil_moisture", "unavailable")
    await hass.async_block_till_done()
    assert hass.states.is_state("binary_sensor.soil_mowing_permitted", "off")
    assert (
        "unavailable"
        in hass.states.get("binary_sensor.soil_mowing_permitted").attributes["reason"]
    )


@pytest.mark.asyncio
async def test_irrigation_example_initializes_persistent_settling_and_blocks_watering(
    hass,
    freezer,
):
    config = load_yaml(EXAMPLES / "irrigation_permission.yaml")
    hass.states.async_set("switch.garden_irrigation", "off")
    assert await async_setup_component(hass, "input_datetime", config)
    assert await async_setup_component(hass, "input_boolean", config)
    assert await async_setup_component(hass, "automation", config)
    assert await async_setup_component(hass, "template", config)
    await hass.async_start()
    await hass.async_block_till_done()
    deadline = hass.states.get("input_datetime.mower_irrigation_clear_after")
    assert deadline is not None
    assert deadline.attributes["timestamp"] > dt_util.utcnow().timestamp() + 800
    assert hass.states.is_state("binary_sensor.irrigation_mowing_permitted", "off")
    later = dt_util.utcnow() + timedelta(minutes=16)
    freezer.move_to(later)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done()
    assert hass.states.is_state("binary_sensor.irrigation_mowing_permitted", "on")
    later += timedelta(minutes=31)
    freezer.move_to(later)
    async_fire_time_changed(hass, later)
    await hass.async_block_till_done()
    assert permission_block(
        hass, "binary_sensor.irrigation_mowing_permitted", 1800
    ) is None
    hass.states.async_set("switch.garden_irrigation", "on")
    await hass.async_block_till_done()
    assert hass.states.is_state("binary_sensor.irrigation_mowing_permitted", "off")
    hass.states.async_set("switch.garden_irrigation", "unknown")
    await hass.async_block_till_done()
    assert hass.states.is_state("binary_sensor.irrigation_mowing_permitted", "off")


@pytest.mark.asyncio
async def test_managed_outcome_trigger_notifies_without_starting_another_task(hass):
    config = substituted_blueprint()
    # Exercise the event branch on all maintained HA versions. Schedule block
    # triggers have their separate native runtime test on HA 2026.7 and newer.
    config["triggers"] = config["triggers"][1:]
    starts, notices = [], []

    async def start(call):
        starts.append(call.data)

    async def notify(call):
        notices.append(call.data)

    hass.services.async_register("dreame_lawn_mower", "start_scheduled_mowing", start)
    hass.services.async_register("persistent_notification", "create", notify)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "id": "managed_outcome_test",
                    "alias": "Managed outcome test",
                    **config,
                }
            ],
        },
    )
    await hass.async_start()
    await hass.async_block_till_done()
    hass.bus.async_fire(
        "dreame_lawn_mower_scheduled_run",
        {
            "entity_id": "lawn_mower.garden",
            "automation_entity": "automation.other",
            "status": "deferred",
            "reason": "external_permission_blocked",
        },
    )
    await hass.async_block_till_done()
    assert notices == []
    hass.bus.async_fire(
        "dreame_lawn_mower_scheduled_run",
        {
            "entity_id": "lawn_mower.garden",
            "automation_entity": "automation.managed_outcome_test",
            "status": "deferred",
            "reason": "external_permission_blocked",
            "detail": "Soil too wet",
            "run_id": "morning",
        },
    )
    await hass.async_block_till_done()
    assert starts == []
    assert len(notices) == 1
    assert notices[0]["title"] == "Mower schedule: deferred"
    assert "Soil too wet" in notices[0]["message"]
