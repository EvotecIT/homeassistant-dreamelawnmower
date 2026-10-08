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
    DreameMowerCleaningMode,
    DreameMowerProperty,
    DreameMowerTaskStatus,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    InvalidActionException,
    InvalidValueException,
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


@pytest.mark.parametrize("acknowledged", [True, False])
def test_cleaning_mode_uses_property_write_result(device, acknowledged):
    device.data[DreameMowerProperty.TASK_STATUS.value] = DreameMowerTaskStatus.COMPLETED
    device.status.cleaning_mode = DreameMowerCleaningMode.MOWING
    device.set_property.return_value = acknowledged

    assert device.set_cleaning_mode(0) is acknowledged

    device.set_property.assert_called_once_with(DreameMowerProperty.CLEANING_MODE, 0)


@pytest.mark.parametrize("mode", [-1, 1, 17])
def test_unsupported_cleaning_mode_never_reaches_device(device, mode):
    device.data[DreameMowerProperty.TASK_STATUS.value] = DreameMowerTaskStatus.COMPLETED
    device.status.cleaning_mode = DreameMowerCleaningMode.MOWING

    with pytest.raises(InvalidValueException, match="Unsupported mower cleaning mode"):
        device.set_cleaning_mode(mode)

    device.set_property.assert_not_called()


def test_missing_cleaning_mode_never_reaches_device(device):
    device.status.cleaning_mode = None

    with pytest.raises(InvalidActionException, match="not supported"):
        device.set_cleaning_mode(0)

    device.set_property.assert_not_called()


@pytest.mark.parametrize("mode", [None, DreameMowerCleaningMode.MOWING])
def test_go_to_zone_records_mowing_mode_without_requiring_reported_mode(device, mode):
    device.status.cleaning_mode = mode

    device._set_go_to_zone(100, 200, 50)

    zone = device.status.go_to_zone
    assert zone is not None
    assert (zone.x, zone.y, zone.size, zone.cleaning_mode) == (100, 200, 50, 0)
    device.set_property.assert_not_called()


def test_go_to_zone_restores_mode_through_property_write(device):
    device.status.cleaning_mode = DreameMowerCleaningMode.UNKNOWN
    device._set_go_to_zone(100, 200, 50)
    device.schedule_update = Mock()

    device._restore_go_to_zone()

    assert device.status.go_to_zone is None
    device.set_property.assert_called_once_with(DreameMowerProperty.CLEANING_MODE, 0)


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


@pytest.mark.parametrize(
    "name,value,prop",
    [
        ("DND", True, DreameMowerProperty.DND),
        ("DND_START", "22:00", DreameMowerProperty.DND_START),
        ("DND_END", "08:00", DreameMowerProperty.DND_END),
    ],
)
def test_dnd_property_dispatch_uses_existing_dedicated_setters(
    device, name, value, prop
):
    device.capability.dnd_task = False

    assert device.set_property_value(name, value) is True

    device.set_property.assert_called_once_with(prop, value)


@pytest.mark.parametrize("value", ["24:00", "22:99", 1])
def test_invalid_dnd_time_never_reaches_device(device, value):
    with pytest.raises(InvalidActionException, match="Invalid value"):
        device.set_property_value("DND_START", value)
    device.set_property.assert_not_called()


def test_read_only_property_never_reaches_device(device):
    with pytest.raises(InvalidActionException, match="Invalid property"):
        device.set_property_value("battery_level", 50)
    device.set_property.assert_not_called()


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
