"""Regression checks for Codex review findings in the client helpers."""

from __future__ import annotations

from types import SimpleNamespace

from dreame_lawn_mower_client import DreameLawnMowerClient
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor


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
