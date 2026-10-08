"""Named command dispatch preserves validation and enum routing."""

import json
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
    DreameMowerStrAIProperty,
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
    device.capability.dnd = True
    device.capability.dnd_task = False
    device.data[DreameMowerProperty.DND.value] = 1

    assert device.set_property_value(name, value) is True

    device.set_property.assert_called_once_with(prop, value)


@pytest.mark.parametrize("value", ["24:00", "22:99", 1])
def test_invalid_dnd_time_never_reaches_device(device, value):
    device.capability.dnd = True
    device.data[DreameMowerProperty.DND.value] = 1
    with pytest.raises(InvalidActionException, match="Invalid value"):
        device.set_property_value("DND_START", value)
    device.set_property.assert_not_called()


@pytest.mark.parametrize("name", ["DND_START", "DND_END"])
def test_disabled_dnd_rejects_time_commands(device, name):
    device.capability.dnd = True
    device.capability.dnd_task = False
    device.data[DreameMowerProperty.DND.value] = 0

    with pytest.raises(InvalidActionException, match="Property unavailable"):
        device.set_property_value(name, "22:00")

    device.set_property.assert_not_called()


def test_string_enum_uses_canonical_property_availability(device, monkeypatch):
    prop = DreameMowerAutoSwitchProperty.WIDER_CORNER_COVERAGE
    device.capability.auto_switch_settings = True
    device.auto_switch_data = {prop.name: 0}
    monkeypatch.setitem(
        device_commands.PROPERTY_AVAILABILITY, prop.name, lambda _: False
    )

    with pytest.raises(InvalidActionException, match="Property unavailable"):
        device.set_property_value(prop.name, 1)

    device.set_property.assert_not_called()


@pytest.mark.parametrize("setting", ["auto_switch", "ai"])
def test_settings_transport_failure_rolls_back_and_returns_no_result(device, setting):
    if setting == "auto_switch":
        prop = DreameMowerAutoSwitchProperty.WIDER_CORNER_COVERAGE
        device.capability.auto_switch_settings = True
        device.auto_switch_data = {prop.name: 0}
        device.set_auto_switch_settings = Mock(
            side_effect=TimeoutError("transport timed out")
        )
        write = device.set_auto_switch_property
        data = device.auto_switch_data
        dirty = device._dirty_auto_switch_data
    else:
        prop = DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION
        device.capability.ai_detection = True
        device.ai_data = {prop.name: False}
        device._dirty_ai_data = {}
        device.status.ai_policy_accepted = True
        device._protocol.set_property = Mock(
            side_effect=TimeoutError("transport timed out")
        )
        write = device.set_ai_property
        data = device.ai_data
        dirty = device._dirty_ai_data

    assert write(prop, 1) is None
    assert data[prop.name] == 0
    assert prop.name not in dirty


@pytest.mark.parametrize("ai_value", [0, "{}"])
@pytest.mark.parametrize("prop", [
    DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION,
    DreameMowerStrAIProperty.AI_OBSTACLE_IMAGE_UPLOAD,
])
def test_ai_policy_refusal_rolls_back_and_remains_visible(device, ai_value, prop):
    device.capability.ai_detection = True
    device.data[DreameMowerProperty.AI_DETECTION.value] = ai_value
    device.ai_data = {
        DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION.name: False,
        DreameMowerStrAIProperty.AI_OBSTACLE_IMAGE_UPLOAD.name: False,
    }
    device._dirty_ai_data = {}
    cloud = Mock()
    cloud.get_batch_device_datas.return_value = {
        "prop.s_ai_config": '{"privacyAuthed":false}',
    }
    device._protocol = Mock(cloud=cloud)

    with pytest.raises(InvalidActionException, match="accept privacy policy"):
        device.set_ai_property(prop, True)

    assert device.ai_data[prop.name] is False
    assert prop.name not in device._dirty_ai_data
    assert device.status.ai_policy_accepted is False
    device._protocol.set_property.assert_not_called()
    cloud.get_batch_device_datas.assert_called_once_with(["prop.s_ai_config"])


