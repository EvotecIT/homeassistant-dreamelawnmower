"""Execute the visual setup and its adapter through real HA state/trigger APIs."""

from __future__ import annotations

import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
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
    MowingTargetSelect,
    RotationMaxAreaNumber,
    RotationRestDaysNumber,
)
from custom_components.dreame_lawn_mower.mowing_conditions import MowingConditions
from custom_components.dreame_lawn_mower.option_updates import EntryUpdateSnapshot
from tests.components.dreame_lawn_mower.test_binary_sensor_state import _coordinator
from tests.components.dreame_lawn_mower.test_managed_mowing import MowerTransport
from tests.test_zone_mowing_history import _maps


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
    assert saved["mowing_target"] == "all"


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


def _rotation_runtime(hass, monkeypatch, *, overview, history=True, **settings):
    manager = SimpleNamespace(async_request=AsyncMock(), skip=Mock())
    coordinator = SimpleNamespace(
        hass=hass,
        entry=SimpleNamespace(entry_id="garden"),
        managed_mowing=manager,
        async_update_listeners=Mock(),
        selected_map_index=0,
        app_maps=_maps(),
        zone_mowing_sessions=SimpleNamespace(complete=True, attributable=True)
        if history
        else None,
    )
    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.mowing_automation.current_zone_overview",
        lambda _: overview,
    )
    runtime = MowingAutomation(
        coordinator,
        config(enabled=True, native_plans_disabled=True, **settings),
    )
    runtime.entities = {
        "permission": SimpleNamespace(
            entity_id="binary_sensor.garden_permission", async_write_ha_state=Mock()
        ),
        "enabled": SimpleNamespace(
            entity_id="switch.garden_automation", async_write_ha_state=Mock()
        ),
    }
    return runtime, manager


async def test_rotation_requests_only_due_whole_zones_on_the_selected_map(
    hass, monkeypatch
):
    overview = [
        {"zone_id": 4, "last_completed_at": None, "area_m2": 70},
        {
            "zone_id": 1,
            "last_completed_at": "2026-01-01T10:00:00+00:00",
            "area_m2": 200,
        },
        {"zone_id": 2, "last_completed_at": "2026-01-01T11:00:00+00:00", "area_m2": 40},
        {"zone_id": 6, "last_completed_at": dt_util.utcnow().isoformat(), "area_m2": 9},
    ]
    runtime, manager = _rotation_runtime(
        hass,
        monkeypatch,
        overview=overview,
        mowing_target="rotation",
        rotation_rest_days=1,
        rotation_max_area=300,
    )
    await runtime._run({})
    manager.skip.assert_not_called()
    params = manager.async_request.call_args.args[0]
    assert params["task_type"] == "zone"
    assert params["zone_ids"] == [4, 1]
    assert params["map_index"] == 0
    assert params["automation_entity"] == "switch.garden_automation"


@pytest.mark.parametrize(
    ("overview", "history", "reason"),
    [
        (
            [{"zone_id": 1, "last_completed_at": None, "area_m2": 10}],
            False,
            "zone_history_unavailable",
        ),
        ([], True, "zone_history_unavailable"),
        (
            [{"zone_id": 1, "last_completed_at": "TODAY", "area_m2": 10}],
            True,
            "no_zone_due",
        ),
    ],
)
async def test_rotation_skips_with_a_reason_and_never_falls_back_to_the_whole_lawn(
    hass, monkeypatch, overview, history, reason
):
    overview = [
        dict(item, last_completed_at=dt_util.utcnow().isoformat())
        if item["last_completed_at"] == "TODAY"
        else item
        for item in overview
    ]
    runtime, manager = _rotation_runtime(
        hass, monkeypatch, overview=overview, history=history, mowing_target="rotation"
    )
    await runtime._run({})
    manager.async_request.assert_not_called()
    params, skipped = manager.skip.call_args.args
    assert skipped == reason
    assert params["task_type"] == "zone"
    assert params["zone_ids"] == []
    assert params["automation_entity"] == "switch.garden_automation"


