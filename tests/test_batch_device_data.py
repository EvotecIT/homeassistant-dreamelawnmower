"""Regression checks for batch device-data helpers."""

from __future__ import annotations

import json

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

    def get_batch_device_datas(self, keys: list[str]) -> dict[str, object]:
        self.calls.append(list(keys))
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
    assert result["schedules"][0]["idx"] == 0
    assert result["schedules"][0]["version"] == 19383
    assert result["schedules"][0]["plan_count"] == 2
    assert result["schedules"][0]["enabled_plan_count"] == 1
    assert result["schedules"][0]["plans"][0]["weeks"][0]["tasks"][0]["start_time"] == (
        "10:58"
    )


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
    assert result["maps"][0]["preferences"][0]["obstacle_avoidance_ai_classes"] == [
        "people",
        "animals",
        "objects",
    ]
    assert result["maps"][1]["preferences"][0]["mowing_height_cm"] == 3.5


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
