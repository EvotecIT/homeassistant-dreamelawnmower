"""Regression checks for cloud key-definition fetch helpers."""

from __future__ import annotations

from dreame_lawn_mower_client import DreameLawnMowerClient
from dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
    DreameLawnMowerDescriptor,
)


class _FakeCloud:
    logged_in = True

    def __init__(self, content: bytes | None = None) -> None:
        self.content = content
        self.requested_url: str | None = None
        self.requested_language: str | None = None

    def get_file(self, url: str, retry_count: int = 4) -> bytes | None:
        self.requested_url = url
        assert retry_count == 1
        return self.content

    def get_device_otc_info(self, lang: str | None = None) -> dict[str, object]:
        self.requested_language = lang
        return {"status": "ok", "map": {"object_name": "MAP.123"}}

    def get_tx_video_access_token(self, os: int = 1) -> dict[str, object]:
        assert os == 1
        return {"accessToken": "access-token-1"}

    def pair_tx_video_device(
        self,
        access_token: str | None = None,
        os: int = 1,
    ) -> dict[str, object]:
        assert access_token == "access-token-1"
        assert os == 1
        return {"paired": True}

    def get_tx_video_device_identity(
        self,
        access_token: str | None = None,
        os: int = 1,
    ) -> dict[str, object]:
        assert access_token == "access-token-1"
        assert os == 1
        return {
            "channelId": "channel-1",
            "productId": "product-1",
            "deviceName": "Mower Camera",
            "secretId": "secret-id-1",
            "secretKey": "secret-key-1",
        }

    def get_tx_video_p2p_info(
        self,
        access_token: str | None = None,
        os: int = 1,
    ) -> dict[str, object]:
        assert access_token == "access-token-1"
        assert os == 1
        return {"p2pInfo": "p2p-info-1"}

    def get_app_plugin_version(
        self,
        model: str,
        app_version_code: int,
        os: int,
    ) -> dict[str, object]:
        return {
            "model": model,
            "app_version_code": app_version_code,
            "os": os,
            "version": 402,
            "sourceCommonPluginVer": 338,
        }