async def test_whole_lawn_stays_the_default_target(hass, monkeypatch):
    runtime, manager = _rotation_runtime(hass, monkeypatch, overview=[])
    assert runtime.config["mowing_target"] == "all"
    await runtime._run({})
    params = manager.async_request.call_args.args[0]
    assert params["task_type"] == "all"
    assert "zone_ids" not in params
    manager.skip.assert_not_called()


@pytest.mark.parametrize(
    "value",
    [
        {"mowing_target": "zones"},
        {"rotation_rest_days": -1},
        {"rotation_max_area": float("inf")},
    ],
)
def test_invalid_rotation_settings_are_rejected(value):
    assert automation_config({"schedule_entity": "schedule.garden", **value}) == {}


async def test_rotation_settings_change_in_place_without_a_reload(hass, monkeypatch):
    runtime, _ = _rotation_runtime(hass, monkeypatch, overview=[])
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={"scan_interval": 45, CONF_MOWING_AUTOMATION: dict(runtime.config)},
    )
    entry.add_to_hass(hass)
    runtime.coordinator.entry = entry
    runtime.coordinator.applied_entry_update = EntryUpdateSnapshot.capture(entry)
    await runtime.async_set_rotation(
        mowing_target="rotation", rotation_rest_days=0, rotation_max_area=250
    )
    saved = entry.options[CONF_MOWING_AUTOMATION]
    assert runtime.config == saved
    assert (saved["mowing_target"], saved["rotation_rest_days"]) == ("rotation", 0)
    assert saved["rotation_max_area"] == 250
    assert saved["enabled"] is True
    assert entry.options["scan_interval"] == 45
    assert not runtime.coordinator.applied_entry_update.requires_reload(entry)
    runtime.coordinator.async_update_listeners.assert_called()
    with pytest.raises(HomeAssistantError):
        await runtime.async_set_rotation(rotation_rest_days=99)
    with pytest.raises(HomeAssistantError):
        await runtime.async_set_rotation(enabled=False)
    assert entry.options[CONF_MOWING_AUTOMATION] == saved


@pytest.mark.parametrize("enabled", [False, True])
async def test_rotation_preserves_a_pending_switch_update(hass, monkeypatch, enabled):
    runtime, manager = _rotation_runtime(hass, monkeypatch, overview=[])
    runtime.enabled = not enabled
    runtime.config["enabled"] = not enabled
    manager.async_cancel_owner = AsyncMock()
    entry = MockConfigEntry(
        domain=DOMAIN, data={},
        options={CONF_MOWING_AUTOMATION: dict(runtime.config)},
    )
    entry.add_to_hass(hass)
    runtime.coordinator.entry = entry
    runtime.coordinator.applied_entry_update = EntryUpdateSnapshot.capture(entry)
    await runtime.async_set_enabled(enabled)
    await runtime.async_set_rotation(rotation_rest_days=2)
    assert entry.options[CONF_MOWING_AUTOMATION]["enabled"] is enabled
    assert runtime.enabled is enabled
    # The queued switch update must still be handled by its own listener.
    assert runtime.coordinator.applied_entry_update.requires_reload(entry)


