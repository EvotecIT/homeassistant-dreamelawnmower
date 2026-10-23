"""Decoded native states, durable terminal failures and request outcome identity."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_managed_control,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.app_protocol import (
    decode_mower_status_blob,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
)
from custom_components.dreame_lawn_mower.managed_mowing import ManagedMowing
from tests.components.dreame_lawn_mower.test_managed_mowing import (
    auto_enable_custom_integrations as auto_enable_custom_integrations,
)
from tests.components.dreame_lawn_mower.test_managed_mowing import (
    managed_runtime,
    request,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "substate,command,outcome",
    [
        (34, "pause", "paused"),
        (35, "pause", "paused"),
        (35, "dock", "docked"),
        (40, "pause", "docked"),
        (35, "resume", None),
        (33, "dock", None),
        (255, "pause", None),
    ],
)
async def test_owned_control_uses_decoded_active_and_resumable_states(
    substate, command, outcome
):
    blob = decode_mower_status_blob(
        [
            206,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            128,
            0,
            128,
            100,
            21,
            substate,
            0,
            0,
            128,
            211,
            196,
            206,
        ]
    )
    assert blob is not None
    client = SimpleNamespace(
        async_refresh_authoritative_snapshot=AsyncMock(
            return_value=SimpleNamespace(
                mission_task_id=11,
                online=True,
                mowing_session_active=blob.mowing_session_active,
                task_resumable=blob.task_resumable,
                paused=blob.task_status == "paused",
                error_code=0,
                returning=blob.task_status == "returning_to_dock",
                docked=False,
            )
        ),
        async_get_current_app_map_index=AsyncMock(return_value=2),
        _async_pause_mowing=AsyncMock(),
        _async_stop_then_dock=AsyncMock(),
    )
    kwargs = dict(expected_task_id=11, expected_map_index=2, admission=lambda: True)
    if outcome is None:
        with pytest.raises(DreameLawnMowerCommandRejectedError):
            await client_managed_control.async_control_owned_task(
                client, command, **kwargs
            )
        client._async_pause_mowing.assert_not_awaited()
        client._async_stop_then_dock.assert_not_awaited()
    else:
        assert (
            await client_managed_control.async_control_owned_task(
                client, command, **kwargs
            )
            == outcome
        )
        selected = (
            client._async_pause_mowing
            if outcome == "paused"
            else client._async_stop_then_dock
        )
        selected.assert_awaited_once()


@pytest.mark.asyncio
async def test_each_rejected_request_publishes_its_own_outcome(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        events = []
        unsubscribe = hass.bus.async_listen(
            "dreame_lawn_mower_scheduled_run", lambda event: events.append(event.data)
        )
        client.async_get_schedule_start_evidence = AsyncMock(
            side_effect=DreameLawnMowerConnectionError("Cloud unavailable")
        )
        for run_id in ("morning", "afternoon"):
            result = await owner.async_request(request(run_id=run_id, defer_minutes=60))
            assert result["reason"] == "eligibility_refresh_failed"
        await hass.async_block_till_done()
        unsubscribe()
        assert sorted(event["run_id"] for event in events) == ["afternoon", "morning"]
        assert len(owner.coordinator.scheduled_run_history.recent()) == 2
        assert client.commands == []


@pytest.mark.asyncio
async def test_overlap_outcome_keeps_request_identity_without_tick_chatter(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        events = []
        unsubscribe = hass.bus.async_listen(
            "dreame_lawn_mower_scheduled_run", lambda event: events.append(event.data)
        )
        hass.states.async_set("binary_sensor.lawn_permission", "off")
        await owner.async_request(
            request(
                permission_entity="binary_sensor.lawn_permission",
                defer_minutes=60,
                run_id="accepted",
            )
        )
        for run_id in ("overlap-one", "overlap-two"):
            result = await owner.async_request(request(run_id=run_id, defer_minutes=60))
            assert result["reason"] == "managed_run_in_progress"
        await owner._async_tick()
        await hass.async_block_till_done()
        unsubscribe()
        assert sorted(event["run_id"] for event in events) == [
            "accepted",
            "overlap-one",
            "overlap-two",
        ]
        assert owner.record["run_id"] == "accepted"
        assert client.commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", ["initial_request", "later_tick"])
async def test_unexpected_failure_retires_persisted_intent_and_run_id(
    tmp_path, failure_at
):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        params = request(
            permission_entity="binary_sensor.lawn_permission",
            defer_minutes=60,
            run_id="morning",
        )
        if failure_at == "later_tick":
            hass.states.async_set("binary_sensor.lawn_permission", "off")
            await owner.async_request(params)
            client.async_get_schedule_start_evidence = AsyncMock(
                side_effect=TypeError("Unexpected device response")
            )
            hass.states.async_set("binary_sensor.lawn_permission", "on")
            await hass.async_block_till_done()
        else:
            hass.states.async_set("binary_sensor.lawn_permission", "on")
            client.async_get_weather_protection = AsyncMock(
                side_effect=TypeError("Unexpected device response")
            )
            assert (await owner.async_request(params))["reason"] == "managed_run_failed"
        assert owner.record is None
        saved = await hass.async_add_executor_job(
            lambda: json.loads(
                Path(hass.config.path(".storage", owner._store.key)).read_text()
            )
        )
        assert saved["data"]["record"] is None
        assert saved["data"]["last_run_id"] == "morning"
        await owner.async_close()
        restored = ManagedMowing(owner.entity)
        await restored.async_start()
        try:
            await hass.async_block_till_done()
            assert restored.record is None
            assert (await restored.async_request(params))[
                "reason"
            ] == "duplicate_scheduled_run"
            assert client.commands == []
        finally:
            await restored.async_close()


@pytest.mark.asyncio
async def test_failed_revocation_write_retains_identity_and_retries_on_close(tmp_path):
    async with managed_runtime(tmp_path) as (owner, client, hass):
        hass.states.async_set("binary_sensor.lawn_permission", "off")
        params = request(
            permission_entity="binary_sensor.lawn_permission",
            defer_minutes=60,
            run_id="morning",
        )
        await owner.async_request(params)
        save = owner._store.async_save
        owner._store.async_save = AsyncMock(side_effect=OSError("Disk unavailable"))
        client.async_get_schedule_start_evidence = AsyncMock(
            side_effect=TypeError("Unexpected device response")
        )
        hass.states.async_set("binary_sensor.lawn_permission", "on")
        await hass.async_block_till_done()
        assert owner.record is None
        assert (await owner.async_request(params))[
            "reason"
        ] == "duplicate_scheduled_run"
        assert client.commands == []
        owner._store.async_save = save
        await owner.async_close()
        saved = await hass.async_add_executor_job(
            lambda: json.loads(
                Path(hass.config.path(".storage", owner._store.key)).read_text()
            )
        )
        assert saved["data"]["record"] is None
        assert saved["data"]["last_run_id"] == "morning"
