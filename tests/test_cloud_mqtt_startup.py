"""MQTT startup consumes fetched identity without repeating cloud HTTP."""

from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    protocol_cloud,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.cloud_wire import (
    cloud_strings,
)


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("preloaded", [False, True])
def test_mqtt_setup_reuses_fetched_information(monkeypatch, account_type, preloaded):
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol(
        "user@example.invalid", "password", country="eu", account_type=account_type,
    )
    mqtt = Mock()
    monkeypatch.setattr(protocol_cloud.mqtt_client, "Client", Mock(return_value=mqtt))
    cloud.get_device_info = Mock(side_effect=AssertionError("Unexpected HTTP"))
    cloud._strings = strings = cloud_strings(account_type)
    cloud._logged_in = True
    cloud._uuid = "account-owner"
    cloud._key = "access-secret"
    info = {
        strings[8]: "device-owner", "did": "42", strings[35]: "mower",
        strings[9]: "mqtt.example.invalid:8883", strings[10]: "{}",
    }
    callback = Mock()
    connected = Mock()
    try:
        cloud._handle_device_info(info)
        if preloaded:
            with cloud._operation_lock():
                result = cloud._connect_device_info_unlocked(info, callback, connected)
                assert result is info
        else:
            cloud.get_device_info = Mock(return_value=info)
            assert cloud.connect(callback, connected) is info
        mqtt.username_pw_set.assert_called_once_with("account-owner", "access-secret")
        mqtt.connect.assert_called_once_with("mqtt.example.invalid", 8883, 50)
        mqtt.loop_start.assert_called_once_with()
        mqtt.tls_insecure_set.assert_called_once_with(False)
        assert cloud._message_callback is callback
        assert cloud._connected_callback is connected
        if preloaded:
            cloud.get_device_info.assert_not_called()
        else:
            cloud.get_device_info.assert_called_once_with()
        cloud.disconnect()
        with cloud._operation_lock():
            result = cloud._connect_device_info_unlocked(info, callback, connected)
            assert result is None
        assert mqtt.connect.call_count == 1
    finally:
        cloud.disconnect()


def test_mqtt_connection_error_does_not_log_exception_secrets(monkeypatch, caplog):
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol(
        "user@example.invalid", "password", country="eu",
    )
    mqtt = Mock()
    mqtt.connect.side_effect = RuntimeError("private-auth-token")
    monkeypatch.setattr(protocol_cloud.mqtt_client, "Client", Mock(return_value=mqtt))
    cloud._strings = cloud_strings("dreame")
    cloud._logged_in = True
    cloud._uuid = "account"
    cloud._uid = "device"
    cloud._key = "token"
    cloud._host = "mqtt.example.invalid:8883"
    try:
        with cloud._operation_lock():
            cloud._connect_device_info_unlocked({"did": "42"}, Mock())
        assert "RuntimeError" in caplog.text
        assert "private-auth-token" not in caplog.text
    finally:
        cloud.disconnect()
