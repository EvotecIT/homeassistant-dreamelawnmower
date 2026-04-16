"""Regression tests for unknown mower property handling."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.dreame_lawn_mower.dreame_client.device import DreameMowerDevice
from custom_components.dreame_lawn_mower.dreame_client.types import DreameMowerProperty


def _device_stub() -> tuple[DreameMowerDevice, list[str]]:
    device = object.__new__(DreameMowerDevice)
    updates: list[str] = []
    device.data = {}
    device.unknown_properties = {}
    device._dirty_data = {}
    device._property_update_callback = {}
    device._ready = True
    device._last_change = 0
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
