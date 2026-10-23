"""Managed runs use real HA state, event and private-storage boundaries."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_managed_control,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
)
from custom_components.dreame_lawn_mower.managed_mowing import ManagedMowing
from custom_components.dreame_lawn_mower.managed_mowing_state import (
    TASK_SCHEMA,
    decode_managed_record,
)
from custom_components.dreame_lawn_mower.mowing_permission import permission_block
from custom_components.dreame_lawn_mower.scheduled_mowing import (
    async_start_scheduled_mowing,
)
from tests.test_scheduled_mowing import eligible_evidence


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations():
    """Use the explicit runtime below, including real atomic disk storage.

    The directory's default fixture creates another HA instance whose mocked
    Store would hide persistence failures in these lifecycle tests.
    """
    yield


class MowerTransport:
    """Deterministic device boundary; no HA or policy behavior is mocked."""

    def __init__(self):
        self.snapshot, self.weather, self.native = eligible_evidence()
        self.snapshot.mission_task_id = 10
        self.snapshot.paused = False
        self.snapshot.returning = False
        self.commands = []
        self.read_hook = None

    async def async_get_schedule_start_evidence(self):
        return deepcopy(self.native)

    async def async_get_weather_protection(self, **kwargs):
        return deepcopy(self.weather)

    async def async_get_device_settings(self):
        return {"resume_after_charging": False}

    async def async_refresh_authoritative_snapshot(self):
        if self.read_hook:
            self.read_hook()
        return deepcopy(self.snapshot)

    async def async_start_fresh_mowing(self, **kwargs):
        if kwargs.get("admission"):
            assert kwargs["admission"]()
        if "expected_map_index" in kwargs:
            assert kwargs["expected_map_index"] == self.native["current_map_index"]
        self.commands.append("start")
        self.snapshot.mission_task_id += 1
        self.snapshot.docked = False
        self.snapshot.mowing_session_active = True
        self.snapshot.task_resumable = True

    async def async_control_managed_task(self, command, **kwargs):
        assert kwargs["expected_task_id"] == self.snapshot.mission_task_id
        assert kwargs["expected_map_index"] == self.native["current_map_index"]
        assert kwargs["admission"]()
        self.commands.append(command)
        self.snapshot.paused = command == "pause"
        if command == "dock":
            self.snapshot.docked = True
            self.snapshot.mowing_session_active = False
            self.snapshot.task_resumable = False
        return {"pause": "paused", "dock": "docked", "resume": "resumed"}[command]


@asynccontextmanager
async def managed_runtime(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.state = CoreState.running
    if hasattr(dr, "async_setup"):
        dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    client = MowerTransport()
    entity = SimpleNamespace(
        hass=hass,
        entity_id="lawn_mower.garden",
        coordinator=SimpleNamespace(
            entry=SimpleNamespace(entry_id="garden"),
            client=client,
            async_add_listener=Mock(return_value=Mock()),
            async_update_listeners=Mock(),
        ),
    )
    owner = ManagedMowing(entity)
    await owner.async_start()
    try:
        yield owner, client, hass
    finally:
        await owner.async_close()
        await hass.async_stop()


def request(**kwargs):
    return TASK_SCHEMA({"task_type": "all", "minimum_battery": 30, **kwargs})


@pytest.mark.asyncio
async def test_permission_distinguishes_blocked_unknown_and_stale(tmp_path):
    async with managed_runtime(tmp_path) as (_, client, hass):
        entity_id = "binary_sensor.lawn_permission"
        assert permission_block(hass, "", 1800) is None
        assert (
            permission_block(hass, entity_id, 1800)[0] == "external_permission_unknown"
        )
        hass.states.async_set(entity_id, "off", {"reason": "Soil is wet"})
        blocked = permission_block(hass, entity_id, 1800)
        assert blocked == ("external_permission_blocked", f"{entity_id}: Soil is wet")
        hass.states.async_set(entity_id, "unknown")
        assert (
            permission_block(hass, entity_id, 1800)[0] == "external_permission_unknown"
        )
        hass.states.async_set(entity_id, "on")
        assert permission_block(hass, entity_id, 1800) is None
        assert (
            permission_block(
                hass,
                entity_id,
                1800,
                now=dt_util.utcnow() + timedelta(seconds=1801),
            )[0]
            == "external_permission_stale"
        )
        assert client.commands == []


@pytest.mark.asyncio
async def test_permission_rechecked_after_mower_preflight(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        client.read_hook = lambda: hass.states.async_set(
            "binary_sensor.lawn_permission",
            "off",
            {"reason": "Watering started"},
        )
        result = await async_start_scheduled_mowing(
            owner.entity,
            permission_entity="binary_sensor.lawn_permission",
        )
        assert result["reason"] == "external_permission_blocked"
        assert "Watering started" in result["detail"]
        assert client.commands == []
        assert owner.coordinator.scheduled_run_history.latest() == result


@pytest.mark.asyncio
async def test_deferred_run_starts_once_when_permission_clears(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "off")
        result = await owner.async_request(
            request(
                permission_entity="binary_sensor.lawn_permission",
                defer_minutes=60,
                run_id="morning",
            )
        )
        assert result["status"] == "deferred"
        assert result["reason"] == "external_permission_blocked"
        saved = await hass.async_add_executor_job(
            lambda: json.loads(
                Path(hass.config.path(".storage", owner._store.key)).read_text()
            ),
        )
        assert saved["data"]["record"]["phase"] == "waiting"
        assert client.commands == []
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await hass.async_block_till_done()
        assert client.commands == ["start"]
        assert owner.record is None
        duplicate = await owner.async_request(
            request(run_id="morning", defer_minutes=60)
        )
        assert duplicate["reason"] == "duplicate_scheduled_run"
        assert client.commands == ["start"]


@pytest.mark.asyncio
async def test_deadline_expires_without_start_and_no_backlog(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "off")
        params = request(
            permission_entity="binary_sensor.lawn_permission", defer_minutes=60
        )
        await owner.async_request(params)
        assert (await owner.async_request(params))[
            "reason"
        ] == "managed_run_in_progress"
        owner.record["start_deadline"] = (
            dt_util.utcnow() - timedelta(seconds=1)
        ).isoformat()
        await owner._async_tick()
        assert owner.record is None
        assert owner.coordinator.last_scheduled_run["status"] == "expired"
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await hass.async_block_till_done()
        assert client.commands == []


@pytest.mark.asyncio
async def test_deferred_run_cannot_follow_a_changed_map(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "off")
        await owner.async_request(
            request(
                permission_entity="binary_sensor.lawn_permission",
                defer_minutes=60,
            )
        )
        client.native["current_map_index"] = 3
        client.native["map_indices"] = [2, 3]
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await hass.async_block_till_done()
        assert (
            owner.coordinator.last_scheduled_run["reason"]
            == "requested_map_not_current"
        )
        assert client.commands == []
        assert owner.record is None


@pytest.mark.asyncio
async def test_real_manual_service_cancels_pending_start(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "off")
        await owner.async_request(
            request(
                permission_entity="binary_sensor.lawn_permission",
                defer_minutes=60,
            )
        )
        hass.services.async_register("lawn_mower", "pause", AsyncMock())
        await hass.services.async_call(
            "lawn_mower",
            "pause",
            {"entity_id": "lawn_mower.garden"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert owner.record is None
        assert owner.coordinator.last_scheduled_run["reason"] == "manual_intervention"
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await hass.async_block_till_done()
        assert client.commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cancel_at", [1, 2], ids=["initial_preflight", "guarded_preflight"]
)
async def test_manual_cancel_during_preflight_revokes_dispatch(tmp_path, cancel_at):
    async with managed_runtime(tmp_path) as (owner, client, _):
        count = 0

        def interrupt():
            nonlocal count
            count += 1
            if count == cancel_at:
                owner.cancel()

        client.read_hook = interrupt
        result = await owner.async_request(request(defer_minutes=60))
        assert result["status"] == "cancelled"
        assert client.commands == []


@pytest.mark.asyncio
async def test_pause_recovery_is_same_task_and_explicitly_opted_in(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await owner.async_request(
            request(
                permission_entity="binary_sensor.lawn_permission",
                interruption="pause",
                resume_when_allowed=True,
                recovery_minutes=120,
            )
        )
        await owner._async_tick()
        identity = client.snapshot.mission_task_id
        hass.states.async_set("binary_sensor.lawn_permission", "off")
        await hass.async_block_till_done()
        assert owner.record["phase"] == "paused"
        assert client.commands == ["start", "pause"]
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await hass.async_block_till_done()
        assert client.commands == ["start", "pause", "resume"]
        assert client.snapshot.mission_task_id == identity
        assert owner.record["phase"] == "confirming_resume"
        await owner._async_tick()
        assert owner.record["phase"] == "running"


@pytest.mark.asyncio
async def test_pause_without_recovery_never_restarts_when_dry(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await owner.async_request(
            request(
                permission_entity="binary_sensor.lawn_permission",
                interruption="pause",
            )
        )
        await owner._async_tick()
        hass.states.async_set("binary_sensor.lawn_permission", "off")
        await hass.async_block_till_done()
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await hass.async_block_till_done()
        assert client.commands == ["start", "pause"]
        assert (
            owner.coordinator.last_scheduled_run["reason"]
            == "automatic_resume_disabled"
        )


@pytest.mark.asyncio
async def test_recovery_expiry_and_task_replacement_never_resume(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await owner.async_request(
            request(
                permission_entity="binary_sensor.lawn_permission",
                interruption="pause",
                resume_when_allowed=True,
                recovery_minutes=120,
            )
        )
        await owner._async_tick()
        hass.states.async_set("binary_sensor.lawn_permission", "off")
        await hass.async_block_till_done()
        owner.record["recovery_deadline"] = (
            dt_util.utcnow() - timedelta(seconds=1)
        ).isoformat()
        await owner._async_tick()
        assert owner.coordinator.last_scheduled_run["status"] == "expired"
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await hass.async_block_till_done()
        assert client.commands == ["start", "pause"]


@pytest.mark.asyncio
async def test_unexpected_task_or_pause_cancels_tracking(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await owner.async_request(
            request(
                permission_entity="binary_sensor.lawn_permission",
                interruption="pause",
                resume_when_allowed=True,
                recovery_minutes=120,
            )
        )
        await owner._async_tick()
        client.snapshot.mission_task_id += 1
        await owner._async_tick()
        assert owner.record is None
        assert (
            owner.coordinator.last_scheduled_run["reason"]
            == "managed_task_changed_or_unknown"
        )
        assert client.commands == ["start"]


@pytest.mark.asyncio
async def test_restart_restores_only_never_dispatched_intent(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "off")
        await owner.async_request(
            request(
                permission_entity="binary_sensor.lawn_permission",
                defer_minutes=60,
            )
        )
        await owner.async_close()
        restored = ManagedMowing(owner.entity)
        await restored.async_start()
        await hass.async_block_till_done()
        assert restored.record["phase"] == "waiting"
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await hass.async_block_till_done()
        assert client.commands == ["start"]
        await restored.async_close()


@pytest.mark.asyncio
async def test_restored_blueprint_waits_for_owner_after_home_assistant_start(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("automation.mowing", "on")
        hass.states.async_set("binary_sensor.lawn_permission", "off")
        await owner.async_request(request(
            permission_entity="binary_sensor.lawn_permission",
            automation_entity="automation.mowing", defer_minutes=60,
        ))
        await owner.async_close()
        hass.state = CoreState.starting
        hass.states.async_remove("automation.mowing")
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        restored = ManagedMowing(owner.entity)
        await restored.async_start()
        try:
            await hass.async_block_till_done()
            assert restored.record["phase"] == "waiting"
            assert client.commands == []
            hass.states.async_set("automation.mowing", "on")
            await hass.async_block_till_done()
            assert client.commands == []
            hass.state = CoreState.running
            hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
            await hass.async_block_till_done()
            assert client.commands == ["start"]
            assert restored.record is None
        finally:
            await restored.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [
    "scheduled_run_cancelled", "manual_intervention", "automation_disabled_or_reloaded",
])
async def test_revocation_is_durable_when_close_precedes_queued_tick(tmp_path, reason):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "off")
        await owner.async_request(request(
            permission_entity="binary_sensor.lawn_permission", defer_minutes=60,
        ))
        owner.cancel(reason)
        await owner.async_close()
        saved = await hass.async_add_executor_job(
            lambda: json.loads(
                Path(hass.config.path(".storage", owner._store.key)).read_text()
            )
        )
        assert saved["data"]["record"] is None
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        restored = ManagedMowing(owner.entity)
        await restored.async_start()
        try:
            await hass.async_block_till_done()
            assert restored.record is None
            assert client.commands == []
        finally:
            await restored.async_close()


@pytest.mark.asyncio
async def test_explicit_cancel_returns_with_durable_revocation(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "off")
        await owner.async_request(request(
            permission_entity="binary_sensor.lawn_permission", defer_minutes=60,
        ))
        await owner.async_cancel()
        saved = await hass.async_add_executor_job(
            lambda: json.loads(
                Path(hass.config.path(".storage", owner._store.key)).read_text()
            )
        )
        assert saved["data"]["record"] is None
        assert owner.record is None
        assert client.commands == []


@pytest.mark.asyncio
async def test_restart_does_not_replay_dispatch_or_resume_a_paused_run(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await owner.async_request(
            request(
                permission_entity="binary_sensor.lawn_permission",
                interruption="pause",
                resume_when_allowed=True,
                recovery_minutes=120,
            )
        )
        await owner._async_tick()
        owner.record["phase"] = "dispatching"
        await owner._save()
        await owner.async_close()
        restored = ManagedMowing(owner.entity)
        await restored.async_start()
        await hass.async_block_till_done()
        assert restored.record is None
        assert (
            owner.coordinator.last_scheduled_run["reason"]
            == "restart_cancelled_recovery"
        )
        assert client.commands == ["start"]
        await restored.async_close()


@pytest.mark.asyncio
async def test_ambiguous_command_is_not_automatically_retried(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, _):
        client.async_start_fresh_mowing = AsyncMock(
            side_effect=DreameLawnMowerConnectionError("ambiguous")
        )
        result = await owner.async_request(request(defer_minutes=60))
        assert result["status"] == "failed"
        assert result["reason"] == "command_not_confirmed"
        await owner._async_tick()
        client.async_start_fresh_mowing.assert_awaited_once()


@pytest.mark.asyncio
async def test_storage_failure_prevents_dispatch(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, _):
        owner._store.async_save = AsyncMock(side_effect=OSError("disk unavailable"))
        result = await owner.async_request(request(defer_minutes=60))
        assert result["reason"] == "managed_storage_unavailable"
        assert owner.record is None
        assert client.commands == []


@pytest.mark.asyncio
async def test_native_control_refuses_wrong_task_and_revoked_admission(tmp_path):
    async with managed_runtime(tmp_path) as (_, client, _hass):
        client.snapshot.task_resumable = True
        client.async_get_current_app_map_index = AsyncMock(return_value=2)
        client._async_pause_mowing = AsyncMock()
        with pytest.raises(DreameLawnMowerCommandRejectedError):
            await client_managed_control.async_control_owned_task(
                client,
                "pause",
                expected_task_id=99,
                expected_map_index=2,
                admission=lambda: True,
            )
        with pytest.raises(DreameLawnMowerCommandRejectedError):
            await client_managed_control.async_control_owned_task(
                client,
                "pause",
                expected_task_id=10,
                expected_map_index=2,
                admission=lambda: False,
            )
        client._async_pause_mowing.assert_not_awaited()


def test_persisted_record_rejects_unbounded_or_unknown_state():
    assert decode_managed_record({"phase": "waiting"}, dt_util.utcnow()) is None
    old = dt_util.utcnow() - timedelta(hours=25)
    record = {
        "params": request(defer_minutes=60),
        "run_id": "test",
        "map_index": 2,
        "baseline_task_id": 10,
        "task_id": None,
        "phase": "waiting",
        "created_at": old.isoformat(),
        "start_deadline": None,
        "recovery_deadline": None,
        "command_at": None,
    }
    assert decode_managed_record(record, dt_util.utcnow()) is None


def test_permission_uses_report_age_instead_of_unchanged_state_age():
    now = dt_util.utcnow()
    state = State(
        "binary_sensor.lawn_permission",
        "on",
        last_changed=now - timedelta(days=1),
        last_updated=now,
        last_reported=now,
    )
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: state))
    assert permission_block(hass, state.entity_id, 60, now=now) is None


@pytest.mark.asyncio
async def test_ambiguous_start_is_not_deferred_when_permission_changes(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "on")

        async def ambiguous(**kwargs):
            client.commands.append("start_attempt")
            hass.states.async_set("binary_sensor.lawn_permission", "off")
            raise DreameLawnMowerConnectionError("Acknowledgement lost")

        client.async_start_fresh_mowing = AsyncMock(side_effect=ambiguous)
        result = await owner.async_request(
            request(
                permission_entity="binary_sensor.lawn_permission",
                defer_minutes=60,
            )
        )
        assert result["status"] == "failed"
        assert owner.record is None
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await hass.async_block_till_done()
        assert client.commands == ["start_attempt"]


@pytest.mark.asyncio
async def test_pause_management_requires_identity_and_disabled_native_resume(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        params = request(
            permission_entity="binary_sensor.lawn_permission", interruption="pause"
        )
        client.snapshot.mission_task_id = None
        assert (await owner.async_request(params))[
            "reason"
        ] == "managed_task_identity_required"
        client.snapshot.mission_task_id = 10
        client.async_get_device_settings = AsyncMock(
            return_value={"resume_after_charging": True}
        )
        assert (await owner.async_request(params))[
            "reason"
        ] == "native_auto_resume_enabled_or_unknown"
        assert client.commands == []


@pytest.mark.asyncio
async def test_end_and_dock_policy_never_rearms_when_permission_returns(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await owner.async_request(
            request(
                permission_entity="binary_sensor.lawn_permission",
                interruption="dock",
            )
        )
        await owner._async_tick()
        hass.states.async_set("binary_sensor.lawn_permission", "off")
        await hass.async_block_till_done()
        assert client.commands == ["start", "dock"]
        assert owner.record is None
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await hass.async_block_till_done()
        assert client.commands == ["start", "dock"]


@pytest.mark.asyncio
async def test_unexpected_pause_revokes_recovery(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        hass.states.async_set("automation.mowing", "on")
        await owner.async_request(
            request(
                permission_entity="binary_sensor.lawn_permission",
                interruption="pause",
                automation_entity="automation.mowing",
                resume_when_allowed=True,
                recovery_minutes=120,
            )
        )
        await owner._async_tick()
        client.snapshot.paused = True
        await owner._async_tick()
        assert (
            owner.coordinator.last_scheduled_run["reason"]
            == "manual_or_external_intervention"
        )
        assert owner.record is None
        assert client.commands == ["start"]


@pytest.mark.asyncio
async def test_disabled_owner_cancels_a_waiting_request(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "off")
        hass.states.async_set("automation.mowing", "on")
        await owner.async_request(
            request(
                permission_entity="binary_sensor.lawn_permission",
                defer_minutes=60,
                automation_entity="automation.mowing",
            )
        )
        hass.states.async_set("automation.mowing", "off")
        await hass.async_block_till_done()
        assert owner.record is None
        assert (
            owner.coordinator.last_scheduled_run["reason"]
            == "automation_disabled_or_unknown"
        )
        assert client.commands == []
