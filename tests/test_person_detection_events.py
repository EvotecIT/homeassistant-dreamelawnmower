"""Realtime occurrence contracts independent of the current error property."""

from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDevice,
    DreameMowerDeviceStatus,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.notice_events import (
    NOTICE_EVENT_LIMIT,
    MowerNoticeEventBuffer,
    MowerNoticeEventCursor,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.types import (
    DreameMowerProperty,
    DreameMowerState,
)
from custom_components.dreame_lawn_mower.mower_condition_history import (
    MowerConditionHistory,
)


def _device(model="mova.mower.g2529b", state=DreameMowerState.MOWING):
    device = object.__new__(DreameMowerDevice)
    device.data = {
        DreameMowerProperty.ERROR.value: 27,
        DreameMowerProperty.STATE.value: state.value,
    }
    device.unknown_properties = {}
    device.realtime_properties = {}
    device.last_realtime_message = None
    device._state_lock = RLock()
    device._dirty_data = {}
    device._property_update_callback = {}
    device._ready = True
    device._last_change = 0
    device._default_properties = [DreameMowerProperty.ERROR, DreameMowerProperty.STATE]
    device._map_manager = None
    device.available = True
    device.info = SimpleNamespace(model=model)
    device.capability = SimpleNamespace(new_state=True)
    device.status = DreameMowerDeviceStatus(device)
    device._property_changed = Mock()
    device.schedule_update = Mock()
    return device


def _message(message_id=None, *, state=None, code=0):
    message = {
        "method": "properties_changed",
        "params": [{"siid": 2, "piid": 2, "value": 27, "code": code}],
    }
    if message_id is not None:
        message["id"] = message_id
    if state is not None:
        # Error comes first: classification must use the completed batch state.
        message["params"].append({"siid": 2, "piid": 1, "value": state.value})
    return message


@pytest.mark.parametrize("model", ["mova.mower.g2529b", "dreame.mower.q2501a"])
def test_repeated_mqtt_notices_update_consumers_without_property_change(model):
    device = _device(model)
    with patch("time.time", side_effect=[100.0, 200.0, 300.0]):
        device._message_callback(_message(11))
        device._message_callback(_message(12))
        device._message_callback(_message(12))

    assert device.data[DreameMowerProperty.ERROR.value] == 27
    assert device.realtime_properties["2.2"]["last_seen"] == 300.0
    assert device.realtime_properties["2.2"]["changed_at"] == 100.0
    assert [event.received_at for event in device._notice_events.events] == [100, 200]
    assert device._property_changed.call_count == 2


def test_batch_classification_uses_new_mowing_state_and_preserves_failed_code():
    device = _device(state=DreameMowerState.PAUSED)
    published = []
    device._property_changed.side_effect = lambda: published.append(
        tuple(getattr(device, "_notice_events", MowerNoticeEventBuffer()).events)
    )
    device._message_callback(_message(11, state=DreameMowerState.MOWING))
    assert len(device._notice_events.events) == 1
    assert published == [device._notice_events.events]
    device._message_callback(_message(12, code=-1))
    assert len(device._notice_events.events) == 1
    device._message_callback(_message(13, state=DreameMowerState.PAUSED))
    assert len(device._notice_events.events) == 1


def test_unknown_model_does_not_announce_a_person_detection_event():
    device = _device("dreame.mower.x1234")
    device._message_callback(_message(11))
    assert not getattr(device, "_notice_events", None)
    device._property_changed.assert_not_called()


def test_reconnect_allows_vendor_id_reuse_without_replaying_consumed_events():
    device = _device()
    cursor = MowerNoticeEventCursor()
    device._message_callback(_message(11))
    assert len(cursor.new_events(device._notice_events.events)) == 1
    device._connected_callback()
    assert cursor.new_events(device._notice_events.events) == ()
    device._message_callback(_message(11))
    assert len(cursor.new_events(device._notice_events.events)) == 1
    device.schedule_update.assert_called_once_with(2, True)


def test_bounded_burst_has_independent_readers_and_receipt_fallback():
    buffer = MowerNoticeEventBuffer()
    first, second = MowerNoticeEventCursor(), MowerNoticeEventCursor()
    for index in range(NOTICE_EVENT_LIMIT + 2):
        assert buffer.record(received_at=float(index))
    events = buffer.events
    assert len(events) == NOTICE_EVENT_LIMIT
    assert events[0].received_at == 2
    assert first.new_events(events) == events
    assert first.new_events(events) == ()
    assert second.new_events(events) == events
    # Two separate unidentified receipts may share a timestamp.
    assert buffer.record(received_at=events[-1].received_at)

    replacement = MowerNoticeEventBuffer()
    replacement.record(received_at=200, message_id=1)
    assert first.new_events(replacement.events) == replacement.events


def test_packet_without_identity_records_once_even_with_duplicate_properties():
    device = _device()
    message = _message()
    message["params"] *= 2
    with patch("time.time", return_value=100.0):
        device._message_callback(message)
        device._message_callback(_message())
    assert len(device._notice_events.events) == 2
    assert device._property_changed.call_count == 2


def test_history_keeps_burst_occurrences_at_same_time_without_polling_repeats():
    buffer = MowerNoticeEventBuffer()
    for message_id in (11, 12, 13):
        buffer.record(received_at=100, message_id=message_id)
    snapshot = SimpleNamespace(
        notification_events=buffer.events,
        status_notice_code=27,
        status_notice_display="Human detected",
        status_notice_tier="attention",
        status_notice_source="status",
    )
    history = MowerConditionHistory()
    assert history.observe(snapshot)
    assert len(history.recent()) == 3
    assert not history.observe(snapshot)
    assert len(history.recent()) == 3
    assert history.latest()["observed_at"] == "1970-01-01T00:01:40+00:00"


def test_history_reconnect_preserves_condition_until_fresh_notice_or_clear():
    device = _device()
    history = MowerConditionHistory()

    def snapshot(available=True, *, warning=True):
        return SimpleNamespace(
            available=available,
            notification_events=device._notice_events.events,
            status_notice_code=27 if warning else None,
            status_notice_display="Human detected" if warning else None,
            status_notice_tier="attention" if warning else None,
            status_notice_source="status",
        )

    with patch("time.time", return_value=100.0):
        device._message_callback(_message(11))
    assert history.observe(snapshot())
    original = history.recent()
    assert not history.observe(snapshot(available=False))
    device._connected_callback()
    assert not history.observe(snapshot())
    assert history.recent() == original

    with patch("time.time", return_value=200.0):
        device._message_callback(_message(11))
    assert history.observe(snapshot())
    assert len(history.recent()) == 2
    assert history.latest()["observed_at"] == "1970-01-01T00:03:20+00:00"
    assert not history.observe(snapshot())

    device._connected_callback()
    assert not history.observe(snapshot(warning=False))
    assert history.observe(snapshot())
    assert len(history.recent()) == 3
