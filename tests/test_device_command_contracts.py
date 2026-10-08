"""Legacy command contracts preserve cache types and reject incomplete state."""

from __future__ import annotations

import base64
import json
from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDevice,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerAutoSwitchProperty,
    DreameMowerProperty,
    DreameMowerStrAIProperty,
    DreameMowerTaskStatus,
    DreameMowerVoiceAssistantLanguage,
    Shortcut,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    InvalidActionException,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    MapData,
)


@pytest.fixture
def mower(monkeypatch):
    device = DreameMowerDevice("Command contracts", None, None)
    device.schedule_update = Mock()
    device._protocol.set_property = Mock()
    device._protocol.action = Mock()
    device.start_custom = Mock(return_value={"code": 0})
    device.data[DreameMowerProperty.TASK_STATUS.value] = DreameMowerTaskStatus.COMPLETED
    monkeypatch.setattr(
        DreameMowerDevice, "device_connected", property(lambda self: True),
    )
    try:
        yield device
    finally:
        device.disconnect()


def test_go_to_rejects_unknown_cleaning_mode_before_dispatch(mower):
    mower.data[DreameMowerProperty.BATTERY_LEVEL.value] = 80

    with pytest.raises(InvalidActionException, match="Cleaning mode"):
        mower.go_to(100, 200)

    assert mower.status.go_to_zone is None
    mower.start_custom.assert_not_called()
    mower._protocol.action.assert_not_called()


@pytest.mark.parametrize("kind", ["ai", "auto_switch"])
def test_setting_write_waits_for_its_property_cache(mower, kind):
    with pytest.raises(InvalidActionException, match="Not supported"):
        if kind == "ai":
            mower.capability.ai_detection = True
            mower.set_ai_property(
                DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION, False,
            )
        else:
            mower.capability.auto_switch_settings = True
            mower.set_auto_switch_property(
                DreameMowerAutoSwitchProperty.CLEANING_ROUTE, 1,
            )

    mower._protocol.set_property.assert_not_called()


@pytest.mark.parametrize("accepted", [True, False])
def test_ai_write_initializes_dirty_cache_and_preserves_acknowledgement(
    mower, accepted,
):
    prop = DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION
    mower.capability.ai_detection = True
    mower.ai_data = {prop.name: True}
    mower._protocol.set_property.return_value = [{"code": 0 if accepted else -1}]

    reply = mower.set_ai_property(prop, False)

    assert reply == [{"code": 0 if accepted else -1}]
    assert mower.ai_data[prop.name] is (not accepted)
    assert (prop.name in mower._dirty_ai_data) is accepted
    mapping = mower.property_mapping[DreameMowerProperty.AI_DETECTION]
    mower._protocol.set_property.assert_called_once_with(
        mapping["siid"], mapping["piid"],
        json.dumps({prop.value: False}, separators=(",", ":")), 3,
    )


@pytest.mark.parametrize("current", [None, MapData()])
def test_obstacle_ignore_rejects_missing_map_or_obstacles(mower, current):
    mower.capability.ai_detection = True
    mower.status._map_manager.get_map = Mock(return_value=current)
    mower.update_map_data_async = Mock()

    with pytest.raises(InvalidActionException, match="Obstacle not found"):
        mower.set_obstacle_ignore(10, 20, True)

    mower.update_map_data_async.assert_not_called()


def test_zone_cleaning_rejects_missing_dimensions_before_dispatch(mower):
    mower.status._map_manager.get_map = Mock(return_value=MapData())

    with pytest.raises(InvalidActionException, match="Map dimensions"):
        mower.clean_zone([0, 0, 500, 500], 1)

    mower.start_custom.assert_not_called()


@pytest.mark.parametrize("outcome", ["unchanged", "accepted", "rejected", "exception"])
def test_shortcut_rename_preserves_a_string_name_and_wire_payload(mower, outcome):
    mower.capability.shortcuts = True
    mower.status.shortcuts = {1: Shortcut(id=1, name="Morning")}
    original_raw = json.dumps([
        {"id": 1, "name": base64.b64encode(b"Morning").decode()},
    ])
    mower.data[DreameMowerProperty.SHORTCUTS.value] = original_raw
    reply = {"out": [{"value": "0" if outcome == "accepted" else "1"}]}
    mower.call_shortcut_action = Mock(
        return_value=reply,
        side_effect=(
            OSError("Transport unavailable") if outcome == "exception" else None
        ),
    )
    requested = "Morning" if outcome == "unchanged" else "Evening"

    if outcome == "exception":
        with pytest.raises(OSError, match="Transport unavailable"):
            mower.rename_shortcut(1, requested)
    else:
        mower.rename_shortcut(1, requested)

    if outcome == "unchanged":
        mower.call_shortcut_action.assert_not_called()
    else:
        mower.call_shortcut_action.assert_called_once_with(
            "EDIT_COMMAND",
            {"id": 1, "name": base64.b64encode(b"Evening").decode(), "type": 3},
        )
    assert mower.status.shortcuts[1].name == (
        "Evening" if outcome == "accepted" else "Morning"
    )
    if outcome != "accepted":
        assert mower.get_property(DreameMowerProperty.SHORTCUTS) == original_raw


