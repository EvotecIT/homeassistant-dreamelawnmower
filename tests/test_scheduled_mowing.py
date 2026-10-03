"""Unattended starts require known evidence and never catch up or resume."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.core import Context, HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.script import Script
from homeassistant.util.yaml.loader import load_yaml

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import schedule_start
from custom_components.dreame_lawn_mower.scheduled_mowing import (
    async_start_scheduled_mowing,
)

BLUEPRINT_PATH = (
    Path(__file__).parents[1]
    / "blueprints"
    / "automation"
    / "dreame_lawn_mower"
    / "guarded_schedule_mowing.yaml"
)


def eligible_evidence():
    snapshot = SimpleNamespace(
        online=True,
        docked=True,
        task_resumable=False,
        mowing_session_active=False,
        error_code=0,
        child_lock=False,
        battery_level=80,
    )
    weather = {"rain_protect_end_time_present": True, "rain_protection_active": False}
    native = {
        "map_inventory_valid": True,
        "map_indices": [2],
        "current_map_index": 2,
        "schedules": [
            {
                "idx": 2,
                "protocol": "tables",
                "read_status": "complete",
                "plans": [{"enabled": False}],
            }
        ],
    }
    return snapshot, weather, native


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("online", None, "connectivity_unknown_or_offline"),
        ("docked", False, "not_docked"),
        ("task_resumable", True, "resumable_or_unknown_task"),
        ("task_resumable", None, "resumable_or_unknown_task"),
        ("mowing_session_active", True, "active_or_unknown_task"),
        ("mowing_session_active", None, "active_or_unknown_task"),
        ("battery_level", None, "battery_unknown"),
        ("battery_level", 29, "battery_below_threshold_or_invalid"),
        ("error_code", None, "fault_or_unknown_fault_state"),
        ("child_lock", True, "child_lock_enabled"),
    ],
)
def test_unknown_or_blocked_mower_state_has_a_skip_reason(field, value, reason):
    snapshot, weather, native = eligible_evidence()
    setattr(snapshot, field, value)
    assert (
        schedule_start.scheduled_start_block_reason(snapshot, weather, native) == reason
    )


def test_rain_unknown_native_competition_and_wrong_map_block_starts():
    snapshot, weather, native = eligible_evidence()
    assert (
        schedule_start.scheduled_start_block_reason(snapshot, weather, native) is None
    )
    assert (
        schedule_start.scheduled_start_block_reason(
            snapshot,
            weather,
            native,
            map_index=1,
        )
        == "requested_map_not_current"
    )
    weather["rain_protection_active"] = True
    assert (
        schedule_start.scheduled_start_block_reason(
            snapshot,
            weather,
            native,
        )
        == "rain_delay_active"
    )
    weather.pop("rain_protect_end_time_present")
    assert (
        schedule_start.scheduled_start_block_reason(
            snapshot,
            weather,
            native,
        )
        == "rain_delay_unknown"
    )
    _snapshot, weather, _native = eligible_evidence()
    native["schedules"][0]["plans"][0]["enabled"] = True
    assert (
        schedule_start.scheduled_start_block_reason(
            snapshot,
            weather,
            native,
        )
        == "native_schedule_enabled_or_unknown"
    )
    native["schedules"][0]["plans"][0]["enabled"] = False
    native["schedules"][0]["protocol"] = "document"
    assert (
        schedule_start.scheduled_start_block_reason(
            snapshot,
            weather,
            native,
        )
        == "native_schedule_state_unknown"
    )  # Default document slot must also be read.


def entity_with_evidence():
    snapshot, weather, native = eligible_evidence()
    client = SimpleNamespace(
        async_get_schedule_start_evidence=AsyncMock(return_value=native),
        async_get_weather_protection=AsyncMock(return_value=weather),
        async_refresh_authoritative_snapshot=AsyncMock(return_value=snapshot),
        async_start_fresh_mowing=AsyncMock(),
    )
    entity = SimpleNamespace(
        entity_id="lawn_mower.garden",
        hass=SimpleNamespace(bus=SimpleNamespace(async_fire=Mock())),
        coordinator=SimpleNamespace(client=client, async_update_listeners=Mock()),
        async_start_zone_mowing=AsyncMock(),
        async_start_spot_mowing=AsyncMock(),
        async_start_edge_mowing=AsyncMock(),
    )
    return entity, snapshot, weather, native


def test_skipped_run_refreshes_evidence_but_sends_no_mower_action():
    entity, snapshot, _weather, _native = entity_with_evidence()
    snapshot.task_resumable = True
    result = asyncio.run(async_start_scheduled_mowing(entity))
    assert result["status"] == "skipped"
    assert result["reason"] == "resumable_or_unknown_task"
    entity.coordinator.client.async_start_fresh_mowing.assert_not_awaited()
    entity.async_start_zone_mowing.assert_not_awaited()
    assert entity.coordinator.last_scheduled_run == result
    assert (
        entity.hass.bus.async_fire.call_args.args[0]
        == "dreame_lawn_mower_scheduled_run"
    )


@pytest.mark.parametrize(
    ("task_type", "argument", "targets"),
    [
        ("zone", "zone_ids", [7]),
        ("edge", "contour_ids", [[7, 0]]),
        ("spot", "spot_ids", [4]),
    ],
)
def test_targeted_run_uses_explicit_command_and_bound_map(task_type, argument, targets):
    entity, _snapshot, _weather, _native = entity_with_evidence()
    result = asyncio.run(
        async_start_scheduled_mowing(
            entity,
            task_type=task_type,
            map_index=2,
            **{argument: targets},
        )
    )
    assert result["status"] == "started"
    getattr(entity, f"async_start_{task_type}_mowing").assert_awaited_once_with(
        targets, require_inactive_task=True
    )
    entity.coordinator.client.async_start_fresh_mowing.assert_not_awaited()


def test_all_area_acknowledgement_is_submitted_until_active_state_is_observed():
    entity, snapshot, _weather, _native = entity_with_evidence()
    result = asyncio.run(async_start_scheduled_mowing(entity, map_index=0))
    assert result["status"] == "submitted"  # All-area mode uses the current map.
    entity.coordinator.client.async_start_fresh_mowing.assert_awaited_once()
    active = deepcopy(snapshot)
    active.mowing_session_active = True
    entity.coordinator.client.async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[snapshot, active],
    )
    assert asyncio.run(async_start_scheduled_mowing(entity))["status"] == "started"


def test_concurrent_block_is_skipped_instead_of_waiting_to_start_later():
    async def scenario():
        entity, _snapshot, _weather, _native = entity_with_evidence()
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed_evidence():
            entered.set()
            await release.wait()
            return _native

        entity.coordinator.client.async_get_schedule_start_evidence.side_effect = (
            delayed_evidence
        )
        first = asyncio.create_task(async_start_scheduled_mowing(entity))
        await entered.wait()
        second = await async_start_scheduled_mowing(entity)
        assert second["reason"] == "scheduled_start_in_progress"
        release.set()
        await first
        entity.coordinator.client.async_start_fresh_mowing.assert_awaited_once()

    asyncio.run(scenario())


def substituted_blueprint():
    blueprint = Blueprint(
        load_yaml(BLUEPRINT_PATH),
        path=str(BLUEPRINT_PATH),
        expected_domain="automation",
        schema=AUTOMATION_BLUEPRINT_SCHEMA,
    )
    return BlueprintInputs(
        blueprint,
        {
            "use_blueprint": {
                "path": str(BLUEPRINT_PATH),
                "input": {
                    "mower_entity": "lawn_mower.garden",
                    "schedule_entity": "schedule.garden",
                },
            }
        },
    ).async_substitute()


@pytest.mark.parametrize("service_available", [True, False])
def test_blueprint_actions_run_in_ha_and_render_outcome_or_service_failure(
    tmp_path,
    service_available,
):
    async def scenario():
        config = substituted_blueprint()
        assert config["mode"] == "single"
        assert config["triggers"] == [
            {
                "trigger": "schedule.block_started",
                "target": {"entity_id": "schedule.garden"},
            }
        ]
        hass = HomeAssistant(str(tmp_path))
        calls = []

        async def start(call):
            if not service_available:
                raise HomeAssistantError("Mower is unavailable")
            assert call.data["task_type"] == "all"
            return {
                "lawn_mower.garden": {
                    "status": "skipped",
                    "reason": "rain_delay_active",
                }
            }

        async def notify(call):
            calls.append(dict(call.data))

        hass.services.async_register(
            "dreame_lawn_mower",
            "start_scheduled_mowing",
            start,
            supports_response=SupportsResponse.OPTIONAL,
        )
        hass.services.async_register("persistent_notification", "create", notify)
        script = Script(
            hass, cv.SCRIPT_SCHEMA(config["actions"]), "Guarded mowing", "automation"
        )
        result = await script.async_run(config["variables"], context=Context())
        expected = (
            "rain_delay_active" if service_available else "mower_service_unavailable"
        )
        assert result.variables["scheduled_result"]["reason"] == expected
        assert expected.replace("_", " ") in calls[0]["message"]
        await hass.async_stop()

    asyncio.run(scenario())
