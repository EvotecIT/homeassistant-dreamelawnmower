"""AI-setting authorization uses explicit cloud policy metadata."""

import json
from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDevice,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerProperty,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    InvalidActionException,
)


@pytest.mark.parametrize("payload,accepted", [
    ({"privacyAuthed": True}, True),
    ({"privacyAuthed": False}, False),
    ({"aiPrivacyAuthed": True}, True),
    ({"aiPrivacyAuthed": False}, False),
    ({"privacyAuthed": False, "aiPrivacyAuthed": True}, False),
    ({"privacyAuthed": 1}, True),
    ({"privacyAuthed": 0}, False),
    ({"privacyAuthed": "false"}, False),
    ({}, False),
    ([], False),
])
def test_ai_setting_requires_explicit_cloud_acceptance(payload, accepted):
    cloud = Mock()
    cloud.get_batch_device_datas.return_value = {
        "prop.s_ai_config": json.dumps(payload),
    }
    protocol = Mock(cloud=cloud)
    protocol.set_property.return_value = {"code": 0}
    mower = DreameMowerDevice("Privacy", None, " ")
    mower.capability.ai_detection = True
    mower.ai_data = {"AI_OBSTACLE_DETECTION": True}
    mower._protocol = protocol
    mower.property_mapping = {DreameMowerProperty.AI_DETECTION: {"siid": 2, "piid": 4}}
    try:
        if accepted:
            assert mower.set_ai_detection(2) == {"code": 0}
            protocol.set_property.assert_called_once_with(2, 4, 2, 3)
        else:
            with pytest.raises(InvalidActionException, match="accept privacy policy"):
                mower.set_ai_detection(2)
            protocol.set_property.assert_not_called()
        assert mower.status.ai_policy_accepted is accepted
        cloud.get_batch_device_datas.assert_called_once_with(["prop.s_ai_config"])
    finally:
        mower.disconnect()
