"""Regression checks for Codex review findings in the client helpers."""

from __future__ import annotations

import ssl
from types import SimpleNamespace

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import protocol
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client import (
    _normalize_cloud_firmware_check,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.runtime_state import (
    snapshot_with_cloud_presence,
)
from dreame_lawn_mower_client import DreameLawnMowerClient
from dreame_lawn_mower_client.client import DreameLawnMowerConnectionError
from dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
    DreameLawnMowerSnapshot,
)


def _client() -> DreameLawnMowerClient:
    return DreameLawnMowerClient(
        username="user@example.invalid",
        password="secret",
        country="eu",
        account_type="dreame",
        descriptor=DreameLawnMowerDescriptor(
            did="device-1",
            name="Garden Mower",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
    )


def _firmware_device() -> SimpleNamespace:
    return SimpleNamespace(
        info=SimpleNamespace(
            firmware_version="1.0.0",
            raw={},
        ),
        status=SimpleNamespace(),
        data={},
    )


def _snapshot(*, available: bool = True) -> DreameLawnMowerSnapshot:
    return DreameLawnMowerSnapshot(
        descriptor=_client().descriptor,
        available=available,
        state="charging_completed",
        state_name="Charging complete",
        activity="docked",
    )


def test_cloud_offline_presence_marks_cached_snapshot_unavailable() -> None:
    snapshot = snapshot_with_cloud_presence(_snapshot(), {"online": False})

    assert snapshot.online is False
    assert snapshot.available is False


def test_cloud_online_presence_keeps_successful_snapshot_available() -> None:
    snapshot = snapshot_with_cloud_presence(_snapshot(), {"online": True})

    assert snapshot.online is True
    assert snapshot.available is True


def test_cloud_mqtt_client_requires_verified_tls(monkeypatch) -> None:
    mqtt_clients = []

    class _MqttClient:
        def __init__(self, *args, **kwargs) -> None:
            self.tls_set_kwargs = None
            self.tls_insecure = None
            self.connected_to = None
            mqtt_clients.append(self)

        def reconnect_delay_set(self, *args) -> None:
            pass

        def tls_set(self, **kwargs) -> None:
            self.tls_set_kwargs = kwargs

        def tls_insecure_set(self, value) -> None:
            self.tls_insecure = value

        def username_pw_set(self, *args) -> None:
            pass

        def connect(self, host, port, keepalive) -> None:
            self.connected_to = (host, port, keepalive)

        def loop_start(self) -> None:
            pass

    monkeypatch.setattr(protocol.mqtt_client, "Client", _MqttClient)

    cloud = protocol.DreameMowerDreameHomeCloudProtocol(
        "user@example.invalid",
        "secret",
        "eu",
        "device-1",
    )
    cloud._logged_in = True
    cloud._uid = "uid-1"
    cloud._did = "device-1"
    cloud._model = "dreame.mower.g2408"
    cloud._host = "mqtt.example.invalid:8883"
    cloud._key = "client-key"
    cloud._uuid = "uuid-1"
    cloud._strings = [""] * 57
    cloud._strings[7] = "topic"
    cloud._strings[53] = "agent-"
    cloud._strings[54] = "-"
    monkeypatch.setattr(cloud, "get_device_info", lambda: {"did": "device-1"})

    result = cloud.connect(message_callback=lambda data: None)

    assert result == {"did": "device-1"}
    assert len(mqtt_clients) == 1
    assert mqtt_clients[0].tls_set_kwargs == {"cert_reqs": ssl.CERT_REQUIRED}
    assert mqtt_clients[0].tls_insecure is False
    assert mqtt_clients[0].connected_to == ("mqtt.example.invalid", 8883, 50)


def test_get_voice_settings_does_not_synthesize_prompt_flags() -> None:
    client = _client()
    client._sync_call_app_action = lambda payload: {  # type: ignore[method-assign]
        "m": "r",
        "r": 0,
        "d": {"LANG": [8, 13], "VOL": 100},
    }

    result = client._sync_get_voice_settings()

    assert result["available"] is True
    assert result["present_config_keys"] == ["LANG", "VOL"]
    assert result["voice_language_index"] == 13
    assert result["volume"] == 100
    assert "voice_prompts" not in result
    assert "general_prompt_voice" not in result


def test_get_voice_settings_requires_supported_keys() -> None:
    client = _client()
    client._sync_call_app_action = lambda payload: {  # type: ignore[method-assign]
        "m": "r",
        "r": 0,
        "d": {"OTHER": 1},
    }

    result = client._sync_get_voice_settings()

    assert result["available"] is False
    assert result["present_config_keys"] == []
    assert "voice_prompts" not in result


def test_get_firmware_update_support_skips_debug_catalog_by_default(
    monkeypatch,
) -> None:
    client = _client()

    monkeypatch.setattr(client, "_ensure_device", lambda: _firmware_device())
    monkeypatch.setattr(client, "_sync_get_cloud_device_info", lambda language: None)
    monkeypatch.setattr(
        client,
        "_sync_get_cloud_device_list_page",
        lambda current, size, language, master, shared_status: None,
    )
    monkeypatch.setattr(
        client,
        "_sync_get_cloud_firmware_check",
        lambda language: {
            "available": True,
            "update_available": False,
        },
    )
    monkeypatch.setattr(
        client,
        "_sync_get_batch_ota_info",
        lambda: {
            "available": True,
            "update_available": False,
        },
    )
    monkeypatch.setattr(
        client,
        "_sync_get_debug_ota_catalog",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("debug OTA catalog should be opt-in")
        ),
    )

    result = client._sync_get_firmware_update_support()

    assert result.debug_catalog_available is None
    assert "debug_ota_catalog" not in result.evidence


