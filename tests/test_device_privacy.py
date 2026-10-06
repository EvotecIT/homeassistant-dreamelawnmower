"""Cloud privacy flags retain refusal and do not infer consent from truthiness."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import device_privacy
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDevice,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerAIProperty,
    DreameMowerProperty,
    DreameMowerStrAIProperty,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    InvalidActionException,
)


@pytest.fixture
def privacy_device():
    mower = DreameMowerDevice(
        "Privacy proof", None, " ", username="user@example.invalid",
        password="validation-only", country="eu", device_id="42",
    )
    mower._prepare_device_initialization({
        "model": "dreame.mower.g2408", "fw_ver": "4.3.6_1200",
    })
    mower.capability.ai_detection = True
    mower.ai_data = {
        DreameMowerAIProperty.AI_OBSTACLE_DETECTION.name: True,
        DreameMowerAIProperty.AI_OBSTACLE_IMAGE_UPLOAD.name: True,
    }
    mower._property_changed = Mock()
    mower._protocol.set_property = Mock(return_value=[{"code": 0}])
    try:
        yield mower
    finally:
        mower.disconnect()


@pytest.mark.parametrize("policy,expected", [
    ({"privacyAuthed": True}, True),
    ({"privacyAuthed": False}, False),
    ({"aiPrivacyAuthed": True}, True),
    ({"aiPrivacyAuthed": False}, False),
    ({"privacyAuthed": False, "aiPrivacyAuthed": True}, False),
    ({"privacyAuthed": 1}, True),
    ({"privacyAuthed": 0}, False),
    ({"privacyAuthed": "false"}, None),
    ({"privacyAuthed": "true"}, None),
    ({"privacyAuthed": []}, None),
    ({"privacyAuthed": 2}, None),
    ({}, None),
    ([], None),
])
def test_privacy_payload_requires_an_explicit_supported_flag(policy, expected):
    assert device_privacy.decode_ai_policy_acceptance({
        device_privacy.AI_POLICY_PROPERTY: json.dumps(policy),
    }) is expected


@pytest.mark.parametrize(
    "response", [None, {}, [], {device_privacy.AI_POLICY_PROPERTY: "{"}],
)
def test_missing_or_invalid_privacy_metadata_is_unknown(response):
    assert device_privacy.decode_ai_policy_acceptance(response) is None


@pytest.mark.parametrize("policy,accepted", [
    ({"privacyAuthed": True}, True),
    ({"privacyAuthed": False}, False),
    ({"aiPrivacyAuthed": True}, True),
    ({"privacyAuthed": "false"}, False),
])
@pytest.mark.parametrize("previously_enabled", [True, False])
def test_ai_command_requires_reported_acceptance(
    privacy_device, policy, accepted, previously_enabled,
):
    mower = privacy_device
    for prop in (DreameMowerAIProperty.AI_OBSTACLE_DETECTION,
                 DreameMowerAIProperty.AI_OBSTACLE_IMAGE_UPLOAD):
        mower.ai_data[prop.name] = previously_enabled
    mower._protocol.cloud.get_batch_device_datas = Mock(return_value={
        device_privacy.AI_POLICY_PROPERTY: json.dumps(policy),
    })
    mower.property_mapping = {DreameMowerProperty.AI_DETECTION: {"siid": 2, "piid": 4}}
    if accepted:
        assert mower.set_ai_detection(2) == [{"code": 0}]
        mower._protocol.set_property.assert_called_once_with(2, 4, 2, 3)
        assert mower.status.ai_policy_accepted is True
    else:
        with pytest.raises(InvalidActionException, match="accept privacy policy"):
            mower.set_ai_detection(2)
        mower._protocol.set_property.assert_not_called()
        # Rejection does not pretend a real device setting was changed.
        assert mower.status.ai_obstacle_detection is previously_enabled
        assert mower.status.ai_obstacle_image_upload is previously_enabled


@pytest.mark.parametrize("wire", ["mask", "json"])
def test_ai_property_updates_preserve_explicit_cloud_refusal(privacy_device, wire):
    mower = privacy_device
    mower.status.ai_policy_accepted = False
    mower.ai_data = {}
    mower.data[DreameMowerProperty.AI_DETECTION.value] = (
        int(DreameMowerAIProperty.AI_OBSTACLE_DETECTION)
        if wire == "mask" else json.dumps({
            DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION.value: True,
        })
    )
    mower._ai_obstacle_detection_changed()
    assert mower.status.ai_obstacle_detection is True
    assert mower.status.ai_policy_accepted is False
    mower._protocol.cloud.get_batch_device_datas = Mock(return_value={
        device_privacy.AI_POLICY_PROPERTY: json.dumps({"privacyAuthed": False}),
    })
    with pytest.raises(InvalidActionException, match="accept privacy policy"):
        mower.set_ai_detection(int(
            DreameMowerAIProperty.AI_OBSTACLE_DETECTION
            | DreameMowerAIProperty.AI_OBSTACLE_IMAGE_UPLOAD
        ))
    mower._protocol.cloud.get_batch_device_datas.assert_called_once_with(
        [device_privacy.AI_POLICY_PROPERTY],
    )
    mower._protocol.set_property.assert_not_called()


def test_ai_property_refusal_rolls_back_optimistic_state(privacy_device):
    mower = privacy_device
    prop = DreameMowerAIProperty.AI_OBSTACLE_DETECTION
    mower.ai_data[prop.name] = False
    mower.ai_data[DreameMowerAIProperty.AI_OBSTACLE_IMAGE_UPLOAD.name] = False
    mower.data[DreameMowerProperty.AI_DETECTION.value] = 0
    mower._protocol.cloud.get_batch_device_datas = Mock(return_value={
        device_privacy.AI_POLICY_PROPERTY: json.dumps({"privacyAuthed": False}),
    })
    with pytest.raises(InvalidActionException, match="accept privacy policy"):
        mower.set_ai_property(prop, True)
    assert mower.status.ai_obstacle_detection is False
    assert prop.name not in mower._dirty_ai_data
    mower._protocol.set_property.assert_not_called()


@pytest.mark.parametrize("settings", [0, {"obstacle_detect_switch": False}])
def test_disabling_ai_does_not_require_privacy_acceptance(privacy_device, settings):
    mower = privacy_device
    mower._protocol.cloud.get_batch_device_datas = Mock(
        side_effect=AssertionError("Disabling must not request consent"),
    )
    assert mower.set_ai_detection(settings) == [{"code": 0}]
    mower._protocol.set_property.assert_called_once()
    mower._protocol.cloud.get_batch_device_datas.assert_not_called()


@pytest.mark.parametrize("accepted", [True, False])
def test_legacy_startup_updates_the_same_privacy_status(monkeypatch, accepted):
    mower = object.__new__(DreameMowerDevice)
    mower._ready = False
    mower.available = False
    mower._map_manager = None
    mower.status = SimpleNamespace(ai_policy_accepted=not accepted)
    mower._protocol = SimpleNamespace(cloud=SimpleNamespace(
        get_batch_device_datas=Mock(return_value={
            device_privacy.AI_POLICY_PROPERTY: json.dumps({"privacyAuthed": accepted}),
        }),
    ))
    monkeypatch.setattr(DreameMowerDevice, "device_connected", property(lambda _: True))
    monkeypatch.setattr(DreameMowerDevice, "cloud_connected", property(lambda _: True))
    mower._finish_device_initialization()
    assert mower._ready and mower.available
    assert mower.status.ai_policy_accepted is accepted
    mower._protocol.cloud.get_batch_device_datas.assert_called_once_with(
        [device_privacy.AI_POLICY_PROPERTY],
    )


@pytest.mark.parametrize("prop,remaining", [
    (DreameMowerAIProperty.AI_OBSTACLE_DETECTION,
     DreameMowerAIProperty.AI_OBSTACLE_IMAGE_UPLOAD),
    (DreameMowerAIProperty.AI_OBSTACLE_IMAGE_UPLOAD,
     DreameMowerAIProperty.AI_OBSTACLE_DETECTION),
])
def test_disabling_one_bit_preserves_other_setting_without_consent(
    privacy_device, prop, remaining,
):
    mower = privacy_device
    mower.data[DreameMowerProperty.AI_DETECTION.value] = int(prop | remaining)
    mower._protocol.cloud.get_batch_device_datas = Mock(
        side_effect=AssertionError("Disabling must not request consent"),
    )
    assert mower.set_ai_property(prop, False) == [{"code": 0}]
    assert mower.ai_data[prop.name] is False
    assert mower.ai_data[remaining.name] is True
    assert mower._protocol.set_property.call_args.args[2] == int(remaining)
    mower._protocol.cloud.get_batch_device_datas.assert_not_called()