async def test_rotation_acknowledges_only_its_own_changes_before_dispatch(
    hass, monkeypatch,
):
    runtime, _ = _rotation_runtime(hass, monkeypatch, overview=[])
    entry = MockConfigEntry(
        domain=DOMAIN, data={},
        options={"scan_interval": 45, CONF_MOWING_AUTOMATION: dict(runtime.config)},
    )
    entry.add_to_hass(hass)
    runtime.coordinator.entry = entry
    runtime.coordinator.applied_entry_update = EntryUpdateSnapshot.capture(entry)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "scan_interval": 90}
    )
    update = hass.config_entries.async_update_entry
    observed = []

    def dispatch(candidate, **kwargs):
        pending = SimpleNamespace(data=candidate.data, options=kwargs["options"])
        snapshot = runtime.coordinator.applied_entry_update
        observed.append(snapshot.changed_options(pending.options))
        assert not snapshot.requires_reload(pending)
        return update(candidate, **kwargs)

    monkeypatch.setattr(hass.config_entries, "async_update_entry", dispatch)
    await runtime.async_set_rotation(rotation_max_area=250)
    assert observed == [frozenset({"scan_interval"})]
    assert runtime.coordinator.applied_entry_update.options["scan_interval"] == 45
    assert entry.options["scan_interval"] == 90


async def test_rotation_materializes_defaults_without_swallowing_wizard_changes(
    hass, monkeypatch,
):
    runtime, _ = _rotation_runtime(hass, monkeypatch, overview=[])
    legacy = {
        key: value for key, value in runtime.config.items()
        if key not in {"mowing_target", "rotation_rest_days", "rotation_max_area"}
    }
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={CONF_MOWING_AUTOMATION: legacy}
    )
    entry.add_to_hass(hass)
    runtime.coordinator.entry = entry
    runtime.coordinator.applied_entry_update = EntryUpdateSnapshot.capture(entry)
    await runtime.async_set_rotation(rotation_rest_days=2)
    assert not runtime.coordinator.applied_entry_update.requires_reload(entry)
    changed = {**entry.options[CONF_MOWING_AUTOMATION], "soil_entity": "sensor.new"}
    hass.config_entries.async_update_entry(
        entry, options={CONF_MOWING_AUTOMATION: changed}
    )
    await runtime.async_set_rotation(rotation_max_area=250)
    assert entry.options[CONF_MOWING_AUTOMATION]["soil_entity"] == "sensor.new"
    assert runtime.coordinator.applied_entry_update.requires_reload(entry)


@pytest.mark.parametrize("revision_state", ["missing", "unverified", "mismatched"])
async def test_rotation_requires_a_verified_map_revision(
    hass, monkeypatch, revision_state,
):
    runtime, manager = _rotation_runtime(
        hass, monkeypatch,
        overview=[{"zone_id": 1, "last_completed_at": None, "area_m2": 100}],
        mowing_target="rotation",
    )
    maps = runtime.coordinator.app_maps
    if revision_state == "missing":
        runtime.coordinator.app_maps = None
    elif revision_state == "unverified":
        maps["maps"][0]["hash_match"] = False
    else:
        maps["maps"][0]["md5"] = "f" * 32
    await runtime._run({})
    manager.async_request.assert_not_called()
    assert manager.skip.call_args.args[1] == "zone_history_unavailable"
    assert runtime.plan()["reason"] == "zone_history_unavailable"


async def test_plan_previews_the_same_zones_as_the_next_start(hass, monkeypatch):
    overview = [
        {"zone_id": 2, "label": "Front", "last_completed_at": None, "area_m2": 40},
        {"zone_id": 1, "label": "Back", "last_completed_at": None, "area_m2": 107.5},
        {"zone_id": 3, "label": "Side", "last_completed_at": None, "area_m2": 300},
    ]
    runtime, manager = _rotation_runtime(
        hass,
        monkeypatch,
        overview=overview,
        mowing_target="rotation",
        rotation_max_area=200,
    )
    next_start = dt_util.now() + timedelta(hours=2)
    hass.states.async_set(
        "schedule.garden", "off", {"next_event": next_start.isoformat()}
    )
    plan = runtime.plan()
    assert plan["next_start"] == next_start.isoformat()
    assert plan["task_type"] == "zone"
    assert [zone["label"] for zone in plan["zones"]] == ["Front", "Back"]
    assert plan["area_m2"] == 147.5
    assert plan["reason"] is None
    await runtime._run({})
    assert manager.async_request.call_args.args[0]["zone_ids"] == [
        zone["zone_id"] for zone in plan["zones"]
    ]
    summary = runtime.summary
    assert summary["plan"] == plan
    assert summary["mowing_target"] == "rotation"
    assert summary["rotation_max_area"] == 200


