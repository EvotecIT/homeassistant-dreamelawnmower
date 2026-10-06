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
    DreameMowerProperty,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    InvalidActionException,
)


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
def test_ai_command_requires_reported_acceptance(policy, accepted):
    mower = object.__new__(DreameMowerDevice)
    mower.capability = SimpleNamespace(ai_detection=True)
    mower.status = SimpleNamespace(
        ai_obstacle_detection=True, ai_obstacle_image_upload=True,
        ai_policy_accepted=False,
    )
    mower._protocol = SimpleNamespace(
        cloud=SimpleNamespace(get_batch_device_datas=Mock(return_value={
            device_privacy.AI_POLICY_PROPERTY: json.dumps(policy),
        })),
        set_property=Mock(return_value={"code": 0}),
    )
    mower.property_mapping = {DreameMowerProperty.AI_DETECTION: {"siid": 2, "piid": 4}}
    mower._property_changed = Mock()
    if accepted:
        assert mower.set_ai_detection(1) == {"code": 0}
        mower._protocol.set_property.assert_called_once_with(2, 4, 1, 3)
        assert mower.status.ai_policy_accepted is True
    else:
        with pytest.raises(InvalidActionException, match="accept privacy policy"):
            mower.set_ai_detection(1)
        mower._protocol.set_property.assert_not_called()
        assert mower.status.ai_obstacle_detection is False
        assert mower.status.ai_obstacle_image_upload is False
        mower._property_changed.assert_called_once_with()


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
