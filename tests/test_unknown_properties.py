"""Regression tests for unknown mower property handling."""

from __future__ import annotations

from threading import RLock
from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDevice,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.types import (
    DreameMowerProperty,
)


def _device_stub() -> tuple[DreameMowerDevice, list[str]]:
    device = object.__new__(DreameMowerDevice)
    updates: list[str] = []
    device.data = {}
    device.unknown_properties = {}
    device.realtime_properties = {}
    device.last_realtime_message = None
    device._state_lock = RLock()
    device._dirty_data = {}
    device._property_update_callback = {}
    device._ready = True
    device._last_change = 0
    device._default_properties = [DreameMowerProperty.BATTERY_LEVEL]
    device._map_manager = None
    device.available = False
    device.capability = SimpleNamespace()
    device.status = SimpleNamespace()
    device._property_changed = lambda: updates.append("changed")
    return device, updates


def test_handle_properties_tolerates_unknown_property_ids() -> None:
    device, updates = _device_stub()
    unknown_did = -113852866
    battery_did = DreameMowerProperty.BATTERY_LEVEL.value

    changed = DreameMowerDevice._handle_properties(
        device,
        [
            {
                "did": str(unknown_did),
                "code": 0,
                "value": 123,
                "siid": 9,
                "piid": 4,
            },
            {
                "did": str(battery_did),
                "code": 0,
                "value": 80,
            },
        ],
    )

    assert changed is True
    assert updates == ["changed"]
    assert device.data[unknown_did] == 123
    assert device.data[battery_did] == 80
    assert device.unknown_properties[unknown_did] == {
        "did": unknown_did,
        "code": 0,
        "siid": 9,
        "piid": 4,
        "value": 123,
        "last_seen": device.unknown_properties[unknown_did]["last_seen"],
    }


def test_handle_properties_tolerates_empty_cloud_response() -> None:
    device, updates = _device_stub()

    assert DreameMowerDevice._handle_properties(device, None) is False
    assert DreameMowerDevice._handle_properties(device, {"code": 0}) is False
    assert updates == []


def test_handle_properties_matches_model_specific_did_by_siid_piid() -> None:
    device, updates = _device_stub()
    model_specific_did = -115364054

    changed = DreameMowerDevice._handle_properties(
        device,
        [
            {
                "did": str(model_specific_did),
                "code": 0,
                "value": 2,
                "siid": 3,
                "piid": 2,
            },
            {
                "did": "-115364055",
                "code": 0,
                "value": 5,
                "siid": 2,
                "piid": 1,
            },
        ],
    )

    assert changed is True
    assert updates == ["changed"]
    assert device.data[DreameMowerProperty.CHARGING_STATUS.value] == 2
    assert device.data[DreameMowerProperty.STATE.value] == 5
    assert model_specific_did not in device.data
    assert device.unknown_properties == {}


def test_tracks_unavailable_unknown_properties_for_diagnostics() -> None:
    device, updates = _device_stub()
    unknown_did = -115545820

    changed = DreameMowerDevice._handle_properties(
        device,
        [
            {
                "did": str(unknown_did),
                "code": -1,
                "siid": 3,
                "piid": 7,
            }
        ],
    )

    assert changed is False
    assert updates == []
    assert device.unknown_properties[unknown_did]["code"] == -1
    assert device.unknown_properties[unknown_did]["siid"] == 3
    assert device.unknown_properties[unknown_did]["piid"] == 7


def test_message_callback_tracks_realtime_properties_and_unmapped_pairs() -> None:
    device, updates = _device_stub()

    DreameMowerDevice._message_callback(
        device,
        {
            "method": "properties_changed",
            "params": [
                {"siid": 3, "piid": 1, "value": 80},
                {"siid": 1, "piid": 4, "value": [206, 0, 206]},
                {
                    "siid": 2,
                    "piid": 50,
                    "value": '{"d":{"exe":true,"o":6,"status":true},"t":"TASK"}',
                },
                {"siid": 9, "piid": 4, "value": {"blob": 123}},
            ],
        },
    )

    assert device.available is True
    assert updates == ["changed"]
    assert device.data[DreameMowerProperty.BATTERY_LEVEL.value] == 80
    assert device.realtime_properties["3.1"]["property_name"] == "BATTERY_LEVEL"
    assert device.realtime_properties["3.1"]["did"] == str(
        DreameMowerProperty.BATTERY_LEVEL.value
    )
    assert device.realtime_properties["1.4"]["property_name"] == "runtime_status_blob"
    assert device.realtime_properties["1.4"]["value"] == [206, 0, 206]
    assert device.realtime_properties["2.50"]["property_name"] == "task_status"
    assert device.realtime_properties["9.4"]["property_name"] == "UNKNOWN_REALTIME_9.4"
    assert device.realtime_properties["9.4"]["value"] == {"blob": 123}
    assert device.last_realtime_message is not None
    assert device.last_realtime_message["message"]["method"] == "properties_changed"
    assert (
        len({entry["last_seen"] for entry in device.realtime_properties.values()}) == 1
    )


