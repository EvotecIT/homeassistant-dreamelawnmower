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
@pytest.mark.parametrize(
    "preloaded,native_owner", [(False, False), (True, False), (True, True)]
)
def test_mqtt_setup_reuses_fetched_information(
    monkeypatch, account_type, preloaded, native_owner
):
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
    owner = Mock() if native_owner else None
    cloud._native_mqtt_connection = owner
    try:
        cloud._handle_device_info(info)
        if preloaded:
            with cloud._operation_lock():
                result = cloud._connect_device_info_unlocked(
                    info, callback, connected, nonblocking=True,
                )
                assert result is info
        else:
            cloud.get_device_info = Mock(return_value=info)
            assert cloud.connect(callback, connected) is info
        mqtt.username_pw_set.assert_called_once_with("account-owner", "access-secret")
        connect = mqtt.connect_async if preloaded else mqtt.connect
        unused_connect = mqtt.connect if preloaded else mqtt.connect_async
        if owner is not None:
            owner.request.assert_called_once_with(mqtt, "mqtt.example.invalid", 8883)
            connect.assert_not_called()
            unused_connect.assert_not_called()
            mqtt.loop_start.assert_not_called()
        else:
            connect.assert_called_once_with("mqtt.example.invalid", 8883, 50)
            unused_connect.assert_not_called()
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
        assert connect.call_count == (0 if native_owner else 1)
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


def test_pending_mqtt_key_is_preserved_until_client_exists():
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol("user", "password")
    cloud._key = "new-key"
    cloud._client_key = "old-key"
    cloud._uuid = "account"
    try:
        assert cloud._set_client_key() is False
        assert cloud._client_key == "old-key"
        cloud._client = mqtt = Mock()
        assert cloud._set_client_key() is True
        mqtt.username_pw_set.assert_called_once_with("account", "new-key")
        assert cloud._set_client_key() is False
        assert mqtt.username_pw_set.call_count == 1
    finally:
        cloud.disconnect()


@pytest.mark.parametrize("result", [0, 5])
def test_late_connect_callback_cannot_restore_closed_client(result):
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol("user", "password")
    cloud._client = mqtt = Mock()
    cloud._key = "new-key"
    cloud.disconnect()
    cloud._on_client_connect(mqtt, cloud, {}, result)
    assert cloud._client_connected is False
    mqtt.subscribe.assert_not_called()
    mqtt.username_pw_set.assert_not_called()
