"""Execute the visual setup and its adapter through real HA state/trigger APIs."""

from __future__ import annotations

import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dreame_lawn_mower.config_flow import DreameLawnMowerOptionsFlow
from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.managed_mowing import ManagedMowing
from custom_components.dreame_lawn_mower.mowing_automation import MowingAutomation
from custom_components.dreame_lawn_mower.mowing_automation_config import (
    AUTOMATION_SCHEMA,
    CONF_MOWING_AUTOMATION,
    automation_config,
)
from custom_components.dreame_lawn_mower.mowing_automation_entities import (
    MowingAutomationSwitch,
    MowingPermissionSensor,
)
from custom_components.dreame_lawn_mower.mowing_conditions import MowingConditions
from tests.components.dreame_lawn_mower.test_binary_sensor_state import _coordinator
from tests.components.dreame_lawn_mower.test_managed_mowing import MowerTransport


def config(**kwargs):
    return AUTOMATION_SCHEMA({"schedule_entity": "schedule.garden", **kwargs})


requires_ui = pytest.mark.skipif(
    tuple(int(part) for part in HA_VERSION.split(".")[:2]) < (2026, 7),
    reason="Visual scheduling uses the native Schedule block trigger in HA2026.7+",
)


@requires_ui
async def test_visual_wizard_saves_without_yaml_and_keeps_other_options(hass):
    hass.config.components.add("stream")
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={"scan_interval": 45})
    entry.add_to_hass(hass)
    initial = await hass.config_entries.options.async_init(entry.entry_id)
    assert initial["type"] is FlowResultType.MENU
    assert initial["menu_options"] == ["settings", "mowing_automation"]
    result = await hass.config_entries.options.async_configure(
        initial["flow_id"],
        {"next_step_id": "mowing_automation"},
    )
    assert result["step_id"] == "mowing_automation"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"enabled": False, "schedule_entity": "schedule.garden"},
    )
    assert result["step_id"] == "mowing_conditions"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "soil_entity": "sensor.soil",
            "wet_threshold": 65,
            "dry_threshold": 55,
            "drying_minutes": 30,
            "source_max_age": 1800,
            "settling_minutes": 15,
        },
    )
    assert result["step_id"] == "mowing_behavior"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "minimum_battery": 40,
            "defer_minutes": 0,
            "recovery_minutes": 0,
            "interruption": "none",
            "resume_when_allowed": False,
            "notify_outcome": True,
        },
    )
    assert result["step_id"] == "mowing_review"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"native_plans_disabled": False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["scan_interval"] == 45
    saved = entry.options[CONF_MOWING_AUTOMATION]
    assert saved["soil_entity"] == "sensor.soil"
    assert saved["enabled"] is False
    assert saved["defer_minutes"] == saved["recovery_minutes"] == 0
    assert saved["interruption"] == "none"


@requires_ui
async def test_wizard_rejects_invalid_thresholds_recovery_and_unconfirmed_enable(
    hass,
):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    flow = DreameLawnMowerOptionsFlow(entry)
    flow.hass = hass
    await flow.async_step_mowing_automation(
        {"enabled": True, "schedule_entity": "schedule.garden"}
    )
    result = await flow.async_step_mowing_conditions(
        {"dry_threshold": 70, "wet_threshold": 65}
    )
    assert result["errors"] == {"dry_threshold": "dry_threshold_below_wet"}
    await flow.async_step_mowing_conditions({"dry_threshold": 55, "wet_threshold": 65})
    result = await flow.async_step_mowing_behavior(
        {"resume_when_allowed": True, "interruption": "none"}
    )
    assert result["errors"]["base"] == "recovery_requires_pause_and_deadline"
    await flow.async_step_mowing_behavior({"resume_when_allowed": False})
    result = await flow.async_step_mowing_review({"native_plans_disabled": False})
    assert result["errors"]["base"] == "native_plans_confirmation_required"
    assert CONF_MOWING_AUTOMATION not in entry.options