@pytest.mark.parametrize("language", ["english", "EN"])
def test_generic_language_setter_dispatches_the_existing_string_enum(mower, language):
    mower.data[DreameMowerProperty.VOICE_ASSISTANT.value] = 1
    mower.data[DreameMowerProperty.VOICE_ASSISTANT_LANGUAGE.value] = "DE"
    mower._protocol.set_property.return_value = [{"code": 0}]

    assert mower.set_property_value("voice_assistant_language", language) is True

    mapping = mower.property_mapping[DreameMowerProperty.VOICE_ASSISTANT_LANGUAGE]
    mower._protocol.set_property.assert_called_once_with(
        mapping["siid"], mapping["piid"], DreameMowerVoiceAssistantLanguage.ENGLISH,
    )


@pytest.mark.parametrize("operation", ["set", "action"])
def test_generic_dispatch_rejects_noncallable_members(mower, operation):
    with pytest.raises(InvalidActionException):
        if operation == "set":
            mower.set_property_value("unknown_setting", 1)
        else:
            mower.call_action_value("status")

    mower._protocol.action.assert_not_called()
    mower._protocol.set_property.assert_not_called()


@pytest.mark.parametrize("kind", ["auto_switch", "string_ai"])
def test_generic_string_enum_setting_keeps_successful_dispatch(mower, kind):
    if kind == "auto_switch":
        prop = DreameMowerAutoSwitchProperty.COLLISION_AVOIDANCE
        mower.capability.auto_switch_settings = True
        mower.auto_switch_data = {prop.name: 0}
        value = 1
        expected = {"k": prop.value, "v": 1}
        retry = 1
        mapped = DreameMowerProperty.AUTO_SWITCH_SETTINGS
        # Use the same model-specific service fixture as async auto-switch proof.
        mower.property_mapping[mapped] = {"siid": 4, "piid": 5}
    else:
        prop = DreameMowerStrAIProperty.AI_HUMAN_DETECTION
        mower.capability.ai_detection = True
        mower.ai_data = {
            prop.name: True,
            DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION.name: True,
        }
        mower._dirty_ai_data = {}
        value = 0
        expected = {prop.value: False}
        retry = 3
        mapped = DreameMowerProperty.AI_DETECTION
    mower._protocol.set_property.return_value = [{"code": 0}]

    assert mower.set_property_value(prop.name.lower(), value) is None

    mapping = mower.property_mapping[mapped]
    if kind == "auto_switch":
        mower._protocol.set_property.assert_called_once_with(
            mapping["siid"], mapping["piid"],
            json.dumps(expected, separators=(",", ":")), retry_count=retry,
        )
    else:
        mower._protocol.set_property.assert_called_once_with(
            mapping["siid"], mapping["piid"],
            json.dumps(expected, separators=(",", ":")), retry,
        )


@pytest.mark.parametrize("kind", ["active_task", "ai_disabled"])
def test_generic_setting_availability_blocks_transport(mower, kind):
    if kind == "active_task":
        prop = DreameMowerAutoSwitchProperty.WIDER_CORNER_COVERAGE
        mower.capability.auto_switch_settings = True
        mower.auto_switch_data = {prop.name: 1}
        mower.property_mapping[DreameMowerProperty.AUTO_SWITCH_SETTINGS] = {
            "siid": 4, "piid": 5,
        }
        mower.data[DreameMowerProperty.TASK_STATUS.value] = (
            DreameMowerTaskStatus.AUTO_CLEANING
        )
    else:
        prop = DreameMowerStrAIProperty.AI_HUMAN_DETECTION
        mower.capability.ai_detection = True
        mower.ai_data = {
            prop.name: False,
            DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION.name: False,
        }
        mower._dirty_ai_data = {}
    mower._protocol.set_property.return_value = [{"code": 0}]

    with pytest.raises(InvalidActionException, match="Property unavailable"):
        mower.set_property_value(prop.name.lower(), 2)

    mower._protocol.set_property.assert_not_called()


@pytest.mark.parametrize("kind", ["zone", "spot"])
@pytest.mark.parametrize("nested", [False, True])
def test_coordinate_commands_keep_flat_and_nested_wire_payloads(mower, kind, nested):
    row = [0, 0, 500, 500] if kind == "zone" else [100, 200]
    coordinates = [row] if nested else row
    if kind == "zone":
        mower.clean_zone(coordinates, None)
    else:
        mower.clean_spot(coordinates, None)

    payload = json.loads(mower.start_custom.call_args.args[1])
    assert payload == {"areas" if kind == "zone" else "points": [row + [1]]}
