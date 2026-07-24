"""Regression checks for Codex review findings in the client helpers."""

from __future__ import annotations

import ssl
from dataclasses import replace
from hashlib import sha256
from importlib import import_module
from importlib.resources import files
from types import SimpleNamespace

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    mqtt_tls,
    protocol,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client import (
    _normalize_cloud_firmware_check,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.runtime_state import (
    snapshot_session_control_state,
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


def test_device_creation_without_external_listener_preserves_internal_callbacks(
    monkeypatch,
) -> None:
    class FakeDevice:
        def __init__(self, *args):
            del args
            self._property_update_callback = {1: [object()]}
            self.listen_calls = []

        def listen(self, callback) -> None:
            self.listen_calls.append(callback)
            if callback is None:
                self._property_update_callback = {}

    internal_device = import_module("_dreame_lawn_mower_client_internal.device")
    monkeypatch.setattr(internal_device, "DreameMowerDevice", FakeDevice)
    client = _client()

    created = client._ensure_device()

    assert created.listen_calls == []
    assert created._property_update_callback


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


def test_session_control_state_uses_active_heartbeat_at_dock() -> None:
    snapshot = replace(
        _snapshot(),
        task_status="paused",
        mowing_session_active=True,
    )

    assert snapshot_session_control_state(snapshot) == "paused"


def test_session_control_state_respects_explicit_inactive_heartbeat() -> None:
    snapshot = replace(
        _snapshot(),
        state="paused",
        mowing_session_active=False,
    )

    assert snapshot_session_control_state(snapshot) == "idle"


def test_session_control_state_falls_back_without_heartbeat_evidence() -> None:
    snapshot = replace(
        _snapshot(),
        state="returning",
        mowing_session_active=None,
    )

    assert snapshot_session_control_state(snapshot) == "returning"


def test_cloud_presence_throttles_empty_refresh_after_cached_success(
    monkeypatch,
) -> None:
    client = _client()
    calls = 0

    def empty_refresh(language: str | None = None):
        nonlocal calls
        calls += 1
        return None

    client._latest_cloud_device_info = {"online": True}
    client._cloud_device_info_refreshed_at = 0.0
    client._sync_get_cloud_device_info = empty_refresh
    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client."
        "time.monotonic",
        lambda: 100.0,
    )

    first = client._sync_get_cached_cloud_device_info()
    second = client._sync_get_cached_cloud_device_info()

    assert first == {"online": True}
    assert second == {"online": True}
    assert calls == 1
    assert client._cloud_device_info_refreshed_at == 100.0


def test_cloud_presence_throttles_failed_refresh_attempt(monkeypatch) -> None:
    client = _client()
    calls = 0

    def failed_refresh(language: str | None = None):
        nonlocal calls
        calls += 1
        raise DreameLawnMowerConnectionError("presence unavailable")

    client._sync_get_cloud_device_info = failed_refresh
    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client."
        "time.monotonic",
        lambda: 100.0,
    )

    try:
        client._sync_get_cached_cloud_device_info()
    except DreameLawnMowerConnectionError:
        pass
    else:
        raise AssertionError("Expected the first presence refresh to fail")

    assert client._sync_get_cached_cloud_device_info() is None
    assert calls == 1
    assert client._cloud_device_info_refreshed_at == 100.0


def test_cloud_mqtt_client_trusts_vendor_ca_with_verified_tls(monkeypatch) -> None:
    mqtt_clients = []

    class _MqttClient:
        def __init__(self, *args, **kwargs) -> None:
            self.tls_context = None
            self.tls_insecure = None
            self.connected_to = None
            mqtt_clients.append(self)

        def reconnect_delay_set(self, *args) -> None:
            pass

        def tls_set_context(self, context) -> None:
            self.tls_context = context

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
    context = mqtt_clients[0].tls_context
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert not context.verify_flags & ssl.VERIFY_X509_STRICT

    vendor_root_pem = (
        files(mqtt_tls.__package__)
        .joinpath("certs", "dreame_mqtt_root_ca.pem")
        .read_text(encoding="ascii")
    )
    vendor_root_der = ssl.PEM_cert_to_DER_cert(vendor_root_pem)
    assert sha256(vendor_root_der).hexdigest() == (
        "6db9ea84c7e4c9aec692cd540ff52381f5e37dae47df8ebeb948a4461aeae425"
    )
    assert vendor_root_der in context.get_ca_certs(binary_form=True)
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
