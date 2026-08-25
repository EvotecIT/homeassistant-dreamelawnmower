"""Regression checks for mower preference app-action probing."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
    apply_mowing_preference_changes,
    decode_mowing_preference_payload,
    encode_mowing_preference_payload,
    individually_writable_obstacle_ai_classes,
    normalize_mowing_preference_mode,
    summarize_mowing_preference_info,
)
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor


class _FakePreferenceCloud:
    logged_in = True

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.modes = {0: 1, 1: 0}
        self.preference_payloads: dict[tuple[int, int], list[int]] = {}

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
                {"type": self.modes[idx], "ver": [[11, 8], [12, 9]]}
                if idx == 0
                else {
                    "type": self.modes[idx],
                    "ver": [],
                }
            )
            return {"out": [{"m": "r", "r": 0, "d": data}]}
        if command == "PRE":
            if payload.get("m") == "s":
                preference_payload = list(payload["d"])
                self.preference_payloads[
                    (int(preference_payload[1]), int(preference_payload[2]))
                ] = preference_payload
                return {"out": [{"m": "r", "r": 0, "d": {"r": 0, "ok": True}}]}
            idx = int(payload["d"]["idx"])
            region = int(payload["d"]["region"])
            payload_data = self.preference_payloads.get(
                (idx, region),
                [
                    8,
                    idx,
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
                ],
            )
            return {"out": [{"m": "r", "r": 0, "d": payload_data}]}
        if command == "PREP":
            self.modes[int(payload["d"]["idx"])] = int(payload["d"]["value"])
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


def _preference_result(preference: dict[str, object]) -> dict[str, object]:
    return {
        "available": True,
        "maps": [
            {
                "idx": 0,
                "label": "map_0",
                "available": True,
                "mode": 0,
                "mode_name": "global",
                "area_count": 1,
                "preferences": [preference],
            }
        ],
    }


def test_decode_mowing_preference_payload_names_known_fields() -> None:
    decoded = decode_mowing_preference_payload(
        [8, 0, 11, 1, 40, 2, 35, 1, 0, 1, 1, 2, 1, 15, 20, 7, 1]
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
        "mowing_direction_method_name": "checkerboard",
        "mowing_direction_degrees": 35,
        "edge_mowing_auto": True,
        "edge_mowing_walk_mode": 0,
        "edge_mowing_walk_mode_name": "line",
        "turning_method_name": "lawn_care",
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
        "obstacle_avoidance_sensitivity": None,
        "edge_cutting_attachment": None,
        "steering_mode": None,
        "cutter_position_height": None,
        "_raw_payload": (
            8,
            0,
            11,
            1,
            40,
            2,
            35,
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
        ),
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


@pytest.mark.parametrize(
    ("payload_map_index", "payload_area_id"),
    [(1, 11), (0, 12)],
)
def test_get_mowing_preferences_rejects_mismatched_payload_identity(
    payload_map_index: int,
    payload_area_id: int,
) -> None:
    client = _client()
    cloud = _FakePreferenceCloud()
    call_app_action = cloud.call_app_action

    def mismatched_call(
        payload: dict[str, object],
        *,
        siid: int = 2,
        aiid: int = 50,
    ) -> dict[str, object]:
        result = call_app_action(payload, siid=siid, aiid=aiid)
        if payload.get("t") == "PREI" and payload.get("m") == "g":
            result["out"][0]["d"]["ver"] = [[11, 8]]
        if payload.get("t") == "PRE" and payload.get("m") == "g":
            preference = result["out"][0]["d"]
            preference[1] = payload_map_index
            preference[2] = payload_area_id
        return result

    cloud.call_app_action = mismatched_call  # type: ignore[method-assign]
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_mowing_preferences(map_indices=[0])

    assert result["available"] is False
    assert result["maps"][0]["preferences"] == []
    assert "mismatched preference identity" in result["maps"][0]["error"]


def test_get_mowing_preferences_preserves_unaffected_areas() -> None:
    client = _client()
    cloud = _FakePreferenceCloud()
    call_app_action = cloud.call_app_action

    def fail_unrelated_area(
        payload: dict[str, object],
        *,
        siid: int = 2,
        aiid: int = 50,
    ) -> dict[str, object]:
        if (
            payload.get("m") == "g"
            and payload.get("t") == "PRE"
            and payload.get("d") == {"idx": 0, "region": 12}
        ):
            cloud.calls.append(payload)
            return {"out": [{"m": "r", "r": 0, "d": None}]}
        return call_app_action(payload, siid=siid, aiid=aiid)

    cloud.call_app_action = fail_unrelated_area  # type: ignore[method-assign]
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_mowing_preferences(map_indices=[0])

    assert result["available"] is True
    assert [item["area_id"] for item in result["maps"][0]["preferences"]] == [11]
    assert result["maps"][0]["available"] is True
    assert result["maps"][0]["errors"] == [
        {
            "idx": 0,
            "area_id": 12,
            "stage": "preference",
            "error": "PRE returned invalid preference data for map 0 area 12.",
        }
    ]
    assert result["errors"] == result["maps"][0]["errors"]


def test_encode_mowing_preference_payload_round_trips_decoded_values() -> None:
    decoded = decode_mowing_preference_payload(
        [8, 0, 11, 1, 40, 2, 35, 1, 0, 1, 1, 2, 1, 15, 20, 7, 1]
    )
    decoded["reported_version"] = 8

    assert encode_mowing_preference_payload(decoded) == [
        8,
        0,
        11,
        1,
        40,
        2,
        35,
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


def test_encode_mowing_preference_payload_preserves_newer_vendor_fields() -> None:
    payload = [
        12,
        0,
        0,
        1,
        40,
        1,
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
        3,
        1,
        2,
        35,
        99,
    ]
    decoded = decode_mowing_preference_payload(payload)

    updated, changed_fields = apply_mowing_preference_changes(
        decoded,
        {"mowing_height_cm": 5.0},
    )

    assert changed_fields == ["mowing_height_cm"]
    assert updated["obstacle_avoidance_sensitivity"] == 3
    assert updated["edge_cutting_attachment"] is True
    assert updated["steering_mode"] == 2
    assert updated["cutter_position_height"] == 35
    assert encode_mowing_preference_payload(updated) == [
        *payload[:4],
        50,
        *payload[5:],
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


def test_lidax_2000_awd_exposes_only_individually_writable_ai_class() -> None:
    assert individually_writable_obstacle_ai_classes("mova.mower.g2584a") == (
        "objects",
    )
    assert individually_writable_obstacle_ai_classes("dreame.mower.g2408") == (
        "people",
        "animals",
        "objects",
    )


def test_lidax_2000_awd_rejects_individual_people_or_animals_change() -> None:
    current = decode_mowing_preference_payload(
        [8, 0, 0, 1, 40, 2, 90, 1, 0, 1, 1, 2, 1, 15, 20, 4, 1]
    )

    with pytest.raises(
        ValueError,
        match="does not support changing people or animals independently",
    ):
        apply_mowing_preference_changes(
            current,
            {"obstacle_avoidance_ai_classes": ["people", "objects"]},
            model="mova.mower.g2584a",
        )

    updated, changed_fields = apply_mowing_preference_changes(
        current,
        {"obstacle_avoidance_ai_classes": []},
        model="mova.mower.g2584a",
    )

    assert changed_fields == ["obstacle_avoidance_ai_classes"]
    assert updated["obstacle_avoidance_ai_classes"] == []


@pytest.mark.parametrize(
    ("field", "value", "maximum"),
    [
        ("mowing_direction_mode", 3, 2),
        ("edge_mowing_walk_mode", 2, 1),
    ],
)
def test_apply_mowing_preference_changes_rejects_unknown_modes(
    field: str,
    value: int,
    maximum: int,
) -> None:
    current = decode_mowing_preference_payload(
        [8, 0, 11, 1, 40, 1, 35, 1, 0, 1, 1, 2, 1, 15, 20, 7, 1]
    )

    with pytest.raises(ValueError, match=rf"{field} must be between 0 and {maximum}"):
        apply_mowing_preference_changes(current, {field: value})


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
    assert result["verification_source"] == "preference_readback"
    assert result["response_data"] == {"r": 0, "ok": True}
    assert [call["t"] for call in cloud.calls] == [
        "PREI",
        "PRE",
        "PRE",
        "PRE",
        "PREI",
        "PRE",
        "PRE",
    ]


def test_preference_write_accepts_data_less_success_after_exact_readback() -> None:
    client = _client()
    before = decode_mowing_preference_payload(
        [220, 0, 0, 1, 40, 2, 90, 1, 0, 1, 1, 2, 1, 15, 20, 7, 1, 2, 0, 1, 35]
    )
    after = decode_mowing_preference_payload(
        [221, 0, 0, 1, 40, 2, 90, 1, 0, 1, 1, 2, 1, 15, 20, 3, 1, 2, 0, 1, 35]
    )
    reads = iter(
        [
            _preference_result(before),
            _preference_result(after),
        ]
    )
    requests: list[dict[str, object]] = []
    client._sync_get_mowing_preferences = lambda **kwargs: next(reads)  # type: ignore[method-assign]  # noqa: ARG005
    client._sync_call_app_action = lambda request: (  # type: ignore[method-assign]
        requests.append(request) or {"m": "r", "q": 42, "r": 0}
    )

    result = client._sync_plan_app_mowing_preference_update(
        map_index=0,
        area_id=0,
        changes={"obstacle_avoidance_ai_classes": ["people", "animals"]},
        execute=True,
        confirm_write=True,
    )

    assert result["executed"] is True
    assert result["request_verified"] is True
    assert result["verification_source"] == "preference_readback"
    assert result["response_data"] is None
    assert result["readback"]["preference"]["version"] == 221
    assert result["readback"]["preference"]["obstacle_avoidance_ai_classes"] == [
        "people",
        "animals",
    ]
    assert result["readback"]["preference"]["obstacle_avoidance_sensitivity"] == 2
    assert result["readback"]["preference"]["steering_mode"] == 1
    assert result["readback"]["preference"]["cutter_position_height"] == 35
    assert requests == [
        {
            "m": "s",
            "t": "PRE",
            "d": [
                220,
                0,
                0,
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
                3,
                1,
                2,
                0,
                1,
                35,
            ],
        }
    ]


def test_preference_write_rejects_acknowledgement_without_matching_readback() -> None:
    client = _client()
    unchanged = decode_mowing_preference_payload(
        [220, 0, 0, 1, 40, 2, 90, 1, 0, 1, 1, 2, 1, 15, 20, 7, 1]
    )
    client._sync_get_mowing_preferences = lambda **kwargs: _preference_result(  # type: ignore[method-assign]  # noqa: ARG005
        unchanged
    )
    client._sync_call_app_action = lambda request: {  # type: ignore[method-assign]  # noqa: ARG005
        "m": "r",
        "r": 0,
        "d": {"r": 0, "ok": True},
    }

    with (
        patch(
            "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
            "client_settings.time.sleep"
        ) as sleep,
        pytest.raises(
            DreameLawnMowerCommandRejectedError,
            match="did not confirm.*obstacle_avoidance_ai_classes",
        ),
    ):
        client._sync_plan_app_mowing_preference_update(
            map_index=0,
            area_id=0,
            changes={"obstacle_avoidance_ai_classes": ["people", "animals"]},
            execute=True,
            confirm_write=True,
        )

    assert [call.args[0] for call in sleep.call_args_list] == [1.0, 2.0]


def test_preference_write_retries_until_exact_readback_catches_up() -> None:
    client = _client()
    before = decode_mowing_preference_payload(
        [220, 0, 0, 1, 40, 2, 90, 1, 0, 1, 1, 2, 1, 15, 20, 7, 1]
    )
    after = decode_mowing_preference_payload(
        [221, 0, 0, 1, 40, 2, 90, 1, 0, 1, 1, 2, 1, 15, 20, 3, 1]
    )
    reads = iter(
        [
            _preference_result(before),
            _preference_result(before),
            _preference_result(after),
        ]
    )
    client._sync_get_mowing_preferences = lambda **kwargs: next(reads)  # type: ignore[method-assign]  # noqa: ARG005
    client._sync_call_app_action = lambda request: {  # type: ignore[method-assign]  # noqa: ARG005
        "m": "r",
        "r": 0,
        "d": {"r": 0, "ok": True},
    }

    with patch(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
        "client_settings.time.sleep"
    ) as sleep:
        result = client._sync_plan_app_mowing_preference_update(
            map_index=0,
            area_id=0,
            changes={"obstacle_avoidance_ai_classes": ["people", "animals"]},
            execute=True,
            confirm_write=True,
        )

    assert result["executed"] is True
    assert result["request_verified"] is True
    assert result["readback"]["preference"]["version"] == 221
    sleep.assert_called_once_with(1.0)


def test_preference_write_ignores_unrelated_area_readback_failure() -> None:
    client = _client()
    cloud = _FakePreferenceCloud()
    call_app_action = cloud.call_app_action

    def fail_unrelated_area(
        payload: dict[str, object],
        *,
        siid: int = 2,
        aiid: int = 50,
    ) -> dict[str, object]:
        if (
            payload.get("m") == "g"
            and payload.get("t") == "PRE"
            and payload.get("d") == {"idx": 0, "region": 12}
        ):
            cloud.calls.append(payload)
            return {"out": [{"m": "r", "r": 0, "d": None}]}
        return call_app_action(payload, siid=siid, aiid=aiid)

    cloud.call_app_action = fail_unrelated_area  # type: ignore[method-assign]
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_plan_app_mowing_preference_update(
        map_index=0,
        area_id=11,
        changes={"mowing_height_cm": 5.0},
        execute=True,
        confirm_write=True,
    )

    assert result["executed"] is True
    assert result["request_verified"] is True
    assert result["readback"]["preference"]["area_id"] == 11
    assert result["readback"]["preference"]["mowing_height_cm"] == 5.0


def test_preference_write_fails_closed_when_target_area_readback_fails() -> None:
    client = _client()
    cloud = _FakePreferenceCloud()
    call_app_action = cloud.call_app_action
    write_started = False

    def fail_target_after_write(
        payload: dict[str, object],
        *,
        siid: int = 2,
        aiid: int = 50,
    ) -> dict[str, object]:
        nonlocal write_started
        if payload.get("m") == "s" and payload.get("t") == "PRE":
            write_started = True
        if (
            write_started
            and payload.get("m") == "g"
            and payload.get("t") == "PRE"
            and payload.get("d") == {"idx": 0, "region": 11}
        ):
            cloud.calls.append(payload)
            return {"out": [{"m": "r", "r": 0, "d": None}]}
        return call_app_action(payload, siid=siid, aiid=aiid)

    cloud.call_app_action = fail_target_after_write  # type: ignore[method-assign]
    client._sync_get_cloud_protocol = lambda: cloud

    with (
        patch(
            "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
            "client_settings.time.sleep"
        ),
        pytest.raises(
            DreameLawnMowerConnectionError,
            match="did not return the target area",
        ),
    ):
        client._sync_plan_app_mowing_preference_update(
            map_index=0,
            area_id=11,
            changes={"mowing_height_cm": 5.0},
            execute=True,
            confirm_write=True,
        )


def test_preference_write_still_rejects_failed_outer_response() -> None:
    client = _client()
    current = decode_mowing_preference_payload(
        [220, 0, 0, 1, 40, 2, 90, 1, 0, 1, 1, 2, 1, 15, 20, 7, 1]
    )
    client._sync_get_mowing_preferences = lambda **kwargs: _preference_result(  # type: ignore[method-assign]  # noqa: ARG005
        current
    )
    client._sync_call_app_action = lambda request: {  # type: ignore[method-assign]  # noqa: ARG005
        "m": "r",
        "r": 5,
    }

    with pytest.raises(DreameLawnMowerConnectionError, match="did not acknowledge"):
        client._sync_plan_app_mowing_preference_update(
            map_index=0,
            area_id=0,
            changes={"obstacle_avoidance_ai_classes": ["people", "animals"]},
            execute=True,
            confirm_write=True,
        )


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
    assert result["verification_source"] == "preference_readback"
    assert result["response_data"] == {"r": 0, "ok": True}
    assert [call["t"] for call in cloud.calls] == [
        "PREI",
        "PRE",
        "PRE",
        "PREP",
        "PREI",
        "PRE",
        "PRE",
    ]


def test_preference_write_accepts_data_less_mode_success_after_exact_readback() -> (
    None
):
    client = _client()
    before = _preference_result(
        decode_mowing_preference_payload(
            [220, 0, 0, 1, 40, 2, 90, 1, 0, 1, 1, 2, 1, 15, 20, 7, 1]
        )
    )
    after = _preference_result(
        decode_mowing_preference_payload(
            [220, 0, 0, 1, 40, 2, 90, 1, 0, 1, 1, 2, 1, 15, 20, 7, 1]
        )
    )
    after["maps"][0]["mode"] = 1
    after["maps"][0]["mode_name"] = "custom"
    reads = iter([before, after])
    client._sync_get_mowing_preferences = lambda **kwargs: next(reads)  # type: ignore[method-assign]  # noqa: ARG005
    client._sync_call_app_action = lambda request: {  # type: ignore[method-assign]  # noqa: ARG005
        "m": "r",
        "r": 0,
    }

    result = client._sync_plan_app_mowing_preference_update(
        map_index=0,
        area_id=None,
        changes={"preference_mode": "custom"},
        execute=True,
        confirm_write=True,
    )

    assert result["executed"] is True
    assert result["request_verified"] is True
    assert result["verification_source"] == "preference_readback"
    assert result["response_data"] is None


def test_preference_write_rejects_acknowledged_mode_without_matching_readback() -> (
    None
):
    client = _client()
    unchanged = decode_mowing_preference_payload(
        [220, 0, 0, 1, 40, 2, 90, 1, 0, 1, 1, 2, 1, 15, 20, 7, 1]
    )
    client._sync_get_mowing_preferences = lambda **kwargs: _preference_result(  # type: ignore[method-assign]  # noqa: ARG005
        unchanged
    )
    client._sync_call_app_action = lambda request: {  # type: ignore[method-assign]  # noqa: ARG005
        "m": "r",
        "r": 0,
        "d": {"r": 0, "ok": True},
    }

    with (
        patch(
            "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
            "client_settings.time.sleep"
        ) as sleep,
        pytest.raises(
            DreameLawnMowerCommandRejectedError,
            match="readback did not confirm.*preference_mode",
        ),
    ):
        client._sync_plan_app_mowing_preference_update(
            map_index=0,
            area_id=None,
            changes={"preference_mode": "custom"},
            execute=True,
            confirm_write=True,
        )

    assert [call.args[0] for call in sleep.call_args_list] == [1.0, 2.0]


def test_plan_app_mowing_preference_update_targets_global_area_zero() -> None:
    client = _client()
    current = decode_mowing_preference_payload(
        [12, 1, 0, 1, 40, 0, 0, 1, 0, 1, 0, 2, 1, 5, 15, 7, 1, 3, 1, 2, 30]
    )
    client._sync_get_mowing_preferences = lambda *args, **kwargs: {
        "available": True,
        "maps": [
            {
                "idx": 1,
                "mode": 0,
                "mode_name": "global",
                "area_count": 1,
                "preferences": [current],
            }
        ],
    }

    result = client._sync_plan_app_mowing_preference_update(
        map_index=1,
        area_id=0,
        changes={"mowing_height_cm": 4.5},
    )

    assert result["area_id"] == 0
    assert result["payload"] == [
        12,
        1,
        0,
        1,
        45,
        0,
        0,
        1,
        0,
        1,
        0,
        2,
        1,
        5,
        15,
        7,
        1,
        3,
        1,
        2,
        30,
    ]


def test_plan_app_mowing_preference_update_can_execute_mode_and_settings_sequence() -> (
    None
):
    client = _client()
    requests: list[dict[str, object]] = []
    preference = {
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
        "obstacle_avoidance_ai_classes": ["people", "animals", "objects"],
        "edge_mowing_safe": True,
    }

    def read_preferences(*args, **kwargs):  # noqa: ANN002, ANN003, ARG001
        readback = dict(preference)
        mode = 0
        if requests:
            mode = 1
            readback["mowing_height_cm"] = 4.0
        return {
            "available": True,
            "maps": [
                {
                    "idx": 1,
                    "mode": mode,
                    "mode_name": "custom" if mode == 1 else "global",
                    "area_count": 1,
                    "preferences": [readback],
                }
            ],
        }

    client._sync_get_mowing_preferences = read_preferences
    client._sync_call_app_action = lambda request: (
        requests.append(request)
        or (
            {"r": 0}
            if request["t"] == "PREP"
            else {"r": 0, "d": {"r": 0, "ok": True}}
        )
    )

    result = client._sync_plan_app_mowing_preference_update(
        map_index=1,
        area_id=5,
        changes={"preference_mode": "custom", "mowing_height_cm": 4.0},
        execute=True,
        confirm_write=True,
    )

    assert result["executed"] is True
    assert result["verification_source"] == "preference_readback"
    assert result["changed_fields"] == ["preference_mode", "mowing_height_cm"]
    assert result["request_candidate"] == {
        "sequence": [
            {"m": "s", "t": "PREP", "d": {"idx": 1, "value": 1}},
            {
                "m": "s",
                "t": "PRE",
                "d": [10, 1, 5, 1, 40, 1, 10, 1, 0, 1, 0, 1, 1, 5, 10, 7, 1],
            },
        ]
    }
    assert result["request_candidates"] == [
        {"m": "s", "t": "PREP", "d": {"idx": 1, "value": 1}},
        {
            "m": "s",
            "t": "PRE",
            "d": [10, 1, 5, 1, 40, 1, 10, 1, 0, 1, 0, 1, 1, 5, 10, 7, 1],
        },
    ]
    assert result["response_data"] == [None, {"r": 0, "ok": True}]
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