def test_get_firmware_update_support_fetches_debug_catalog_when_requested(
    monkeypatch,
) -> None:
    client = _client()

    monkeypatch.setattr(client, "_ensure_device", lambda: _firmware_device())
    monkeypatch.setattr(client, "_sync_get_cloud_device_info", lambda language: None)
    monkeypatch.setattr(
        client,
        "_sync_get_cloud_device_list_page",
        lambda current, size, language, master, shared_status: None,
    )
    monkeypatch.setattr(
        client,
        "_sync_get_cloud_firmware_check",
        lambda language: {
            "available": True,
            "update_available": False,
        },
    )
    monkeypatch.setattr(
        client,
        "_sync_get_batch_ota_info",
        lambda: {
            "available": True,
            "update_available": False,
        },
    )
    monkeypatch.setattr(
        client,
        "_sync_get_debug_ota_catalog",
        lambda **kwargs: {
            "source": "debug_ota_catalog",
            "available": True,
        },
    )

    result = client._sync_get_firmware_update_support(include_debug_ota_catalog=True)

    assert result.debug_catalog_available is True
    assert result.evidence["debug_ota_catalog"] == {
        "source": "debug_ota_catalog",
        "available": True,
    }


def test_get_firmware_update_support_still_checks_firmware_after_metadata_error(
    monkeypatch,
) -> None:
    client = _client()

    monkeypatch.setattr(client, "_ensure_device", lambda: _firmware_device())
    monkeypatch.setattr(
        client,
        "_sync_get_cloud_device_info",
        lambda language: (_ for _ in ()).throw(
            DreameLawnMowerConnectionError("device info unavailable")
        ),
    )
    monkeypatch.setattr(
        client,
        "_sync_get_cloud_device_list_page",
        lambda current, size, language, master, shared_status: None,
    )
    monkeypatch.setattr(
        client,
        "_sync_get_cloud_firmware_check",
        lambda language: {
            "available": True,
            "update_available": True,
            "latest_version": "1.1.0",
        },
    )
    monkeypatch.setattr(
        client,
        "_sync_get_batch_ota_info",
        lambda: {
            "available": True,
            "update_available": False,
        },
    )

    result = client._sync_get_firmware_update_support()

    assert result.cloud_check_available is True
    assert result.cloud_check_update_available is True
    assert result.latest_version == "1.1.0"
    assert result.cloud_error is not None
    assert "cloud_device_info: device info unavailable" in result.cloud_error


def test_cloud_firmware_check_marks_error_response_unavailable(monkeypatch) -> None:
    client = _client()

    class _Cloud:
        def check_device_version(self, language=None):
            return {
                "code": 500,
                "success": False,
                "msg": "backend unavailable",
            }

    monkeypatch.setattr(client, "_sync_get_cloud_protocol", lambda: _Cloud())
    monkeypatch.setattr(client, "_ensure_device", lambda: _firmware_device())

    result = client._sync_get_cloud_firmware_check()

    assert result["available"] is False
    assert result["update_available"] is None
    assert result["errors"] == [
        {
            "stage": "response",
            "error": "cloud_error",
            "code": 500,
            "success": False,
            "msg": "backend unavailable",
        }
    ]


def test_cloud_firmware_check_flattens_structured_release_notes() -> None:
    result = _normalize_cloud_firmware_check(
        {
            "curVersion": "4.3.6_0447",
            "newVersion": "4.3.6_0550",
            "hasNewFirmware": True,
            "description": (
                '{"en":["1. Optimize WiFi connection experience.",'
                '{"content":"2. Optimize recharge logic.<br>3. Improve stability."},'
                '{"detail":"4. Fix known issues."}]}'
            ),
        }
    )

    assert result["changelog_available"] is True
    assert result["changelog"] == (
        "1. Optimize WiFi connection experience.\n"
        "2. Optimize recharge logic.\n"
        "3. Improve stability.\n"
        "4. Fix known issues."
    )


def test_cloud_firmware_check_keeps_error_description_unavailable() -> None:
    result = _normalize_cloud_firmware_check(
        {
            "curVersion": "4.3.6_0320",
            "newVersion": "4.3.6_0447",
            "hasNewFirmware": True,
            "description": '{"code":10005,"success":false,"msg":"missing lang"}',
        }
    )

    assert result["changelog"] is None
    assert result["changelog_available"] is False
    assert result["changelog_error"] == {
        "code": 10005,
        "success": False,
        "msg": "missing lang",
    }