@pytest.mark.parametrize("settings", [
    2,
    32,
    {"obstacle_detect_switch": True},
    {"obstacle_app_display_switch": True},
])
@pytest.mark.parametrize("accepted", [False, True])
def test_direct_ai_enable_checks_policy_before_writing(device, settings, accepted):
    device.capability.ai_detection = True
    device.ai_data = {
        DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION.name: False,
        DreameMowerStrAIProperty.AI_OBSTACLE_IMAGE_UPLOAD.name: False,
    }
    cloud = Mock()
    cloud.get_batch_device_datas.return_value = {
        "prop.s_ai_config": json.dumps({"privacyAuthed": accepted}),
    }
    device._protocol = Mock(cloud=cloud)
    device._protocol.set_property.return_value = [{"code": 0}]

    if accepted:
        assert device.set_ai_detection(settings) == [{"code": 0}]
        device._protocol.set_property.assert_called_once()
    else:
        with pytest.raises(InvalidActionException, match="accept privacy policy"):
            device.set_ai_detection(settings)
        device._protocol.set_property.assert_not_called()
    cloud.get_batch_device_datas.assert_called_once_with(["prop.s_ai_config"])


@pytest.mark.parametrize("settings", [0, {
    "obstacle_detect_switch": False, "obstacle_app_display_switch": False,
}])
def test_ai_disable_does_not_require_consent_or_clear_confirmed_cache(device, settings):
    device.capability.ai_detection = True
    device.ai_data = {"AI_OBSTACLE_DETECTION": True, "AI_OBSTACLE_IMAGE_UPLOAD": True}
    device.data[DreameMowerProperty.AI_DETECTION.value] = 34
    device._protocol = Mock()
    device._protocol.set_property.return_value = [{"code": 0}]

    assert device.set_ai_detection(settings) == [{"code": 0}]

    device._protocol.cloud.get_batch_device_datas.assert_not_called()
    device._protocol.set_property.assert_called_once()
    assert all(device.ai_data.values())


def test_ai_refusal_preserves_the_untouched_reported_setting(device):
    device.capability.ai_detection = True
    device.ai_data = {"AI_OBSTACLE_DETECTION": True, "AI_OBSTACLE_IMAGE_UPLOAD": False}
    device.data[DreameMowerProperty.AI_DETECTION.value] = 2
    device._dirty_ai_data = {}
    cloud = Mock()
    cloud.get_batch_device_datas.return_value = {
        "prop.s_ai_config": '{"privacyAuthed":false}',
    }
    device._protocol = Mock(cloud=cloud)

    with pytest.raises(InvalidActionException, match="accept privacy policy"):
        device.set_ai_property(DreameMowerStrAIProperty.AI_OBSTACLE_IMAGE_UPLOAD, True)

    assert device.ai_data == {
        "AI_OBSTACLE_DETECTION": True, "AI_OBSTACLE_IMAGE_UPLOAD": False,
    }
    device._protocol.set_property.assert_not_called()


@pytest.mark.parametrize("wire", ["mask", "json"])
def test_ai_readback_preserves_explicit_policy_refusal(device, wire):
    device.capability.ai_detection = True
    device._dirty_ai_data = {}
    device.ai_data = {}
    device.data[DreameMowerProperty.AI_DETECTION.value] = (
        2 if wire == "mask" else '{"obstacle_detect_switch":true}'
    )

    device._ai_obstacle_detection_changed()

    assert device.status.ai_obstacle_detection is True
    assert device.status.ai_policy_accepted is False


@pytest.mark.parametrize("wire", ["mask", "json"])
def test_stale_ai_readback_retains_pending_settings(device, wire):
    device.capability.ai_detection = True
    device.ai_data = {"AI_OBSTACLE_DETECTION": False}
    device._dirty_ai_data = {}
    device.status.ai_policy_accepted = True
    device.data[DreameMowerProperty.AI_DETECTION.value] = 0 if wire == "mask" else "{}"
    device._protocol = Mock()
    device._protocol.set_property.return_value = [{"code": 0}]
    device.set_ai_property(DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION, True)
    device.data[DreameMowerProperty.AI_DETECTION.value] = (
        0 if wire == "mask" else '{"obstacle_detect_switch":false}'
    )

    for _ in range(2):
        device._ai_obstacle_detection_changed()
        assert device.status.ai_obstacle_detection is True
        assert "AI_OBSTACLE_DETECTION" in device._dirty_ai_data

    device.data[DreameMowerProperty.AI_DETECTION.value] = (
        2 if wire == "mask" else '{"obstacle_detect_switch":true}'
    )
    device._ai_obstacle_detection_changed()
    assert "AI_OBSTACLE_DETECTION" not in device._dirty_ai_data


