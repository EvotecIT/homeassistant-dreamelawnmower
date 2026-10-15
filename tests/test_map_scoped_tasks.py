"""Map-scoped movement keeps its selected map across the task queue."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.dreame_lawn_mower.button import (
    DreameLawnMowerGoToMaintenancePointButton,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerDescriptor,
)
from custom_components.dreame_lawn_mower.lawn_mower import DreameLawnMower
from custom_components.dreame_lawn_mower.scheduled_mowing import (
    async_start_scheduled_mowing,
)

from .test_current_map_controls import (
    _app_maps,
    _batch_device_data,
    _vector_map_details,
)
from .test_maintenance_point_controls import _complete_fresh_map_refresh, _coordinator
from .test_scheduled_mowing import eligible_evidence
from .test_targeted_task_confirmation import _snapshot

_TARGETS = [
    ("zone", [1]),
    ("spot", [2]),
    ("edge", [[1, 0]]),
    ("maintenance", 301),
]


def _client():
    return DreameLawnMowerClient(
        username="user@example.invalid",
        password="secret",
        country="eu",
        account_type="dreame",
        descriptor=DreameLawnMowerDescriptor(
            did="42",
            name="Garden",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
    )


@pytest.mark.parametrize(("kind", "targets"), _TARGETS)
@pytest.mark.parametrize("surface", ["ha", "client"])
def test_map_scoped_command_rejects_map_switch_while_queued(kind, targets, surface):
    async def scenario():
        client = _client()
        current_index = 0

        async def map_index():
            return current_index

        client.async_get_current_app_map_index = AsyncMock(side_effect=map_index)
        client.async_refresh_authoritative_snapshot = AsyncMock(
            return_value=_snapshot()
        )
        client._async_refresh_authoritative_snapshot = AsyncMock(
            return_value=_snapshot()
        )
        client._async_call_mowing_task = AsyncMock(return_value={"r": 0})
        client._async_require_targeted_task_confirmation = AsyncMock()
        if kind == "maintenance":
            coordinator = _coordinator(activity="docked")
            _complete_fresh_map_refresh(coordinator)
            entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
            command_name = "async_go_to_maintenance_point"
        else:
            coordinator = SimpleNamespace(
                app_maps=_app_maps(),
                batch_device_data=_batch_device_data(),
                vector_map_details=_vector_map_details(),
                selected_map_index=0,
                selected_mowing_action="all_area",
                selected_zone_id=1,
                selected_spot_id=2,
                selected_contour_id=(1, 0),
                async_request_refresh=AsyncMock(),
            )
            entity = object.__new__(DreameLawnMower)
            command_name = f"async_start_{kind}_mowing"
        coordinator.client = client
        entity.coordinator = coordinator
        entered = asyncio.Event()

        async def read(operation):
            entered.set()
            return await operation(None)

        client._async_cloud_read = read
        await client._task_control_gate.acquire()
        if surface == "client":
            command = getattr(client, command_name)(targets, expected_map_index=0)
        elif kind == "maintenance":
            command = entity.async_press()
        else:
            command = getattr(entity, command_name)(targets)
        pending = asyncio.create_task(command)
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert not pending.done()
            current_index = 1
            client._task_control_gate.release()
            error = (
                HomeAssistantError
                if surface == "ha"
                else DreameLawnMowerCommandRejectedError
            )
            with pytest.raises(error, match="map changed"):
                await pending
            client._async_call_mowing_task.assert_not_awaited()
            coordinator.async_request_refresh.assert_not_awaited()
        finally:
            if client._task_control_gate.locked():
                client._task_control_gate.release()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize(("kind", "targets"), _TARGETS)
def test_unbound_direct_movement_rejects_map_switch_admission_backlog(kind, targets):
    async def scenario():
        client = _client()
        switching, release_switch, cancel_entered, movement_entered = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        current_index = 0
        actions = []

        async def dispatch(action, **kwargs):
            nonlocal current_index
            actions.append(action["o"])
            if action["o"] == 200:
                switching.set()
                await release_switch.wait()
                current_index = 1
            return {"r": 0}

        async def read(operation):
            task_name = asyncio.current_task().get_name()
            if task_name == "queued-cancel":
                cancel_entered.set()
            elif task_name == "queued-movement":
                movement_entered.set()
            return await operation(None)

        client._async_cloud_read = read
        client.async_get_current_app_map_index = AsyncMock(
            side_effect=lambda: current_index
        )
        client.async_refresh_authoritative_snapshot = AsyncMock(
            return_value=_snapshot()
        )
        client._async_refresh_authoritative_snapshot = AsyncMock(
            return_value=_snapshot()
        )
        client._async_call_mowing_task = AsyncMock(side_effect=dispatch)
        client._async_require_targeted_task_confirmation = AsyncMock()
        switch = asyncio.create_task(client.async_switch_current_map(1))
        cancel = movement = None
        try:
            await asyncio.wait_for(switching.wait(), 2)
            cancel = asyncio.create_task(
                client.async_cancel_current_task(),
                name="queued-cancel",
            )
            await asyncio.wait_for(cancel_entered.wait(), 2)
            assert client._task_control_admission_gate.locked()
            method = (
                "async_go_to_maintenance_point"
                if kind == "maintenance"
                else f"async_start_{kind}_mowing"
            )
            movement = asyncio.create_task(
                getattr(client, method)(targets),
                name="queued-movement",
            )
            await asyncio.wait_for(movement_entered.wait(), 2)
            client.async_get_current_app_map_index.assert_not_awaited()
            release_switch.set()
            await asyncio.wait_for(switch, 2)
            await asyncio.wait_for(cancel, 2)
            with pytest.raises(
                DreameLawnMowerCommandRejectedError,
                match="expected_map_index",
            ):
                await asyncio.wait_for(movement, 2)
            assert actions == [200]
        finally:
            release_switch.set()
            pending = [task for task in [switch, cancel, movement] if task is not None]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize(("kind", "targets"), _TARGETS)
def test_direct_map_scoped_command_requires_known_map_before_queueing(kind, targets):
    async def scenario():
        client = _client()
        client.async_get_current_app_map_index = AsyncMock(return_value=None)
        client._async_cloud_read = lambda operation: operation(None)
        client._async_call_mowing_task = AsyncMock()
        try:
            method = (
                "async_go_to_maintenance_point"
                if kind == "maintenance"
                else f"async_start_{kind}_mowing"
            )
            with pytest.raises(DreameLawnMowerCommandRejectedError, match="map index"):
                await getattr(client, method)(targets)
            client._async_call_mowing_task.assert_not_awaited()
            assert not client._task_control_gate.locked()
        finally:
            await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize(("current_index", "selected_index"), [(None, 0), (0, 1)])
def test_maintenance_point_requires_known_matching_selected_map(
    current_index,
    selected_index,
):
    coordinator = _coordinator(activity="docked")
    _complete_fresh_map_refresh(coordinator)
    coordinator.app_maps["current_map_index"] = current_index
    coordinator.selected_map_index = selected_index
    entity = object.__new__(DreameLawnMowerGoToMaintenancePointButton)
    entity.coordinator = coordinator

    with pytest.raises(ValueError, match="verified active map"):
        asyncio.run(entity.async_press())
    coordinator.client.async_go_to_maintenance_point.assert_not_awaited()


@pytest.mark.parametrize(("kind", "targets"), _TARGETS)
@pytest.mark.parametrize("later_method", ["async_cancel_current_task", "async_dock"])
def test_later_control_waits_for_earlier_movement_map_capture(
    kind,
    targets,
    later_method,
):
    async def scenario():
        client = _client()
        reading_map, release_map, later_entered = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )

        async def map_index():
            reading_map.set()
            await release_map.wait()
            return 0

        async def read(operation):
            if asyncio.current_task().get_name() == "later-control":
                later_entered.set()
            return await operation(None)

        client.async_get_current_app_map_index = AsyncMock(side_effect=map_index)
        client.async_refresh_authoritative_snapshot = AsyncMock(
            return_value=_snapshot()
        )
        client._async_refresh_authoritative_snapshot = AsyncMock(
            return_value=_snapshot()
        )
        client._async_call_mowing_task = AsyncMock(return_value={"r": 0})
        client._async_require_targeted_task_confirmation = AsyncMock()
        client._async_stop_then_dock = AsyncMock()
        client._async_cloud_read = read
        method = (
            "async_go_to_maintenance_point"
            if kind == "maintenance"
            else f"async_start_{kind}_mowing"
        )
        earlier = asyncio.create_task(getattr(client, method)(targets))
        later = None
        try:
            await asyncio.wait_for(reading_map.wait(), 2)
            later = asyncio.create_task(
                getattr(client, later_method)(),
                name="later-control",
            )
            await asyncio.wait_for(later_entered.wait(), 2)
            assert not later.done(), "Later control overtook earlier movement"
            release_map.set()
            await asyncio.wait_for(earlier, 2)
            await asyncio.wait_for(later, 2)
            client._async_call_mowing_task.assert_awaited_once()
        finally:
            release_map.set()
            earlier.cancel()
            if later is not None:
                later.cancel()
            await asyncio.gather(
                earlier,
                *([later] if later is not None else []),
                return_exceptions=True,
            )
            await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize(("kind", "targets"), _TARGETS[:3])
def test_scheduled_targets_retain_requested_map_during_eligibility(kind, targets):
    async def scenario():
        client = _client()
        entity = object.__new__(DreameLawnMower)
        entity.entity_id = "lawn_mower.garden"
        entity.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))
        coordinator = SimpleNamespace(
            client=client,
            app_maps=_app_maps(),
            batch_device_data=_batch_device_data(),
            vector_map_details=_vector_map_details(),
            selected_map_index=None,
            selected_mowing_action="all_area",
            async_request_refresh=AsyncMock(),
            async_update_listeners=Mock(),
        )
        entity.coordinator = coordinator
        snapshot, weather, native = eligible_evidence()
        native.update(current_map_index=0, map_indices=[0, 1])
        native["schedules"][0]["idx"] = 0
        native["schedules"].append({**native["schedules"][0], "idx": 1})

        async def weather_after_switch(**kwargs):
            coordinator.app_maps["current_map_index"] = 1
            for payload, key, index_key in [
                (coordinator.app_maps, "maps", "idx"),
                (
                    coordinator.batch_device_data["batch_mowing_preferences"],
                    "maps",
                    "idx",
                ),
                (coordinator.vector_map_details, "maps", "map_index"),
            ]:
                payload[key] = [payload[key][0]]
                payload[key][0][index_key] = 1
            for entry in coordinator.batch_device_data["batch_mowing_preferences"][
                "maps"
            ][0]["preferences"]:
                entry["map_index"] = 1
            return weather

        client.async_get_schedule_start_evidence = AsyncMock(return_value=native)
        client.async_get_weather_protection = AsyncMock(
            side_effect=weather_after_switch
        )
        client.async_refresh_authoritative_snapshot = AsyncMock(return_value=snapshot)
        client._async_refresh_authoritative_snapshot = AsyncMock(
            return_value=_snapshot(docked=True, task_resumable=False)
        )
        client.async_get_current_app_map_index = AsyncMock(return_value=1)
        client._async_call_mowing_task = AsyncMock(return_value={"r": 0})
        client._async_require_targeted_task_confirmation = AsyncMock()
        client._async_cloud_read = lambda operation: operation(None)
        field = {"zone": "zone_ids", "spot": "spot_ids", "edge": "contour_ids"}[kind]
        try:
            result = await async_start_scheduled_mowing(
                entity,
                task_type=kind,
                map_index=0,
                **{field: targets},
            )
            assert result["status"] == "failed", result
            assert result["map_index"] == 0
            client._async_call_mowing_task.assert_not_awaited()
        finally:
            await client.async_close()

    asyncio.run(scenario())