async def test_settings_save_preserves_latest_wizard_configuration(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={CONF_MOWING_AUTOMATION: config()}
    )
    entry.add_to_hass(hass)
    flow = DreameLawnMowerOptionsFlow(entry)
    flow.hass = hass
    # Another browser changes the enable state while the settings form is open.
    newer = config(enabled=True, native_plans_disabled=True)
    hass.config_entries.async_update_entry(
        entry, options={CONF_MOWING_AUTOMATION: newer}
    )
    result = await flow.async_step_settings({"scan_interval": 45})
    assert result["data"][CONF_MOWING_AUTOMATION] == newer


@pytest.mark.parametrize(
    "value",
    [
        None,
        {"dry_threshold": 65, "wet_threshold": 65},
        {"enabled": True},
        {"interruption": "pause", "resume_when_allowed": True},
        {"irrigation_entity": "sensor.soil"},
        {"source_max_age": 0},
    ],
)
def test_invalid_persisted_configuration_never_enables_mowing(value):
    assert automation_config(value) == {}


async def test_soil_hysteresis_drying_missing_and_stale_reports(hass):
    rules = MowingConditions(config(soil_entity="sensor.soil", source_max_age=7200))
    start = dt_util.utcnow()
    hass.states.async_set("sensor.soil", "70")
    assert rules.evaluate(hass, start)[0] is False
    hass.states.async_set("sensor.soil", "60")
    assert rules.evaluate(hass, start)[0] is False
    hass.states.async_set("sensor.soil", "54")
    start = dt_util.utcnow()
    assert rules.evaluate(hass, start)[0] is False
    assert rules.evaluate(hass, start + timedelta(minutes=29, seconds=59))[0] is False
    assert rules.evaluate(hass, start + timedelta(minutes=30))[0] is True
    # Crossing the dry threshold upward does not retrigger wetness until >65.
    hass.states.async_set("sensor.soil", "60")
    assert rules.evaluate(hass, dt_util.utcnow() + timedelta(minutes=30))[0] is True
    hass.states.async_set("sensor.soil", "unavailable")
    assert "unavailable" in rules.evaluate(hass, dt_util.utcnow())[1]
    hass.states.async_set("sensor.soil", "54")
    now = dt_util.utcnow()
    assert rules.evaluate(hass, now)[0] is False
    assert "stale" in rules.evaluate(hass, now + timedelta(seconds=7201))[1]
    assert rules._dry_since is None
    assert (
        MowingConditions(config(soil_entity="sensor.soil")).evaluate(hass, now)[0]
        is False
    )


async def test_irrigation_and_external_permission_combine_and_reset_after_restart(hass):
    settings = config(
        irrigation_entity="switch.irrigation", permission_entity="input_boolean.garden"
    )
    rules = MowingConditions(settings)
    hass.states.async_set("switch.irrigation", "on")
    hass.states.async_set("input_boolean.garden", "on")
    assert "Irrigation" in rules.evaluate(hass, dt_util.utcnow())[1]
    hass.states.async_set("switch.irrigation", "off")
    now = dt_util.utcnow()
    assert rules.evaluate(hass, now)[0] is False

    assert rules.evaluate(hass, now + timedelta(minutes=15))[0] is True
    assert (
        MowingConditions(settings).evaluate(hass, now + timedelta(minutes=15))[0]
        is False
    )
    hass.states.async_set("input_boolean.garden", "off", {"reason": "Garden occupied"})
    assert "Garden occupied" in rules.evaluate(hass, now + timedelta(minutes=15))[1]
    hass.states.async_set("switch.irrigation", "unknown")
    assert rules.evaluate(hass, now)[0] is False


