"""Heartbeat link readings reach normal HA diagnostic measurements."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.app_protocol import (
    decode_mower_status_blob,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.link_signals import (
    decode_link_signals,
)
from custom_components.dreame_lawn_mower.sensor import SENSORS, DreameLawnMowerSensor
from dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
    snapshot_from_device,
)

from .test_dreame_models import _FakeDevice


def heartbeat(length=20, *, wifi=192, cellular=180):
    raw = [0] * length
    raw[0] = raw[-1] = 0xCE
    raw[17], raw[18] = wifi, cellular
    return raw


@pytest.mark.parametrize("length", [20, 22])
def test_signal_readings_do_not_require_new_task_state_semantics(length):
    packet = heartbeat(length)
    blob = decode_mower_status_blob(packet, property_key="1.1")
    assert blob.wifi_signal_dbm == -64
    assert blob.cellular_signal_dbm == -76
    assert blob.cellular_module_installed is True
    assert blob.cellular_plan_valid is True
    if length == 22:
        assert blob.task_status is None
        assert blob.heartbeat_docked is None


@pytest.mark.parametrize("property_key", [None, "1.4", "2.1"])
def test_same_sized_runtime_and_unknown_properties_have_no_signal(property_key):
    blob = decode_mower_status_blob(heartbeat(22), property_key=property_key)
    assert blob.wifi_signal_dbm is None
    assert blob.cellular_signal_dbm is None
    assert blob.cellular_module_installed is None


@pytest.mark.parametrize("raw,installed,valid", [
    (0, True, False), (127, False, None), (128, True, True),
])
def test_placeholders_and_module_state_never_become_zero_dbm(raw, installed, valid):
    signal = decode_link_signals(heartbeat(wifi=raw, cellular=raw), property_key="1.1")
    assert signal.wifi_signal_dbm is None
    assert signal.cellular_signal_dbm is None
    assert signal.cellular_module_installed is installed
    assert signal.cellular_plan_valid is valid


@pytest.mark.parametrize("bad", [
    None, [], heartbeat(24), [0] * 20,
    [True, *heartbeat()[1:]], [*heartbeat()[:-1], 255],
    [*heartbeat()[:17], 256, *heartbeat()[18:]],
])
def test_unvalidated_heartbeat_cannot_publish_link_measurements(bad):
    signal = decode_link_signals(bad, property_key="1.1")
    assert signal.wifi_signal_dbm is None
    assert signal.cellular_signal_dbm is None
    assert signal.cellular_module_installed is None


@pytest.mark.parametrize("property_key", ["1.1", "1.4"])
@pytest.mark.parametrize("encoded", [False, True])
def test_wire_readings_reach_snapshot_and_diagnostic_sensor(property_key, encoded):
    descriptor = DreameLawnMowerDescriptor(
        did="test", name="Garden", model="dreame.mower.g2408",
        display_model="A2", account_type="dreame", country="eu",
    )
    device = _FakeDevice()
    device.realtime_properties[property_key] = {
        "value": json.dumps(heartbeat()) if encoded else heartbeat(),
    }
    snapshot = snapshot_from_device(descriptor, device)
    coordinator = SimpleNamespace(
        data=snapshot, client=SimpleNamespace(descriptor=descriptor),
    )
    descriptions = {item.key: item for item in SENSORS}
    for key, expected in (("wifi_signal_dbm", -64), ("cellular_signal_dbm", -76)):
        entity = DreameLawnMowerSensor(coordinator, descriptions[key])
        assert entity.device_class == SensorDeviceClass.SIGNAL_STRENGTH
        assert entity.state_class == SensorStateClass.MEASUREMENT
        assert entity.native_unit_of_measurement == "dBm"
        if property_key == "1.1":
            assert entity.available
            assert entity.native_value == expected
            coordinator.data = replace(snapshot, available=False)
            assert not entity.available
            coordinator.data = snapshot
        else:
            assert not entity.available
            assert entity.native_value is None
