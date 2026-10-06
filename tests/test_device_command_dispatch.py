"""Named command dispatch preserves validation and enum routing."""

from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import device_commands
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDevice,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerAction,
    DreameMowerAutoSwitchProperty,
    DreameMowerProperty,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    InvalidActionException,
)


@pytest.fixture
def device(monkeypatch):
    mower = DreameMowerDevice("Dispatch", None, " ")
    monkeypatch.setattr(
        DreameMowerDevice, "device_connected", property(lambda self: True)
    )
    mower.data[DreameMowerProperty.VOLUME.value] = 20
    mower.set_property = Mock(return_value=True)
    mower.call_action = Mock(return_value={"code": 0})
    try:
        yield mower
    finally:
        mower.disconnect()


@pytest.mark.parametrize("value,expected", [("0", 0), ("100", 100), (True, 1)])
def test_numeric_property_dispatch_preserves_bounds_and_conversion(
    device, value, expected
):
    device.set_property_value("volume", value)
    device.set_property.assert_called_once_with(DreameMowerProperty.VOLUME, expected)


@pytest.mark.parametrize("value", [-1, 101, "invalid"])
def test_invalid_volume_never_reaches_command(device, value):
    with pytest.raises(InvalidActionException, match="Invalid value"):
        device.set_property_value("volume", value)
    device.set_property.assert_not_called()


def test_string_enum_dispatch_uses_auto_switch_cache(device):
    prop = DreameMowerAutoSwitchProperty.AUTO_CHARGING
    device.capability.auto_switch_settings = True
    device.auto_switch_data = {prop.name: 0}
    device.set_auto_charging = None
    device.set_property_value(prop.name, True)
    device.set_property.assert_called_once_with(prop, 1)


def test_read_only_property_cannot_reach_a_named_setter(device):
    device.set_battery_level = Mock()
    with pytest.raises(InvalidActionException, match="Invalid property"):
        device.set_property_value("battery_level", 50)
    device.set_battery_level.assert_not_called()


def test_disconnected_device_rejects_named_action(device, monkeypatch):
    monkeypatch.setattr(
        DreameMowerDevice, "device_connected", property(lambda self: False)
    )
    device.start = Mock()
    with pytest.raises(InvalidActionException, match="Device unavailable"):
        device.call_action_value("start")
    device.start.assert_not_called()


def test_named_action_returns_its_result(device):
    device.start = Mock(return_value={"code": 0})
    assert device.call_action_value("start") == {"code": 0}
    device.start.assert_called_once_with()
    device.call_action.assert_not_called()


def test_enum_action_dispatch_preserves_protocol_result_check(device):
    device.call_action_value("STOP")
    device.call_action.assert_called_once_with(DreameMowerAction.STOP)
    device.call_action.return_value = {"code": -1}
    with pytest.raises(InvalidActionException, match="Unable to call action"):
        device.call_action_value("STOP")


def test_availability_rejection_prevents_property_and_action_commands(
    device, monkeypatch
):
    monkeypatch.setitem(
        device_commands.PROPERTY_AVAILABILITY, "VOLUME", lambda _: False
    )
    monkeypatch.setitem(device_commands.ACTION_AVAILABILITY, "STOP", lambda _: False)
    with pytest.raises(InvalidActionException, match="Property unavailable"):
        device.set_property_value("volume", 50)
    with pytest.raises(InvalidActionException, match="Action unavailable"):
        device.call_action_value("STOP")
    device.set_property.assert_not_called()
    device.call_action.assert_not_called()
