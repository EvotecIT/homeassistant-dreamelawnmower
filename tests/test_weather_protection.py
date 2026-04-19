"""Regression checks for read-only weather/rain protection probing."""

from __future__ import annotations

from dreame_lawn_mower_client import DreameLawnMowerClient
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor


class _FakeWeatherCloud:
    logged_in = True

    def __init__(self, *, rpet_error: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.rpet_error = rpet_error

    def call_app_action(
        self,
        payload: dict[str, object],
        *,
        siid: int = 2,
        aiid: int = 50,
    ) -> dict[str, object]:
        assert siid == 2
        assert aiid == 50
        self.calls.append(payload)
        command = payload.get("t")
        if command == "CFG":
            return {
                "out": [
                    {
                        "m": "r",
                        "r": 0,
                        "d": {
                            "WRF": True,
                            "WRP": [1, 8],
                            "rainProtectEndTime": 1776600000,
                        },
                    }
                ]
            }
        if command == "RPET":
            if self.rpet_error:
                raise RuntimeError("not protecting")
            return {"out": [{"m": "r", "r": 0, "d": {"endTime": 1776600300}}]}
        raise AssertionError(f"Unexpected app command: {payload}")


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


def test_get_weather_protection_uses_read_only_app_actions() -> None:
    client = _client()
    cloud = _FakeWeatherCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_weather_protection()

    assert result["source"] == "app_action_weather_protection"
    assert result["available"] is True
    assert result["fault_hint"] == "INFO_BAD_WEATHER_PROTECTING"
    assert result["present_config_keys"] == ["WRF", "WRP"]
    assert result["weather_switch_enabled"] is True
    assert result["rain_protection_enabled"] is True
    assert result["rain_protection_duration_hours"] == 8
    assert result["rain_sensor_sensitivity"] == 0
    assert result["rain_protection_raw"] == [1, 8, 0]
    assert result["rain_protect_end_time"] == 1776600300
    assert result["errors"] == []
    assert result["warnings"] == []
    assert [call["t"] for call in cloud.calls] == ["CFG", "RPET"]


def test_get_weather_protection_keeps_config_when_rpet_is_unavailable() -> None:
    client = _client()
    cloud = _FakeWeatherCloud(rpet_error=True)
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_weather_protection()

    assert result["available"] is True
    assert result["present_config_keys"] == ["WRF", "WRP"]
    assert result["rain_protection_enabled"] is True
    assert result["errors"] == []
    assert result["warnings"] == [
        {"stage": "rain_end_time", "warning": "not protecting"}
    ]
