"""Regression checks for mower preference app-action probing."""

from __future__ import annotations

import pytest

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    apply_mowing_preference_changes,
    decode_mowing_preference_payload,
    encode_mowing_preference_payload,
    normalize_mowing_preference_mode,
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
            data = (
                {"type": 1, "ver": [[11, 8], [12, 9]]}
                if idx == 0
                else {
                    "type": 0,
                    "ver": [],
                }
            )
            return {"out": [{"m": "r", "r": 0, "d": data}]}
        if command == "PRE":
            if payload.get("m") == "s":
                return {"out": [{"m": "r", "r": 0, "d": {"r": 0, "ok": True}}]}
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
        if command == "PREP":
            return {"out": [{"m": "r", "r": 0, "d": {"r": 0, "ok": True}}]}
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


def test_normalize_mowing_preference_mode_accepts_labels_and_ints() -> None:
    assert normalize_mowing_preference_mode("global") == 0
    assert normalize_mowing_preference_mode("custom") == 1
    assert normalize_mowing_preference_mode("1") == 1


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


def test_encode_mowing_preference_payload_round_trips_decoded_values() -> None:
    decoded = decode_mowing_preference_payload(
        [8, 0, 11, 1, 40, 2, 90, 1, 0, 1, 1, 2, 1, 15, 20, 7, 1]
    )
    decoded["reported_version"] = 8

    assert encode_mowing_preference_payload(decoded) == [
        8,
        0,
        11,
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


def test_apply_mowing_preference_changes_updates_labels_and_ai_classes() -> None:
    current = decode_mowing_preference_payload(
        [8, 0, 11, 1, 40, 2, 90, 1, 0, 1, 1, 2, 1, 15, 20, 7, 1]
    )
    current["reported_version"] = 8

    updated, changed_fields = apply_mowing_preference_changes(
        current,
        {
            "mowing_height_cm": 5,
            "efficient_mode": 0,
            "obstacle_avoidance_ai_classes": ["animals"],
        },
    )

    assert changed_fields == [
        "mowing_height_cm",
        "efficient_mode",
        "obstacle_avoidance_ai_classes",
    ]
    assert updated["mowing_height_cm"] == 5.0
    assert updated["efficient_mode"] == 0
    assert updated["efficient_mode_name"] == "standard"
    assert updated["obstacle_avoidance_ai"] == 2
    assert updated["obstacle_avoidance_ai_classes"] == ["animals"]


def test_plan_app_mowing_preference_update_builds_candidate_request() -> None:
    client = _client()
    cloud = _FakePreferenceCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_plan_app_mowing_preference_update(
        map_index=0,
        area_id=11,
        changes={
            "mowing_height_cm": 5,
            "edge_mowing_auto": False,
            "obstacle_avoidance_ai_classes": ["people", "objects"],
        },
    )

    assert result["dry_run"] is True
    assert result["executed"] is False
    assert result["execute_supported"] is True
    assert result["request_verified"] is False
    assert result["changed"] is True
    assert result["changed_fields"] == [
        "mowing_height_cm",
        "edge_mowing_auto",
        "obstacle_avoidance_ai_classes",
    ]
    assert result["mode_name"] == "custom"
    assert result["map"] == {
        "idx": 0,
        "label": "map_0",
        "available": True,
        "mode": 1,
        "mode_name": "custom",
        "area_count": 2,
        "preference_count": 2,
    }
    assert result["previous_preference"]["mowing_height_cm"] == 4.0
    assert result["updated_preference"]["mowing_height_cm"] == 5.0
    assert result["updated_preference"]["edge_mowing_auto"] is False
    assert result["updated_preference"]["obstacle_avoidance_ai"] == 5
    assert result["updated_preference"]["obstacle_avoidance_ai_classes"] == [
        "people",
        "objects",
    ]
    assert result["payload"] == [8, 0, 11, 1, 50, 2, 90, 0, 0, 1, 1, 2, 1, 15, 20, 5, 1]
    assert result["request_candidate"] == {
        "m": "s",
        "t": "PRE",
        "d": [8, 0, 11, 1, 50, 2, 90, 0, 0, 1, 1, 2, 1, 15, 20, 5, 1],
    }
    assert [call["t"] for call in cloud.calls] == ["PREI", "PRE", "PRE"]


def test_plan_app_mowing_preference_update_can_execute_confirmed_request() -> None:
    client = _client()
    cloud = _FakePreferenceCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_plan_app_mowing_preference_update(
        map_index=0,
        area_id=11,
        changes={
            "mowing_height_cm": 5,
        },
        execute=True,
        confirm_write=True,
    )

    assert result["dry_run"] is False
    assert result["executed"] is True
    assert result["execute_supported"] is True
    assert result["request_verified"] is True
    assert result["response_data"] == {"r": 0, "ok": True}
    assert [call["t"] for call in cloud.calls] == ["PREI", "PRE", "PRE", "PRE"]


def test_plan_app_mowing_preference_update_can_build_mode_only_request() -> None:
    client = _client()
    cloud = _FakePreferenceCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_plan_app_mowing_preference_update(
        map_index=0,
        area_id=None,
        changes={"preference_mode": "global"},
    )

    assert result["dry_run"] is True
    assert result["area_id"] is None
    assert result["mode_name"] == "custom"
    assert result["target_mode"] == 0
    assert result["target_mode_name"] == "global"
    assert result["mode_changed"] is True
    assert result["changed_fields"] == ["preference_mode"]
    assert result["changes"] == {"preference_mode": "global"}
    assert result["payload"] is None
    assert result["request_candidate"] == {
        "m": "s",
        "t": "PREP",
        "d": {"idx": 0, "value": 0},
    }
    assert result["request_candidates"] == [
        {"m": "s", "t": "PREP", "d": {"idx": 0, "value": 0}}
    ]
    assert [call["t"] for call in cloud.calls] == ["PREI", "PRE", "PRE"]


def test_plan_app_mowing_preference_update_can_execute_mode_only_request() -> None:
    client = _client()
    cloud = _FakePreferenceCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_plan_app_mowing_preference_update(
        map_index=0,
        area_id=None,
        changes={"preference_mode": 0},
        execute=True,
        confirm_write=True,
    )

    assert result["executed"] is True
    assert result["request_verified"] is True
    assert result["response_data"] == {"r": 0, "ok": True}
    assert [call["t"] for call in cloud.calls] == ["PREI", "PRE", "PRE", "PREP"]


def test_plan_app_mowing_preference_update_can_execute_mode_and_settings_sequence() -> (
    None
):
    client = _client()
    requests: list[dict[str, object]] = []
    client._sync_get_mowing_preferences = lambda *args, **kwargs: {
        "available": True,
        "maps": [
            {
                "idx": 1,
                "mode": 0,
                "mode_name": "global",
                "area_count": 1,
                "preferences": [
                    {
                        "version": 10,
                        "map_index": 1,
                        "area_id": 5,
                        "reported_version": 10,
                        "efficient_mode": 1,
                        "mowing_height_cm": 3.5,
                        "mowing_direction_mode": 1,
                        "mowing_direction_degrees": 10,
                        "edge_mowing_auto": True,
                        "edge_mowing_walk_mode": 0,
                        "edge_mowing_obstacle_avoidance": True,
                        "cutter_position": 0,
                        "edge_mowing_num": 1,
                        "obstacle_avoidance_enabled": True,
                        "obstacle_avoidance_height_cm": 5,
                        "obstacle_avoidance_distance_cm": 10,
                        "obstacle_avoidance_ai": 7,
                        "obstacle_avoidance_ai_classes": [
                            "people",
                            "animals",
                            "objects",
                        ],
                        "edge_mowing_safe": True,
                    }
                ],
            }
        ],
    }
    client._sync_call_app_action = lambda request: (
        requests.append(request) or {"r": 0, "d": {"r": 0, "ok": True}}
    )

    result = client._sync_plan_app_mowing_preference_update(
        map_index=1,
        area_id=5,
        changes={"preference_mode": "custom", "mowing_height_cm": 4.0},
        execute=True,
        confirm_write=True,
    )

    assert result["executed"] is True
    assert result["changed_fields"] == ["preference_mode", "mowing_height_cm"]
    assert result["request_candidate"] == {
        "sequence": [
            {"m": "s", "t": "PREP", "d": {"idx": 1, "value": 1}},
            {
                "m": "s",
                "t": "PRE",
                "d": [10, 1, 5, 1, 40, 1, 170, 1, 0, 1, 0, 1, 1, 5, 10, 7, 1],
            },
        ]
    }
    assert result["request_candidates"] == [
        {"m": "s", "t": "PREP", "d": {"idx": 1, "value": 1}},
        {
            "m": "s",
            "t": "PRE",
            "d": [10, 1, 5, 1, 40, 1, 170, 1, 0, 1, 0, 1, 1, 5, 10, 7, 1],
        },
    ]
    assert result["response_data"] == [{"r": 0, "ok": True}, {"r": 0, "ok": True}]
    assert [request["t"] for request in requests] == ["PREP", "PRE"]


def test_plan_app_mowing_preference_update_rejects_global_mode_with_zone_changes() -> (
    None
):
    client = _client()
    cloud = _FakePreferenceCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    with pytest.raises(ValueError, match="preference_mode=global"):
        client._sync_plan_app_mowing_preference_update(
            map_index=0,
            area_id=11,
            changes={"preference_mode": "global", "mowing_height_cm": 5},
        )


def test_plan_app_mowing_preference_update_rejects_unconfirmed_execute() -> None:
    client = _client()

    with pytest.raises(ValueError, match="confirm_write=True"):
        client._sync_plan_app_mowing_preference_update(
            map_index=0,
            area_id=11,
            changes={"mowing_height_cm": 5},
            execute=True,
            confirm_write=False,
        )