async def test_plan_reports_why_no_zone_would_start_and_whole_lawn(hass, monkeypatch):
    runtime, _ = _rotation_runtime(
        hass, monkeypatch, overview=[], mowing_target="rotation"
    )
    hass.states.async_set("schedule.garden", "on", {"next_event": None})
    plan = runtime.plan()
    assert plan["next_start"] is None
    assert plan["zones"] == []
    assert plan["reason"] == "zone_history_unavailable"
    runtime.config = config(enabled=True, native_plans_disabled=True)
    assert runtime.plan()["task_type"] == "all"
    assert runtime.plan()["reason"] is None


async def test_settings_controls_are_real_entities_that_save_in_place(hass):
    settings = config(enabled=True, native_plans_disabled=True)
    entry = MockConfigEntry(
        domain=DOMAIN, data={}, options={CONF_MOWING_AUTOMATION: settings}
    )
    entry.add_to_hass(hass)
    coordinator = _coordinator(hass)
    coordinator.entry = entry
    coordinator.applied_entry_update = EntryUpdateSnapshot.capture(entry)
    runtime = MowingAutomation(coordinator, settings)
    coordinator.mowing_automation = runtime
    target = MowingTargetSelect(coordinator, runtime)
    rest = RotationRestDaysNumber(coordinator, runtime)
    area = RotationMaxAreaNumber(coordinator, runtime)
    selects = EntityComponent(logging.getLogger(__name__), "select", hass)
    numbers = EntityComponent(logging.getLogger(__name__), "number", hass)
    try:
        await selects.async_add_entities([target])
        await numbers.async_add_entities([rest, area])
        await hass.async_block_till_done()
        assert hass.states.get(target.entity_id).state == "all"
        assert hass.states.get(rest.entity_id).state == "1"
        assert hass.states.get(area.entity_id).state == "0.0"
        assert runtime.summary["target_entity"] == target.entity_id
        assert runtime.summary["rest_days_entity"] == rest.entity_id
        assert runtime.summary["max_area_entity"] == area.entity_id
        assert not runtime._started
        # The select/number services call exactly these entity methods.
        await target.async_select_option("rotation")
        await rest.async_set_native_value(0)
        await area.async_set_native_value(300)
        await hass.async_block_till_done()
        saved = entry.options[CONF_MOWING_AUTOMATION]
        assert saved["mowing_target"] == "rotation"
        assert saved["rotation_rest_days"] == 0
        assert saved["rotation_max_area"] == 300
        assert hass.states.get(target.entity_id).state == "rotation"
        assert hass.states.get(rest.entity_id).state == "0"
        assert hass.states.get(area.entity_id).state == "300.0"
        assert not coordinator.applied_entry_update.requires_reload(entry)
    finally:
        await runtime.async_close()


async def test_settings_controls_do_not_start_the_schedule_before_its_entities(
    hass,
):
    coordinator = SimpleNamespace(
        hass=hass,
        entry=SimpleNamespace(entry_id="garden"),
        managed_mowing=SimpleNamespace(async_request=AsyncMock()),
        async_update_listeners=Mock(),
    )
    runtime = MowingAutomation(
        coordinator, config(enabled=True, native_plans_disabled=True)
    )
    for kind in ("target", "rest_days", "max_area"):
        runtime.bind(kind, SimpleNamespace(entity_id=f"x.{kind}"))
    assert not runtime._started
    runtime.bind("permission", SimpleNamespace(async_write_ha_state=Mock()))
    runtime.bind("enabled", SimpleNamespace(entity_id="switch.garden_automation"))
    assert not runtime._started
    runtime.bind("mower", SimpleNamespace())
    assert runtime._started
    await runtime.async_close()
