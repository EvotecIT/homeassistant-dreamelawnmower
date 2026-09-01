"""Regression checks for batch device-data helpers."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    batch_data_text,
    decode_batch_mowing_preferences,
    decode_batch_ota_info,
    decode_batch_schedule_payload,
)
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor


def _schedule_text() -> str:
    return (
        '{"d":[[0,1,"","AJKSTiIDABCSkk7/DwAgkpJO/w8AMJKSTv8PAECSkk7/'
        'DwBQkpJOAAAAYJKSTv8PAA=="],[1,0,""]],"v":19383}'
    )


def _settings_text() -> str:
    return json.dumps(
        [
            {
                "mode": 0,
                "settings": {
                    "0": {
                        "version": 152,
                        "id": 0,
                        "efficientMode": 1,
                        "mowingHeight": 4,
                        "mowingDirectionMode": 1,
                        "mowingDirection": 40,
                        "edgeMowingAuto": 1,
                        "edgeMowingWalkMode": 1,
                        "edgeMowingObstacleAvoidance": 1,
                        "cutterPosition": 0,
                        "edgeMowingNum": 1,
                        "obstacleAvoidanceEnabled": 1,
                        "obstacleAvoidanceHeight": 5,
                        "obstacleAvoidanceDistance": 15,
                        "obstacleAvoidanceAi": 7,
                        "edgeMowingSafe": 1,
                        "edgeCuttingAttachment": 0,
                    },
                    "1": {
                        "version": 10,
                        "id": 1,
                        "efficientMode": 0,
                        "mowingHeight": 6,
                        "mowingDirectionMode": 0,
                        "mowingDirection": 0,
                        "edgeMowingAuto": 1,
                        "edgeMowingWalkMode": 0,
                        "edgeMowingObstacleAvoidance": 1,
                        "cutterPosition": 1,
                        "edgeMowingNum": 1,
                        "obstacleAvoidanceEnabled": 1,
                        "obstacleAvoidanceHeight": 20,
                        "obstacleAvoidanceDistance": 20,
                        "obstacleAvoidanceAi": 7,
                        "edgeMowingSafe": 1,
                    },
                },
            },
            {
                "mode": 0,
                "settings": {
                    "0": {
                        "version": 10,
                        "id": 0,
                        "efficientMode": 1,
                        "mowingHeight": 3.5,
                        "mowingDirectionMode": 1,
                        "mowingDirection": 10,
                        "edgeMowingAuto": 1,
                        "edgeMowingWalkMode": 1,
                        "edgeMowingObstacleAvoidance": 1,
                        "cutterPosition": 0,
                        "edgeMowingNum": 1,
                        "obstacleAvoidanceEnabled": 1,
                        "obstacleAvoidanceHeight": 5,
                        "obstacleAvoidanceDistance": 10,
                        "obstacleAvoidanceAi": 7,
                        "edgeMowingSafe": 1,
                    }
                },
            },
        ],
        separators=(",", ":"),
    )


class _FakeBatchCloud:
    logged_in = True

    def __init__(self) -> None:
        schedule_text = _schedule_text()
        settings_text = _settings_text()
        self.calls: list[list[str]] = []
        self.request_options: list[dict[str, float | None]] = []
        self.payload = {
            "SCHEDULE.0": schedule_text,
            "SCHEDULE.info": str(len(schedule_text)),
            "SETTINGS.0": settings_text[:150],
            "SETTINGS.1": settings_text[150:300],
            "SETTINGS.2": settings_text[300:],
            "SETTINGS.info": str(len(settings_text)),
            "OTA_INFO.0": "[1,0]",
            "OTA_INFO.info": "5",
            "prop.s_auto_upgrade": "0",
        }

    def get_batch_device_datas(
        self,
        keys: list[str],
        *,
        timeout: float | None = None,
        deadline: float | None = None,
    ) -> dict[str, object]:
        self.calls.append(list(keys))
        self.request_options.append(
            {
                "timeout": timeout,
                "deadline": deadline,
            }
        )
        return dict(self.payload)


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


def test_batch_data_text_combines_split_chunks() -> None:
    payload = {
        "SETTINGS.0": '{"a":1,"b":',
        "SETTINGS.1": "[2,3]}trailing",
        "SETTINGS.info": "17",
    }

    assert batch_data_text(payload, "SETTINGS") == '{"a":1,"b":[2,3]}'


def test_decode_batch_schedule_payload_decodes_live_shaped_schedule() -> None:
    result = decode_batch_schedule_payload(
        {
            "SCHEDULE.0": _schedule_text(),
            "SCHEDULE.info": str(len(_schedule_text())),
        },
        map_index_hint=0,
    )

    assert result["source"] == "batch_device_data_schedule"
    assert result["available"] is True
    assert result["active_schedule_version"] == 19383
    assert result["schedules"][0]["idx"] == 0
    assert result["schedules"][0]["version"] == 19383
    assert result["schedules"][0]["plan_count"] == 2
    assert result["schedules"][0]["enabled_plan_count"] == 1
    assert result["schedules"][0]["plans"][0]["weeks"][0]["tasks"][0]["start_time"] == (
        "10:58"
    )


@pytest.mark.parametrize("plan_data", [None, {}, "invalid"])
def test_decode_batch_schedule_payload_rejects_invalid_plan_list(
    plan_data: object,
) -> None:
    payload: dict[str, object] = {"v": 22}
    if plan_data is not None:
        payload["d"] = plan_data
    payload_text = json.dumps(payload, separators=(",", ":"))

    result = decode_batch_schedule_payload({"SCHEDULE.0": payload_text})

    assert result["available"] is False
    assert "active_schedule_version" not in result
    assert result["schedules"][0]["available"] is False
    assert result["schedules"][0]["error"] == (
        "missing_or_invalid_batch_schedule_plans"
    )
    assert result["errors"] == [
        {
            "stage": "schedule",
            "error": "missing_or_invalid_batch_schedule_plans",
        }
    ]


def test_decode_batch_mowing_preferences_decodes_map_settings() -> None:
    result = decode_batch_mowing_preferences(
        {
            "SETTINGS.0": _settings_text()[:150],
            "SETTINGS.1": _settings_text()[150:300],
            "SETTINGS.2": _settings_text()[300:],
            "SETTINGS.info": str(len(_settings_text())),
        }
    )

    assert result["source"] == "batch_device_data_mowing_preferences"
    assert result["available"] is True
    assert [entry["idx"] for entry in result["maps"]] == [0, 1]
    assert result["maps"][0]["mode_name"] == "global"
    assert result["maps"][0]["area_count"] == 2
    assert result["maps"][0]["preferences"][0]["efficient_mode_name"] == "efficient"
    assert result["maps"][0]["preferences"][0]["mowing_height_cm"] == 4.0
    assert (
        result["maps"][0]["preferences"][0]["mowing_direction_mode_name"]
        == "rotation"
    )
    assert (
        result["maps"][0]["preferences"][0]["mowing_direction_method_name"]
        == "mow_at_angle"
    )
    assert result["maps"][0]["preferences"][0]["mowing_direction_degrees"] == 40
    assert (
        result["maps"][0]["preferences"][0]["edge_mowing_walk_mode_name"]
        == "side"
    )
    assert result["maps"][0]["preferences"][0]["turning_method_name"] == "efficient"
    assert result["maps"][0]["preferences"][0]["edge_cutting_attachment"] is False
    assert result["maps"][0]["preferences"][0]["obstacle_avoidance_ai_classes"] == [
        "people",
        "animals",
        "objects",
    ]
    assert result["maps"][1]["preferences"][0]["mowing_height_cm"] == 3.5
    assert result["payload_shape"]["alignment"] == "payload_order"
    assert result["payload_shape"]["map_entry_count"] == 2
    assert result["payload_shape"]["map_entries"][0]["settings_entry_count"] == 2
    assert "mowingHeight" in result["payload_shape"]["map_entries"][0][
        "preference_field_keys"
    ]
    assert "raw_setting" not in result["payload_shape"]["map_entries"][0]


def test_decode_batch_mowing_preferences_aligns_with_app_map_index_hints() -> None:
    settings_text = _settings_text()

    result = decode_batch_mowing_preferences(
        {
            "SETTINGS.0": settings_text[:150],
            "SETTINGS.1": settings_text[150:300],
            "SETTINGS.2": settings_text[300:],
            "SETTINGS.info": str(len(settings_text)),
        },
        map_index_hints=[1, 2],
        map_slot_index_hints=[0, 1, 2],
    )

    assert [entry["idx"] for entry in result["maps"]] == [1, 2]
    assert result["maps"][0]["preferences"][0]["map_index"] == 1
    assert result["maps"][0]["preferences"][0]["mowing_height_cm"] == 4.0
    assert result["maps"][1]["preferences"][0]["map_index"] == 2
    assert result["maps"][1]["preferences"][0]["mowing_height_cm"] == 3.5
    assert result["payload_shape"]["alignment"] == "created_app_maps"

    filtered = decode_batch_mowing_preferences(
        {
            "SETTINGS.0": settings_text[:150],
            "SETTINGS.1": settings_text[150:300],
            "SETTINGS.2": settings_text[300:],
            "SETTINGS.info": str(len(settings_text)),
        },
        map_indices=[1],
        map_index_hints=[1, 2],
        map_slot_index_hints=[0, 1, 2],
    )

    assert [entry["idx"] for entry in filtered["maps"]] == [1]
    assert filtered["maps"][0]["preferences"][0]["mowing_height_cm"] == 4.0


def test_decode_batch_mowing_preferences_preserves_uncreated_map_slots() -> None:
    settings_text = json.dumps(
        [
            {"mode": 0, "settings": {}},
            {
                "mode": 0,
                "settings": {
                    "0": {
                        "id": 0,
                        "version": 78,
                        "mowingHeight": 5,
                        "efficientMode": 0,
                    }
                },
            },
        ],
        separators=(",", ":"),
    )

    result = decode_batch_mowing_preferences(
        {
            "SETTINGS.0": settings_text,
            "SETTINGS.info": str(len(settings_text)),
        },
        map_index_hints=[1],
        map_slot_index_hints=[0, 1],
    )

    assert [entry["idx"] for entry in result["maps"]] == [0, 1]
    assert result["maps"][0]["available"] is False
    assert result["maps"][1]["available"] is True
    assert result["maps"][1]["preferences"][0]["mowing_height_cm"] == 5.0
    assert result["payload_shape"]["alignment"] == "app_map_slots"
    assert [
        entry["resolved_map_index"]
        for entry in result["payload_shape"]["map_entries"]
    ] == [0, 1]


@pytest.mark.parametrize(
    "slot_hints",
    [
        [1, 1, 2],
        [True, 2],
        [1, "invalid"],
        [1, -1],
    ],
)
def test_decode_batch_mowing_preferences_rejects_invalid_slot_hints(
    slot_hints: list[object],
) -> None:
    settings_text = json.dumps(
        [
            {"mode": 0, "settings": {}},
            {"mode": 0, "settings": {}},
        ],
        separators=(",", ":"),
    )

    result = decode_batch_mowing_preferences(
        {"SETTINGS.0": settings_text},
        map_index_hints=[8, 9],
        map_slot_index_hints=slot_hints,  # type: ignore[arg-type]
    )

    assert [entry["idx"] for entry in result["maps"]] == [0, 1]
    assert result["payload_shape"]["alignment"] == "payload_order"
    assert (
        result["payload_shape"]["alignment_warning"]
        == "invalid_app_map_slot_hints"
    )


def test_explicit_batch_map_id_overrides_valid_slot_hint() -> None:
    settings_text = json.dumps(
        [{"idx": 7, "mode": 0, "settings": {}}],
        separators=(",", ":"),
    )

    result = decode_batch_mowing_preferences(
        {"SETTINGS.0": settings_text},
        map_index_hints=[3],
        map_slot_index_hints=[1],
    )

    assert [entry["idx"] for entry in result["maps"]] == [7]
    assert result["payload_shape"]["alignment"] == "app_map_slots"
    assert result["payload_shape"]["map_entries"][0]["resolved_map_index"] == 7


def test_decode_batch_ota_info_decodes_flags() -> None:
    result = decode_batch_ota_info(
        {
            "OTA_INFO.0": "[1,0]",
            "OTA_INFO.info": "5",
            "prop.s_auto_upgrade": "0",
        }
    )

    assert result == {
        "source": "batch_device_data_ota_info",
        "available": True,
        "ota_info": [1, 0],
        "update_available": None,
        "auto_upgrade_enabled": False,
        "ota_state": 1,
        "ota_state_name": "idle",
        "ota_progress": 0,
        "errors": [],
    }


def test_client_batch_helpers_use_batch_device_data_api() -> None:
    client = _client()
    cloud = _FakeBatchCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    schedule_result = client._sync_get_batch_schedules(map_index_hint=0)
    preference_result = client._sync_get_batch_mowing_preferences(map_indices=[1])
    ota_result = client._sync_get_batch_ota_info()

    assert schedule_result["schedules"][0]["version"] == 19383
    assert schedule_result["schedules"][0]["idx"] == 0
    assert [entry["idx"] for entry in preference_result["maps"]] == [1]
    assert preference_result["maps"][0]["preferences"][0]["mowing_height_cm"] == 3.5
    assert ota_result["update_available"] is None
    assert ota_result["ota_state_name"] == "idle"
    assert ota_result["ota_progress"] == 0
    assert ota_result["auto_upgrade_enabled"] is False
    assert len(cloud.calls) == 3


def test_batch_schedule_recovery_can_skip_map_discovery() -> None:
    client = _client()
    cloud = _FakeBatchCloud()
    client._sync_get_cloud_protocol = lambda: cloud
    client._sync_get_current_app_map_index = lambda: pytest.fail(
        "batch recovery must not probe MAPL"
    )

    result = client._sync_get_batch_schedules(discover_map_index=False)

    assert result["schedules"][0]["idx"] is None
    assert len(cloud.calls) == 1
    assert "SCHEDULE.info" in cloud.calls[0]


def test_async_batch_schedule_recovery_has_an_overall_deadline() -> None:
    client = _client()
    cloud = _FakeBatchCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud
    started = time.monotonic()

    result = asyncio.run(
        client.async_get_batch_schedules(map_index_hint=0)
    )

    assert result["schedules"][0]["version"] == 19383
    request_options = cloud.request_options[0]
    assert request_options["timeout"] is not None
    assert 0 < request_options["timeout"] <= 5.0
    assert request_options["deadline"] is not None
    assert started < request_options["deadline"] <= started + 5.1


def test_vector_map_batch_fetch_requests_all_device_sized_path_chunks() -> None:
    client = _client()
    cloud = _FakeBatchCloud()
    cloud.payload["M_PATH.18"] = "late path chunk"
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_vector_map_batch_data()

    assert result is not None
    assert result["M_PATH.18"] == "late path chunk"
    assert cloud.calls == [[]]
