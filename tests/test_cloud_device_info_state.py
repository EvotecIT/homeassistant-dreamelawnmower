"""Device-info state shared by native HTTP reads and legacy MQTT."""

import json

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    cloud_wire,
    protocol_cloud,
)


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
def test_device_info_updates_mqtt_identity_without_legacy_login(account_type):
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol(
        "account@example.invalid", "password", account_type=account_type,
    )
    strings = cloud_wire.cloud_strings(account_type)
    info = {
        strings[8]: "owner", "did": "42", strings[35]: "mower.model",
        strings[9]: "mqtt.example.invalid",
        strings[10]: json.dumps({strings[11]: "stream-key"}),
    }
    try:
        cloud._handle_device_info(info)
        assert cloud._uid == "owner"
        assert cloud._did == "42"
        assert cloud._model == "mower.model"
        assert cloud._host == "mqtt.example.invalid"
        assert cloud._stream_key == "stream-key"
        assert not cloud._logged_in

        # A response without a new key retains the existing stream credential.
        cloud._handle_device_info({**info, strings[10]: ""})
        assert cloud._stream_key == "stream-key"

        # A malformed vendor response cannot leave a mixed old/new identity.
        with pytest.raises(ValueError):
            cloud._handle_device_info({
                **info, "did": "other-device", strings[10]: "broken-json",
            })
        assert cloud._did == "42"
        assert cloud._stream_key == "stream-key"

        # A response finishing after permanent teardown cannot revive state.
        cloud._shutdown_requested = True
        cloud._handle_device_info({**info, "did": "late-device"})
        assert cloud._did == "42"
    finally:
        cloud._session.close()
