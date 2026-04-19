"""Regression checks for mower-native app schedule retrieval."""

from __future__ import annotations

from dreame_lawn_mower_client import DreameLawnMowerClient
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor


class _FakeAppScheduleCloud:
    logged_in = True

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.payloads = {
            -1: {"version": 31345, "text": '{"d":[[0,1,"","EODBJwAAADDgwScAAAA="]]}'},
            0: {
                "version": 19383,
                "text": (
                    '{"d":[[0,1,"","AJKSTiIDABCSkk7/DwAgkpJO/w8="],'
                    '[1,0,""]]}'
                ),
            },
            1: {"version": 4760, "text": '{"d":[[0,0,""]]}'},
        }

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
        if command == "SCHDT":
            return {"out": [{"m": "r", "r": 0, "d": [658, 1257, 0, 19383]}]}
        if command == "SCHDIV2":
            idx = int(payload["d"]["i"])
            entry = self.payloads[idx]
            return {
                "out": [
                    {
                        "m": "r",
                        "r": 0,
                        "d": {
                            "i": idx,
                            "l": len(entry["text"].encode("utf-8")),
                            "v": entry["version"],
                        },
                    }
                ]
            }
        if command == "SCHDDV2":
            data = payload["d"]
            version = int(data["v"])
            start = int(data["s"])
            size = int(data["l"])
            text = next(
                item["text"]
                for item in self.payloads.values()
                if item["version"] == version
            )
            chunk = text.encode("utf-8")[start : start + size].decode("utf-8")
            return {
                "out": [
                    {
                        "m": "r",
                        "r": 0,
                        "d": {"l": len(chunk.encode("utf-8")), "d": chunk},
                    }
                ]
            }
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


def test_app_schedules_decode_plans_and_current_task() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_app_schedules(chunk_size=40)

    assert result["available"] is True
    assert result["current_task"] == {
        "start": 658,
        "start_time": "10:58",
        "end": 1257,
        "end_time": "20:57",
        "plan_id": 0,
        "version": 19383,
    }
    assert [schedule["idx"] for schedule in result["schedules"]] == [-1, 0, 1]
    map_0 = result["schedules"][1]
    assert map_0["available"] is True
    assert map_0["version"] == 19383
    assert map_0["plan_count"] == 2
    assert map_0["enabled_plan_count"] == 1
    assert map_0["plans"][0]["weeks"][0] == {
        "week_day": 0,
        "week_day_name": "sun",
        "tasks": [
            {
                "type": 0,
                "type_name": "all_area_mowing",
                "cyclic": False,
                "start": 658,
                "start_time": "10:58",
                "end": 1257,
                "end_time": "20:57",
                "real_end": 802,
                "real_end_time": "13:22",
                "regions": [],
            }
        ],
    }
    assert "raw_text" not in map_0
    assert [call["t"] for call in cloud.calls[:3]] == ["SCHDT", "MAPL", "SCHDIV2"]


def test_app_schedules_can_include_raw_payload_text() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_get_app_schedules(
        include_raw=True,
        map_indices=[0],
        chunk_size=100,
    )

    assert [schedule["idx"] for schedule in result["schedules"]] == [0]
    assert result["schedules"][0]["raw_text"].startswith('{"d":')
    assert [call["t"] for call in cloud.calls] == ["SCHDT", "SCHDIV2", "SCHDDV2"]
