"""Person detection uses real HA event state and coordinator subscription lifetime."""

import logging
from dataclasses import replace
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.setup import async_setup_component

from custom_components.dreame_lawn_mower.coordinator import DreameLawnMowerCoordinator
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDevice,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerSnapshot,
    descriptor_from_cloud_record,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.notice_events import (
    MowerNoticeEventBuffer,
)
from custom_components.dreame_lawn_mower.event import (
    DreameLawnMowerPersonDetectionEvent,
    async_setup_entry,
)
from custom_components.dreame_lawn_mower.mower_condition_history import (
    MowerConditionHistory,
)


def _coordinator(hass, model="mova.mower.g2529b"):
    descriptor = descriptor_from_cloud_record(
        {"did": "test", "model": model, "name": "Mower"},
        account_type="mova",
        country="eu",
    )
    coordinator = DataUpdateCoordinator(
        hass, logging.getLogger(__name__), name="test", config_entry=None
    )
    coordinator.client = SimpleNamespace(descriptor=descriptor)
    coordinator.async_set_updated_data(
        DreameLawnMowerSnapshot(
            descriptor=descriptor,
            available=True,
            state="mowing",
            state_name="mowing",
            activity="mowing",
        )
    )
    return coordinator


@pytest.mark.parametrize(
    ("model", "expected"),
    [("mova.mower.g2529b", 1), ("dreame.mower.q2501a", 1), ("dreame.mower.p2255", 0)],
)
async def test_event_platform_only_creates_supported_models(hass, model, expected):
    entry = SimpleNamespace(entry_id="test", runtime_data=_coordinator(hass, model))
    entities = []
    await async_setup_entry(hass, entry, entities.extend)
    assert len(entities) == expected


async def test_queued_ha_update_survives_reconnect_before_snapshot(hass):
    coordinator = _coordinator(hass)
    buffer = MowerNoticeEventBuffer()
    history = MowerConditionHistory()
    component = EntityComponent(logging.getLogger(__name__), "event", hass)
    entity = DreameLawnMowerPersonDetectionEvent(coordinator)
    entity.entity_id = "event.mower_person_detection"
    await component.async_add_entities([entity])

    @callback
    def publish():
        snapshot = replace(
            coordinator.data,
            notification_events=buffer.events,
            status_notice_code=27,
            status_notice_display="Human detected",
            status_notice_tier="attention",
        )
        history.observe(snapshot)
        coordinator.async_set_updated_data(snapshot)

    bridge = SimpleNamespace(
        hass=hass, _shutting_down=False, _schedule_client_update=publish
    )
    device = SimpleNamespace(
        _ready=True,
        _state_lock=RLock(),
        realtime_properties={},
        last_realtime_message=None,
        _notice_events=buffer,
        schedule_update=Mock(),
    )
    try:
        buffer.record(received_at=100, message_id=11)
        DreameLawnMowerCoordinator._handle_client_update(bridge)
        # The HA loop has not yet read the newly received device occurrence.
        DreameMowerDevice._connected_callback(device)
        await hass.async_block_till_done()
        state = hass.states.get(entity.entity_id)
        assert state.attributes["event_type"] == "human_detected"
        assert state.attributes["observed_at"] == "1970-01-01T00:01:40+00:00"
        assert len(history.recent()) == 1
        publish()
        await hass.async_block_till_done()
        assert len(history.recent()) == 1
    finally:
        await component.async_remove_entity(entity.entity_id)


async def test_burst_emits_each_ha_state_once_and_unload_removes_listener(hass):
    coordinator = _coordinator(hass)
    buffer = MowerNoticeEventBuffer()
    buffer.record(received_at=100, message_id=1)
    coordinator.async_set_updated_data(
        replace(coordinator.data, notification_events=buffer.events)
    )
    component = EntityComponent(logging.getLogger(__name__), "event", hass)
    entity = DreameLawnMowerPersonDetectionEvent(coordinator)
    entity.entity_id = "event.mower_person_detection"
    await component.async_add_entities([entity])
    assert hass.states.get(entity.entity_id).state == "unknown"
    notifications = []
    @callback
    def notify(call):
        notifications.append(call.data["occurrence"])

    hass.services.async_register("test", "notify", notify)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "alias": "Person detection",
                "triggers": [
                    {
                        "trigger": "state",
                        "entity_id": entity.entity_id,
                        "attribute": "occurrence",
                    }
                ],
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": "{{ trigger.to_state is not none "
                        "and trigger.from_state is not none "
                        "and trigger.from_state.state != 'unavailable' "
                        "and trigger.to_state.state not in ['unknown', 'unavailable'] "
                        "and trigger.to_state.attributes.event_type "
                        "== 'human_detected' }}",
                    }
                ],
                "actions": [
                    {
                        "action": "test.notify",
                        "data": {
                            "occurrence": "{{ trigger.to_state.attributes.occurrence }}"
                        },
                    }
                ],
                "mode": "queued",
            }
        },
    )
    await hass.async_block_till_done()
    observed = []

    @callback
    def capture(event):
        if event.data["entity_id"] == entity.entity_id:
            observed.append(event.data["new_state"])

    unsubscribe = hass.bus.async_listen(EVENT_STATE_CHANGED, capture)
    try:
        for message_id in (2, 3, 4):
            buffer.record(received_at=100 + message_id, message_id=message_id)
        snapshot = replace(coordinator.data, notification_events=buffer.events)
        coordinator.async_set_updated_data(snapshot)
        await hass.async_block_till_done()
        assert len(observed) == 3
        assert [state.attributes["observed_at"] for state in observed] == [
            "1970-01-01T00:01:42+00:00",
            "1970-01-01T00:01:43+00:00",
            "1970-01-01T00:01:44+00:00",
        ]
        assert all(
            state.attributes["event_type"] == "human_detected" for state in observed
        )
        assert len(notifications) == 3
        coordinator.async_set_updated_data(snapshot)
        await hass.async_block_till_done()
        assert len(observed) == 3
        assert len(notifications) == 3

        coordinator.async_set_updated_data(replace(snapshot, available=False))
        await hass.async_block_till_done()
        assert hass.states.get(entity.entity_id).state == "unavailable"
        buffer.reset_connection()
        coordinator.async_set_updated_data(
            replace(snapshot, notification_events=buffer.events)
        )
        await hass.async_block_till_done()
        assert observed[-1].attributes["occurrence"].endswith(":4")
        assert len(notifications) == 3
        coordinator.async_set_updated_data(replace(snapshot, available=False))
        await hass.async_block_till_done()
        buffer.record(received_at=105, message_id=5)
        coordinator.async_set_updated_data(
            replace(snapshot, notification_events=buffer.events)
        )
        await hass.async_block_till_done()
        assert len(notifications) == 4
        assert notifications[-1].endswith(":5")
        await component.async_remove_entity(entity.entity_id)
        assert not coordinator._listeners
        assert component.get_entity(entity.entity_id) is None
        assert hass.states.get(entity.entity_id).state == "unavailable"
        replacement = DreameLawnMowerPersonDetectionEvent(coordinator)
        replacement.entity_id = entity.entity_id
        await component.async_add_entities([replacement])
        coordinator.async_set_updated_data(coordinator.data)
        await hass.async_block_till_done()
        assert len(notifications) == 4
    finally:
        unsubscribe()
        if component.get_entity(entity.entity_id) is not None:
            await component.async_remove_entity(entity.entity_id)