async def test_soil_with_different_units_cannot_grant_mowing_permission(hass):
    rules = MowingConditions(config(soil_entity="sensor.soil", drying_minutes=0))
    hass.states.async_set("sensor.soil", "20", {"unit_of_measurement": "°C"})
    assert rules.evaluate(hass, dt_util.utcnow()) == (
        False,
        "Soil moisture must use percentage units",
    )


async def test_schedule_adapter_uses_native_trigger_and_has_no_startup_or_end_catchup(
    hass,
):
    pytest.importorskip("homeassistant.components.schedule.trigger")
    manager = SimpleNamespace(async_request=AsyncMock(), async_cancel_owner=AsyncMock())
    entry = MockConfigEntry(domain=DOMAIN, data={})
    coordinator = SimpleNamespace(
        hass=hass, entry=entry, managed_mowing=manager, async_update_listeners=Mock()
    )
    runtime = MowingAutomation(
        coordinator, config(enabled=True, native_plans_disabled=True)
    )
    hass.states.async_set(
        "schedule.garden", "on", {"next_event": "2026-10-03T10:00:00Z"}
    )

    def permission_write():
        hass.states.async_set(
            "binary_sensor.garden_permission",
            "on" if runtime.permitted else "off",
            {"reason": runtime.reason, "evaluated_at": dt_util.utcnow().isoformat()},
        )

    permission = SimpleNamespace(
        entity_id="binary_sensor.garden_permission",
        async_write_ha_state=permission_write,
    )
    enable = SimpleNamespace(
        entity_id="switch.garden_automation", async_write_ha_state=Mock()
    )
    hass.states.async_set(enable.entity_id, "on")
    try:
        runtime.bind("permission", permission)
        runtime.bind("enabled", enable)
        runtime.bind("mower", SimpleNamespace())
        await hass.async_start()
        await hass.async_block_till_done()
        manager.async_request.assert_not_called()
        hass.states.async_set(
            "schedule.garden", "off", {"next_event": "2026-10-03T11:00:00Z"}
        )
        await hass.async_block_till_done()
        hass.states.async_set(
            "schedule.garden", "on", {"next_event": "2026-10-03T12:00:00Z"}
        )
        await hass.async_block_till_done()
        assert manager.async_request.call_count == 1
        params = manager.async_request.call_args.args[0]
        assert params["automation_entity"] == enable.entity_id
        assert params["permission_entity"] == permission.entity_id
        assert params["defer_minutes"] == 0
        assert params["interruption"] == "none"
        hass.states.async_set(
            "schedule.garden", "on", {"next_event": "2026-10-03T13:00:00Z"}
        )
        await hass.async_block_till_done()
        assert manager.async_request.call_count == 2
        await runtime.async_cancel_owned()
        manager.async_cancel_owner.assert_awaited_once_with(enable.entity_id)
        hass.states.async_set(
            "schedule.garden", "on", {"next_event": "2026-10-03T14:00:00Z"}
        )
        await hass.async_block_till_done()
        assert manager.async_request.call_count == 2
    finally:
        await runtime.async_close()


async def test_disabled_adapter_never_submits_and_close_drains_callbacks(hass):
    coordinator = SimpleNamespace(
        hass=hass,
        entry=SimpleNamespace(entry_id="garden"),
        managed_mowing=SimpleNamespace(async_request=AsyncMock()),
        async_update_listeners=Mock(),
    )
    runtime = MowingAutomation(coordinator, config())
    await runtime._run({})
    coordinator.managed_mowing.async_request.assert_not_called()
    await runtime.async_close()
    await runtime._run({})
    assert runtime._closed
    coordinator.managed_mowing.async_request.assert_not_called()


