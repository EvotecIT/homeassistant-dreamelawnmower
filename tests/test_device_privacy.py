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


@pytest.mark.parametrize("wire", ["mask", "json"])
def test_ai_pending_edits_survive_repeated_stale_readback(privacy_device, wire):
    mower = privacy_device
    mower.ai_data = {"AI_OBSTACLE_DETECTION": False}
    mower.status.ai_policy_accepted = True
    mower.data[DreameMowerProperty.AI_DETECTION.value] = 0 if wire == "mask" else "{}"
    mower.set_ai_property(DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION, True)
    mower.data[DreameMowerProperty.AI_DETECTION.value] = (
        0 if wire == "mask" else '{"obstacle_detect_switch":false}'
    )
    for _ in range(2):
        mower._ai_obstacle_detection_changed()
        assert mower.status.ai_obstacle_detection is True
        assert "AI_OBSTACLE_DETECTION" in mower._dirty_ai_data
    mower.data[DreameMowerProperty.AI_DETECTION.value] = (
        2 if wire == "mask" else '{"obstacle_detect_switch":true}'
    )
    mower._ai_obstacle_detection_changed()
    assert "AI_OBSTACLE_DETECTION" not in mower._dirty_ai_data


@pytest.mark.parametrize("failure", [
    None, TimeoutError("transport timed out"), [{"code": -1}],
])
def test_ai_whole_masks_preserve_edits_after_failed_write(privacy_device, failure):
    mower = privacy_device
    mower.ai_data = {
        "AI_OBSTACLE_DETECTION": False, "AI_OBSTACLE_IMAGE_UPLOAD": False,
        "AI_PET_DETECTION": False,
    }
    mower.status.ai_policy_accepted = True
    unknown_bit = 1 << 20
    mower.data[DreameMowerProperty.AI_DETECTION.value] = unknown_bit
    mower._protocol.set_property.side_effect = [
        [{"code": 0}], [{"code": 0}] if failure is None else failure, [{"code": 0}],
    ]
    mower.set_ai_property(DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION, True)
    if isinstance(failure, BaseException):
        with pytest.raises(TimeoutError):
            mower.set_ai_property(
                DreameMowerStrAIProperty.AI_OBSTACLE_IMAGE_UPLOAD, True,
            )
    else:
        mower.set_ai_property(DreameMowerStrAIProperty.AI_OBSTACLE_IMAGE_UPLOAD, True)
    mower.set_ai_property(DreameMowerStrAIProperty.AI_PET_DETECTION, True)
    assert [call.args[2] for call in mower._protocol.set_property.call_args_list] == [
        unknown_bit | 2, unknown_bit | 34,
        unknown_bit | (50 if failure is None else 18),
    ]


@pytest.mark.parametrize("prop,remaining_mask", [
    (DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION, 32),
    (DreameMowerStrAIProperty.AI_OBSTACLE_IMAGE_UPLOAD, 2),
])
def test_ai_disable_then_reenable_requires_consent_before_readback(
    privacy_device, prop, remaining_mask,
):
    mower = privacy_device
    mower.data[DreameMowerProperty.AI_DETECTION.value] = 34
    mower._protocol.cloud.get_batch_device_datas = Mock(return_value={
        device_privacy.AI_POLICY_PROPERTY: '{"privacyAuthed":false}',
    })
    assert mower.set_ai_property(prop, False) == [{"code": 0}]
    with pytest.raises(InvalidActionException, match="accept privacy policy"):
        mower.set_ai_property(prop, True)
    assert mower.ai_data[prop.name] is False
    assert mower._protocol.set_property.call_args.args[2] == remaining_mask
    mower._protocol.set_property.assert_called_once()


@pytest.mark.parametrize("wire", ["mask", "json"])
@pytest.mark.parametrize("failure", [
    TimeoutError("transport timed out"), [{"code": -1}],
])
def test_repeated_ai_failure_restores_pending_edit(privacy_device, wire, failure):
    mower = privacy_device
    mower.ai_data = {"AI_OBSTACLE_DETECTION": False}
    mower.status.ai_policy_accepted = True
    mower.data[DreameMowerProperty.AI_DETECTION.value] = 0 if wire == "mask" else "{}"
    mower._protocol.set_property.side_effect = [[{"code": 0}], failure]
    prop = DreameMowerStrAIProperty.AI_OBSTACLE_DETECTION
    mower.set_ai_property(prop, True)
    pending = mower._dirty_ai_data[prop.name]
    if isinstance(failure, BaseException):
        with pytest.raises(TimeoutError):
            mower.set_ai_property(prop, False)
    else:
        mower.set_ai_property(prop, False)
    assert mower._dirty_ai_data[prop.name] is pending
    mower.data[DreameMowerProperty.AI_DETECTION.value] = (
        0 if wire == "mask" else '{"obstacle_detect_switch":false}'
    )
    for _ in range(2):
        mower._ai_obstacle_detection_changed()
        assert mower.status.ai_obstacle_detection is True


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