def _client() -> DreameLawnMowerClient:
    return DreameLawnMowerClient(
        username="user@example.invalid",
        password="secret",
        country="eu",
        account_type="dreame",
        descriptor=DreameLawnMowerDescriptor(
            did="device-1",
            name="Garage Mower",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
    )


def test_cloud_key_definition_fetches_public_payload() -> None:
    client = _client()
    cloud = _FakeCloud(
        b'{"keyDefine":{"2.1":{"en":{"13":"Charging Completed"}}},"ver":10}'
    )
    client._sync_get_cloud_protocol = lambda: cloud
    client._sync_get_cloud_device_info = lambda language=None: {
        "keyDefine": {
            "url": "https://example.invalid/key.json",
            "ver": 10,
        }
    }

    result = client._sync_get_cloud_key_definition("en")

    assert result["url_present"] is True
    assert result["fetched"] is True
    assert result["ver"] == 10
    assert result["source"] == "device_info"
    assert result["payload"]["keyDefine"]["2.1"]["en"]["13"] == (
        "Charging Completed"
    )
    assert cloud.requested_url == "https://example.invalid/key.json"


def test_cloud_key_definition_falls_back_to_device_list_record() -> None:
    client = _client()
    cloud = _FakeCloud(
        b'{"keyDefine":{"6.13":{"en":{"map":"Map payload"}}},"ver":11}'
    )
    client._sync_get_cloud_protocol = lambda: cloud
    client._sync_get_cloud_device_info = lambda language=None: {"keyDefine": {}}
    client._sync_get_cloud_device_list_page = lambda **kwargs: {
        "page": {
            "records": [
                {
                    "did": "device-1",
                    "keyDefine": {
                        "url": "https://example.invalid/list-key.json",
                        "ver": 11,
                    },
                }
            ]
        }
    }

    result = client._sync_get_cloud_key_definition("en")

    assert result["url_present"] is True
    assert result["fetched"] is True
    assert result["ver"] == 11
    assert result["source"] == "device_list_v2"
    assert result["payload"]["keyDefine"]["6.13"]["en"]["map"] == "Map payload"
    assert cloud.requested_url == "https://example.invalid/list-key.json"


def test_cloud_key_definition_reports_missing_url() -> None:
    client = _client()
    client._sync_get_cloud_protocol = lambda: _FakeCloud()
    client._sync_get_cloud_device_info = lambda language=None: {"keyDefine": {}}
    client._sync_get_cloud_device_list_page = lambda **kwargs: {"page": {"records": []}}

    result = client._sync_get_cloud_key_definition("en")

    assert result["url_present"] is False
    assert result["fetched"] is False
    assert result["source"] is None
    assert result["error"] == "key_define_url_missing"


def test_cloud_device_otc_info_uses_app_endpoint_helper() -> None:
    client = _client()
    cloud = _FakeCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_cloud_device_otc_info("en")

    assert result == {"status": "ok", "map": {"object_name": "MAP.123"}}
    assert cloud.requested_language == "en"


def test_app_plugin_version_uses_app_endpoint_helper() -> None:
    client = _client()
    client._sync_get_cloud_protocol = lambda: _FakeCloud()

    result = client._sync_get_app_plugin_version(app_version_code=2050300, os=1)

    assert result == {
        "model": "dreame.mower.g2408",
        "app_version_code": 2050300,
        "os": 1,
        "version": 402,
        "sourceCommonPluginVer": 338,
    }


def test_camera_stream_inputs_use_tx_video_endpoints() -> None:
    client = _client()
    client._sync_get_cloud_protocol = lambda: _FakeCloud()

    result = client._sync_get_camera_stream_inputs()

    assert result["source"] == "dreame_third_video_tx"
    assert result["tx_rtc_info"]["channel_id"] == "channel-1"
    assert result["tx_rtc_info"]["product_id"] == "product-1"
    assert result["tx_rtc_info"]["device_name"] == "Mower Camera"
    assert result["tx_rtc_info"]["secret_id"] == "secret-id-1"
    assert result["tx_rtc_info"]["secret_key"] == "secret-key-1"
    assert result["p2p_info"]["available"] is True
    assert result["p2p_info"]["p2p_info"] == "p2p-info-1"


def test_camera_stream_runtime_inputs_are_redactable_xp2p_contract() -> None:
    client = _client()
    client._sync_get_cloud_protocol = lambda: _FakeCloud()

    result = client._sync_get_camera_stream_runtime_inputs()

    assert result.ready is True
    assert result.xp2p_id == "product-1/Mower Camera"
    assert result.live_command == "action=live"
    assert result.missing_required == ()
    assert result.qcloud_credential_state == "complete"
    assert result.missing_qcloud_credentials == ()
    assert result.app_credential_state == "absent"
    assert result.missing_app_credentials == ("app_id", "app_secret")
    assert result.as_dict()["p2p_info"] == "p2p-info-1"
    redacted = result.as_dict(redact=True)
    assert "p2p_info" not in redacted
    assert redacted["p2p_info_present"] is True
    assert redacted["secret_key_present"] is True
    assert redacted["qcloud_credential_state"] == "complete"
    assert redacted["missing_qcloud_credentials"] == ()
    assert redacted["app_credential_state"] == "absent"
    assert redacted["missing_app_credentials"] == ("app_id", "app_secret")


def test_camera_stream_runtime_inputs_report_partial_vendor_credentials() -> None:
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="device-1",
        product_id="product-1",
        device_name="Mower Camera",
        p2p_info="p2p-info-1",
        secret_id="secret-id-1",
        app_secret="app-secret-1",
    )

    redacted = inputs.as_dict(redact=True)

    assert inputs.ready is True
    assert inputs.qcloud_credential_state == "partial"
    assert inputs.missing_qcloud_credentials == ("secret_key",)
    assert inputs.app_credential_state == "partial"
    assert inputs.missing_app_credentials == ("app_id",)
    assert redacted["qcloud_credential_state"] == "partial"
    assert redacted["missing_qcloud_credentials"] == ("secret_key",)
    assert redacted["app_credential_state"] == "partial"
    assert redacted["missing_app_credentials"] == ("app_id",)
    assert redacted["secret_id_present"] is True
    assert redacted["secret_key_present"] is False