@pytest.mark.parametrize("failure", [
    None, TimeoutError("transport timed out"), [{"code": -1}],
])
def test_consecutive_ai_masks_preserve_edits_and_unknown_bits(device, failure):
    device.capability.ai_detection = True
    device.ai_data = {
        "AI_OBSTACLE_DETECTION": False, "AI_OBSTACLE_IMAGE_UPLOAD": False,
        "AI_PET_DETECTION": False,
    }
    device._dirty_ai_data = {}
    device.status.ai_policy_accepted = True
    unknown_bit = 1 << 20
    device.data[DreameMowerProperty.AI_DETECTION.value] = unknown_bit
    device._protocol = Mock()
    device._protocol.set_property.side_effect = [
        [{"code": 0}], [{"code": 0}] if failure is None else failure, [{"code": 0}],
    ]

    device.set_ai_property(DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION, True)
    device.set_ai_property(DreameMowerStrAIProperty.AI_OBSTACLE_IMAGE_UPLOAD, True)
    device.set_ai_property(DreameMowerStrAIProperty.AI_PET_DETECTION, True)

    assert [call.args[2] for call in device._protocol.set_property.call_args_list] == [
        unknown_bit | 2, unknown_bit | 34,
        unknown_bit | (50 if failure is None else 18),
    ]
    assert device.ai_data["AI_OBSTACLE_DETECTION"] is True
    assert device.ai_data["AI_OBSTACLE_IMAGE_UPLOAD"] is (failure is None)


@pytest.mark.parametrize("prop,remaining_mask", [
    (DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION, 32),
    (DreameMowerStrAIProperty.AI_OBSTACLE_IMAGE_UPLOAD, 2),
])
def test_ai_disable_then_reenable_requires_consent_before_readback(
    device, prop, remaining_mask,
):
    device.capability.ai_detection = True
    device.ai_data = {"AI_OBSTACLE_DETECTION": True, "AI_OBSTACLE_IMAGE_UPLOAD": True}
    device._dirty_ai_data = {}
    device.data[DreameMowerProperty.AI_DETECTION.value] = 34
    device._protocol = Mock()
    device._protocol.set_property.return_value = [{"code": 0}]
    device._protocol.cloud.get_batch_device_datas.return_value = {
        "prop.s_ai_config": '{"privacyAuthed":false}',
    }

    assert device.set_ai_property(prop, False) == [{"code": 0}]
    with pytest.raises(InvalidActionException, match="accept privacy policy"):
        device.set_ai_property(prop, True)

    assert device.ai_data[prop.name] is False
    assert device._protocol.set_property.call_args.args[2] == remaining_mask
    device._protocol.set_property.assert_called_once()


@pytest.mark.parametrize("wire", ["mask", "json"])
@pytest.mark.parametrize("failure", [
    TimeoutError("transport timed out"), [{"code": -1}],
])
def test_failed_repeated_ai_edit_restores_the_previous_pending_record(
    device, wire, failure,
):
    device.capability.ai_detection = True
    device.ai_data = {"AI_OBSTACLE_DETECTION": False}
    device._dirty_ai_data = {}
    device.status.ai_policy_accepted = True
    device.data[DreameMowerProperty.AI_DETECTION.value] = 0 if wire == "mask" else "{}"
    device._protocol = Mock()
    device._protocol.set_property.side_effect = [[{"code": 0}], failure]
    prop = DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION
    device.set_ai_property(prop, True)
    pending = device._dirty_ai_data[prop.name]

    device.set_ai_property(prop, False)

    assert device._dirty_ai_data[prop.name] is pending
    device.data[DreameMowerProperty.AI_DETECTION.value] = (
        0 if wire == "mask" else '{"obstacle_detect_switch":false}'
    )
    for _ in range(2):
        device._ai_obstacle_detection_changed()
        assert device.status.ai_obstacle_detection is True


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