async def test_wizard_cancels_previous_owned_run_before_replacing_settings(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    cancel = AsyncMock()
    entry.runtime_data = SimpleNamespace(
        mowing_automation=SimpleNamespace(async_cancel_owned=cancel)
    )
    flow = DreameLawnMowerOptionsFlow(entry)
    flow.hass = hass
    flow._mowing_input = config()
    result = await flow.async_step_mowing_review({"native_plans_disabled": True})
    cancel.assert_awaited_once()
    assert result["data"][CONF_MOWING_AUTOMATION]["native_plans_disabled"] is True


async def test_saving_unchanged_wizard_keeps_enabled_runtime_and_pending_intent(hass):
    settings = config(enabled=True, native_plans_disabled=True)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={CONF_MOWING_AUTOMATION: settings},
    )
    cancel = AsyncMock()
    entry.runtime_data = SimpleNamespace(
        mowing_automation=SimpleNamespace(async_cancel_owned=cancel),
    )
    flow = DreameLawnMowerOptionsFlow(entry)
    flow.hass = hass
    flow._mowing_input = dict(settings)
    result = await flow.async_step_mowing_review({"native_plans_disabled": True})
    cancel.assert_not_awaited()
    assert result["data"][CONF_MOWING_AUTOMATION]["enabled"] is True


@requires_ui
@pytest.mark.parametrize(
    "cancel", [False, True], ids=["dry-and-start", "disable-and-cancel"]
)
async def test_real_entities_drive_one_guarded_run_or_cancel_without_movement(
    hass, cancel
):
    settings = config(
        enabled=True,
        native_plans_disabled=True,
        soil_entity="sensor.soil",
        drying_minutes=0,
        defer_minutes=60,
    )
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={CONF_MOWING_AUTOMATION: settings}
    )
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass)
    coordinator.entry = entry
    client = MowerTransport()
    client.descriptor = coordinator.client.descriptor
    coordinator.client = client
    mower = SimpleNamespace(
        hass=hass, entity_id="lawn_mower.garden", coordinator=coordinator
    )
    manager = ManagedMowing(mower)
    coordinator.managed_mowing = manager
    await manager.async_start()
    runtime = MowingAutomation(coordinator, settings)
    coordinator.mowing_automation = runtime
    permission = MowingPermissionSensor(coordinator, runtime)
    enabled = MowingAutomationSwitch(coordinator, runtime)
    sensors = EntityComponent(logging.getLogger(__name__), "binary_sensor", hass)
    switches = EntityComponent(logging.getLogger(__name__), "switch", hass)
    hass.states.async_set("sensor.soil", "80", {"unit_of_measurement": "%"})
    hass.states.async_set(
        "schedule.garden", "off", {"next_event": "2026-10-03T10:00:00Z"}
    )
    try:
        await sensors.async_add_entities([permission])
        await switches.async_add_entities([enabled])
        runtime.bind("mower", mower)
        await hass.async_start()
        await hass.async_block_till_done()
        assert hass.states.get(enabled.entity_id).state == "on"
        assert hass.states.get(permission.entity_id).state == "off"
        assert client.commands == []
        hass.states.async_set(
            "schedule.garden", "on", {"next_event": "2026-10-03T11:00:00Z"}
        )
        await hass.async_block_till_done()
        assert manager.record["phase"] == "waiting"
        assert client.commands == []
        assert runtime.last_outcome["status"] == "deferred"
        if cancel:
            await enabled.async_turn_off()
            await hass.async_block_till_done()
            assert entry.options[CONF_MOWING_AUTOMATION]["enabled"] is False
            assert hass.states.get(enabled.entity_id).state == "off"
            assert manager.record is None
        hass.states.async_set("sensor.soil", "40", {"unit_of_measurement": "%"})
        await hass.async_block_till_done()
        assert hass.states.get(permission.entity_id).state == "on"
        assert client.commands == ([] if cancel else ["start"])
        assert manager.record is None
        assert runtime.last_outcome["status"] == ("cancelled" if cancel else "started")
    finally:
        await runtime.async_close()
        await manager.async_close()
        await sensors.async_remove_entity(permission.entity_id)
        await switches.async_remove_entity(enabled.entity_id)
