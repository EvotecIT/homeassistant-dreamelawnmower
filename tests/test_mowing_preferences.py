"""Regression checks for mower preference app-action probing."""

from __future__ import annotations

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    decode_mowing_preference_payload,
    summarize_mowing_preference_info,
)
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor


class _FakePreferenceCloud:
    logged_in = True

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

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
        if command == "MAPL":
            return {
                "out": [
                    {
                        "m": "r",
                        "r": 0,
                        "d": [[0, 1, 1, 1, 0], [1, 0, 1, 1, 0]],
                    }
                ]
            }
        if command == "PREI":
            idx = int(payload["d"]["idx"])
            data = {"type": 1, "ver": [[11, 8], [12, 9]]} if idx == 0 else {
                "type": 0,
                "ver": [],
            }
            return {"out": [{"m": "r", "r": 0, "d": data}]}
        if command == "PRE":
            region = int(payload["d"]["region"])
            payload_data = [
                8,
                0,
                region,
                1,
                40,
                2,
                90,
                1,
                0,
                1,
                1,
                2,
                1,
                15,
                20,
                7,
                1,
            ]
            return {"out": [{"m": "r", "r": 0, "d": payload_data}]}
        raise AssertionError(f"Unexpected app command: {payload}")


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


def test_decode_mowing_preference_payload_names_known_fields() -> None:
    decoded = decode_mowing_preference_payload(
        [8, 0, 11, 1, 40, 2, 90, 1, 0, 1, 1, 2, 1, 15, 20, 7, 1]
    )

    assert decoded == {
        "version": 8,
        "map_index": 0,
        "area_id": 11,
        "efficient_mode": 1,
        "efficient_mode_name": "efficient",
        "mowing_height_cm": 4.0,
        "mowing_direction_mode": 2,
        "mowing_direction_mode_name": "checkerboard",
        "mowing_direction_degrees": 90,
        "edge_mowing_auto": True,
        "edge_mowing_walk_mode": 0,
        "edge_mowing_walk_mode_name": "line",
        "edge_mowing_obstacle_avoidance": True,
        "cutter_position": 1,
        "cutter_position_name": "left",
        "edge_mowing_num": 2,
        "obstacle_avoidance_enabled": True,
        "obstacle_avoidance_height_cm": 15,
        "obstacle_avoidance_distance_cm": 20,
        "obstacle_avoidance_ai": 7,
        "obstacle_avoidance_ai_classes": ["people", "animals", "objects"],
        "edge_mowing_safe": True,
    }


def test_summarize_mowing_preference_info_decodes_mode_and_area_versions() -> None:
    summary = summarize_mowing_preference_info({"type": 1, "ver": [[11, 8]]})

    assert summary == {
        "valid": True,
        "mode": 1,
        "mode_name": "custom",
        "area_count": 1,
        "areas": [{"area_id": 11, "version": 8}],
    }


def test_get_mowing_preferences_uses_read_only_app_actions() -> None:
    client = _client()
    cloud = _FakePreferenceCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_mowing_preferences()

    assert result["source"] == "app_action_mowing_preferences"
    assert result["available"] is True
    assert result["property_hint"] == "2.52"
    assert [entry["idx"] for entry in result["maps"]] == [0, 1]
    assert result["maps"][0]["mode_name"] == "custom"
    assert result["maps"][0]["area_count"] == 2
    assert result["maps"][0]["preferences"][0]["area_id"] == 11
    assert result["maps"][0]["preferences"][0]["reported_version"] == 8
    assert result["maps"][0]["preferences"][0]["mowing_height_cm"] == 4.0
    assert result["maps"][1]["mode_name"] == "global"
    assert result["maps"][1]["preferences"] == []
    assert [call["t"] for call in cloud.calls] == [
        "MAPL",
        "PREI",
        "PRE",
        "PRE",
        "PREI",
    ]


def test_get_mowing_preferences_can_limit_maps_and_include_raw() -> None:
    client = _client()
    cloud = _FakePreferenceCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_mowing_preferences(include_raw=True, map_indices=[0])

    assert [entry["idx"] for entry in result["maps"]] == [0]
    assert "raw_info" in result["maps"][0]
    assert "raw_response" in result["maps"][0]["preferences"][0]
    assert [call["t"] for call in cloud.calls] == ["PREI", "PRE", "PRE"]
