"""Legacy mower controls share property dispatch across transports."""

from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDevice,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerCleaningMode,
    DreameMowerProperty,
    DreameMowerTaskStatus,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
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

def test_go_to_zone_restores_mode_through_property_write(device):
    device.status.cleaning_mode = DreameMowerCleaningMode.UNKNOWN
    device._set_go_to_zone(100, 200, 50)
    device.schedule_update = Mock()
    device.data[DreameMowerProperty.CLEANING_MODE.value] = 17
    device._protocol.set_property = Mock(return_value=[{"code": 0}])

    device._restore_go_to_zone()

    assert device.status.go_to_zone is None
    mapping = device.property_mapping[DreameMowerProperty.CLEANING_MODE]
    device._protocol.set_property.assert_called_once_with(
        mapping["siid"], mapping["piid"], 0,
    )


@pytest.fixture
def mode_device(monkeypatch):
    mower = DreameMowerDevice("Mode dispatch", None, " ")
    monkeypatch.setattr(
        DreameMowerDevice, "device_connected", property(lambda self: True)
    )
    mower.data[DreameMowerProperty.TASK_STATUS.value] = DreameMowerTaskStatus.COMPLETED
    mower.data[DreameMowerProperty.CLEANING_MODE.value] = (
        DreameMowerCleaningMode.UNKNOWN
    )
    mower.status.cleaning_mode = DreameMowerCleaningMode.UNKNOWN
    mower._protocol.set_property = Mock(return_value=[{"code": 0}])
    mower.schedule_update = Mock()
    try:
        yield mower
    finally:
        mower.disconnect()


def test_cleaning_mode_setter_dispatches_the_supported_mower_property(mode_device):
    assert mode_device.set_cleaning_mode(DreameMowerCleaningMode.MOWING) is True

    mapping = mode_device.property_mapping[DreameMowerProperty.CLEANING_MODE]
    mode_device._protocol.set_property.assert_called_once_with(
        mapping["siid"], mapping["piid"], 0,
    )
    assert mode_device.data[DreameMowerProperty.CLEANING_MODE.value] == 0


@pytest.mark.parametrize("mode", [DreameMowerCleaningMode.UNKNOWN, 1])
def test_cleaning_mode_setter_rejects_unsupported_modes_before_dispatch(
    mode_device, mode,
):
    with pytest.raises(InvalidValueException, match="Unsupported mower cleaning mode"):
        mode_device.set_cleaning_mode(mode)

    mode_device._protocol.set_property.assert_not_called()
    assert mode_device.data[DreameMowerProperty.CLEANING_MODE.value] == -1