@pytest.mark.parametrize("piid", [4, 53])
def test_message_callback_publishes_external_realtime_property_changes(
    piid: int,
) -> None:
    device, updates = _device_stub()

    DreameMowerDevice._message_callback(
        device,
        {
            "method": "properties_changed",
            "params": [{"siid": 1, "piid": piid, "value": [206, 1, 206]}],
        },
    )
    DreameMowerDevice._message_callback(
        device,
        {
            "method": "properties_changed",
            "params": [{"siid": 1, "piid": piid, "value": [206, 2, 206]}],
        },
    )
    DreameMowerDevice._message_callback(
        device,
        {
            "method": "properties_changed",
            "params": [{"siid": 1, "piid": piid, "value": [206, 2, 206]}],
        },
    )

    assert updates == ["changed", "changed"]


def test_message_callback_publishes_task_region_changes() -> None:
    device, updates = _device_stub()
    first_task = '{"d":{"exe":true,"region_id":[1],"status":true},"t":"TASK"}'
    second_task = (
        '{"d":{"exe":true,"region_id":[1,2],"status":true},"t":"TASK"}'
    )

    for task in (first_task, second_task, second_task):
        DreameMowerDevice._message_callback(
            device,
            {
                "method": "properties_changed",
                "params": [{"siid": 2, "piid": 50, "value": task}],
            },
        )

    assert updates == ["changed", "changed"]


@pytest.mark.parametrize("piid", [51, 52])
def test_message_callback_publishes_each_settings_announcement(piid: int) -> None:
    device, updates = _device_stub()
    message = {
        "method": "properties_changed",
        "params": [{"siid": 2, "piid": piid, "value": {}}],
    }

    DreameMowerDevice._message_callback(device, message)
    DreameMowerDevice._message_callback(device, message)

    assert updates == ["changed", "changed"]
    assert device.realtime_properties[f"2.{piid}"]["value"] == {}


def test_message_callback_does_not_publish_raw_heartbeat_as_pose() -> None:
    device, updates = _device_stub()

    DreameMowerDevice._message_callback(
        device,
        {
            "method": "properties_changed",
            "params": [
                {
                    "siid": 1,
                    "piid": 1,
                    "value": [
                        206,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                        0,
                        202,
                        69,
                        36,
                        0,
                        4,
                        128,
                        167,
                        126,
                        0,
                        128,
                        206,
                    ],
                }
            ],
        },
    )

    assert updates == []


def test_message_callback_does_not_publish_unmapped_diagnostic_changes() -> None:
    device, updates = _device_stub()

    DreameMowerDevice._message_callback(
        device,
        {
            "method": "properties_changed",
            "params": [{"siid": 9, "piid": 4, "value": {"blob": 123}}],
        },
    )

    assert updates == []


def test_message_callback_applies_known_and_realtime_state_under_one_lock() -> None:
    device, _updates = _device_stub()
    original_handle_properties = device._handle_properties
    lock_owned_during_update = False

    def handle_properties(properties: list[dict[str, object]]) -> bool:
        nonlocal lock_owned_during_update
        lock_owned_during_update = device._state_lock._is_owned()
        assert "2.1" in device.realtime_properties
        assert "2.2" in device.realtime_properties
        return original_handle_properties(properties)

    device._handle_properties = handle_properties

    DreameMowerDevice._message_callback(
        device,
        {
            "method": "properties_changed",
            "params": [
                {"siid": 2, "piid": 1, "value": 1},
                {"siid": 2, "piid": 2, "value": 0},
            ],
        },
    )

    assert lock_owned_during_update is True
