"""Device-info state shared by native HTTP reads and legacy MQTT."""

import json

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    cloud_wire,
    protocol_cloud,
)


@pytest.mark.parametrize("method", ["get_device_info_v2", "get_device_info"])
@pytest.mark.parametrize(
    "response",
    [
        {"code": 0, "data": None},
        {"code": 0, "data": []},
        {"code": 0, "data": "unexpected"},
        {"data": {}},
        ["data"],
        "data",
    ],
)
def test_device_info_rejects_malformed_response_without_identity_change(
    monkeypatch, response, method
):
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol(
        "account@example.invalid",
        "password",
    )
    cloud._did = "existing-device"
    cloud._strings = cloud_wire.cloud_strings("dreame")
    cloud._host = "existing.example.invalid"
    monkeypatch.setattr(cloud, "request", lambda *args, **kwargs: response)
    monkeypatch.setattr(cloud, "_api_call", lambda *args, **kwargs: response)
    try:
        with pytest.raises(protocol_cloud.DeviceException):
            getattr(cloud, method)()
        assert cloud._did == "existing-device"
        assert cloud._host == "existing.example.invalid"
    finally:
        cloud._session.close()


@pytest.mark.parametrize("response", [None, {}, {"code": 401, "data": None}])
def test_device_info_v2_retains_empty_and_failed_response_contract(
    monkeypatch, response
):
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol(
        "account@example.invalid",
        "password",
    )
    cloud._strings = cloud_wire.cloud_strings("dreame")
    cloud._did = "existing-device"
    monkeypatch.setattr(cloud, "request", lambda *args, **kwargs: response)
    try:
        assert cloud.get_device_info_v2() is None
        assert cloud._did == "existing-device"
    finally:
        cloud._session.close()


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
def test_device_info_updates_mqtt_identity_without_legacy_login(
    account_type, monkeypatch
):
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol(
        "account@example.invalid",
        "password",
        account_type=account_type,
    )
    strings = cloud_wire.cloud_strings(account_type)
    info = {
        strings[8]: "owner",
        "did": "42",
        strings[35]: "mower.model",
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

        cloud._strings = strings
        monkeypatch.setattr(
            cloud, "request", lambda *args, **kwargs: {"code": 0, "data": info}
        )
        assert cloud.get_device_info_v2() == info

        # A response without a new key retains the existing stream credential.
        cloud._handle_device_info({**info, strings[10]: ""})
        assert cloud._stream_key == "stream-key"

        # A malformed vendor response cannot leave a mixed old/new identity.
        with pytest.raises(ValueError):
            cloud._handle_device_info(
                {
                    **info,
                    "did": "other-device",
                    strings[10]: "broken-json",
                }
            )
        assert cloud._did == "42"
        assert cloud._stream_key == "stream-key"

        # A response finishing after permanent teardown cannot revive state.
        cloud._shutdown_requested = True
        cloud._handle_device_info({**info, "did": "late-device"})
        assert cloud._did == "42"
    finally:
        cloud._session.close()


@pytest.mark.parametrize("fallback", [False, True])
def test_legacy_device_info_preserves_merge_and_fallback(monkeypatch, fallback):
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol("user", "password")
    strings = cloud_wire.cloud_strings("dreame")
    cloud._strings = strings
    info = {
        strings[8]: "owner",
        "did": "42",
        strings[35]: "mower.model",
        strings[9]: "mqtt.example.invalid",
        strings[10]: "",
        "name": "primary",
    }
    metadata = (
        {}
        if fallback
        else {strings[31]: {strings[32]: {"name": "secondary", "extra": 1}}}
    )
    responses = iter([{"code": 0, "data": info}, {"code": 0, "data": metadata}])
    monkeypatch.setattr(cloud, "_api_call", lambda *args, **kwargs: next(responses))
    fallback_info = {**info, "name": "fallback"}
    monkeypatch.setattr(
        cloud,
        "get_devices",
        lambda **kwargs: {strings[34]: {strings[36]: [fallback_info]}},
    )
    try:
        result = cloud.get_device_info()
        assert result == (fallback_info if fallback else {"extra": 1, **info})
        assert cloud._did == "42"
        assert cloud._host == "mqtt.example.invalid"
    finally:
        cloud._session.close()


@pytest.mark.parametrize("metadata", [[], {"properties": []}])
def test_legacy_device_info_rejects_malformed_metadata(monkeypatch, metadata):
    cloud = protocol_cloud.DreameMowerDreameHomeCloudProtocol("user", "password")
    strings = cloud_wire.cloud_strings("dreame")
    cloud._strings = strings
    info = {
        strings[8]: "owner",
        "did": "42",
        strings[35]: "model",
        strings[9]: "host",
        strings[10]: "",
    }
    data = metadata if isinstance(metadata, list) else {strings[31]: {strings[32]: []}}
    responses = iter([{"code": 0, "data": info}, {"code": 0, "data": data}])
    monkeypatch.setattr(cloud, "_api_call", lambda *args, **kwargs: next(responses))
    try:
        with pytest.raises(protocol_cloud.DeviceException, match="metadata"):
            cloud.get_device_info()
    finally:
        cloud._session.close()
