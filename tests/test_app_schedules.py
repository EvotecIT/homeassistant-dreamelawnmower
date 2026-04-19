"""Regression checks for mower-native app schedule retrieval."""

from __future__ import annotations

import pytest

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    build_schedule_upload_requests,
    decode_schedule_payload_text,
    encode_schedule_payload_text,
)
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
        if command == "SCHDSV2":
            return {"out": [{"m": "r", "r": 0, "d": {"r": 0, "v": 19383}}]}
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


@pytest.mark.parametrize(
    "payload_text",
    [
        '{"d":[[0,1,"","EODBJwAAADDgwScAAAA="]]}',
        (
            '{"d":[[0,1,"","AJKSTiIDABCSkk7/DwAgkpJO/w8AMJKSTv8PAECSkk7/'
            'DwBQkpJOAAAAYJKSTv8PAA=="],[1,0,""]]}'
        ),
        '{"d":[[0,0,""]]}',
    ],
)
def test_schedule_payload_round_trips(payload_text: str) -> None:
    plans = decode_schedule_payload_text(payload_text)

    assert encode_schedule_payload_text(plans) == payload_text


def test_schedule_upload_requests_chunk_payload() -> None:
    requests = build_schedule_upload_requests(
        map_index=0,
        payload_text='{"d":[]}',
        version=123,
        chunk_size=4,
    )

    assert requests == [
        {"m": "s", "t": "SCHDIV2", "d": {"i": 0, "l": 8, "v": 123}},
        {"m": "s", "t": "SCHDDV2", "d": {"s": 0, "l": 4, "d": '{"d"', "v": 123}},
        {"m": "s", "t": "SCHDDV2", "d": {"s": 4, "l": 4, "d": ":[]}", "v": 123}},
    ]


def test_schedule_upload_requests_do_not_split_utf8_characters() -> None:
    requests = build_schedule_upload_requests(
        map_index=0,
        payload_text='{"name":"zażółć"}',
        version=123,
        chunk_size=10,
    )

    chunks = [request["d"] for request in requests[1:]]
    assert "".join(chunk["d"] for chunk in chunks) == '{"name":"zażółć"}'
    assert [chunk["s"] for chunk in chunks] == [0, 10, 20]
    assert [chunk["l"] for chunk in chunks] == [10, 10, 1]


def test_set_app_schedule_plan_enabled_builds_dry_run_request() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_set_app_schedule_plan_enabled(
        map_index=0,
        plan_id=1,
        enabled=True,
    )

    assert result["dry_run"] is True
    assert result["executed"] is False
    assert result["previous_enabled"] is False
    assert result["request"] == {
        "m": "s",
        "t": "SCHDSV2",
        "d": {"i": 0, "v": 19383, "s": [1, 1]},
    }
    assert [call["t"] for call in cloud.calls] == ["SCHDT", "SCHDIV2", "SCHDDV2"]


def test_set_app_schedule_plan_enabled_requires_confirmation_to_execute() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    with pytest.raises(ValueError, match="confirm_write=True"):
        client._sync_set_app_schedule_plan_enabled(
            map_index=0,
            plan_id=1,
            enabled=True,
            execute=True,
        )


def test_set_app_schedule_plan_enabled_can_execute_when_confirmed() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    result = client._sync_set_app_schedule_plan_enabled(
        map_index=0,
        plan_id=1,
        enabled=True,
        execute=True,
        confirm_write=True,
    )

    assert result["dry_run"] is False
    assert result["executed"] is True
    assert result["response"] == {"m": "r", "r": 0, "d": {"r": 0, "v": 19383}}
    assert result["response_data"] == {"r": 0, "v": 19383}
    assert [call["t"] for call in cloud.calls] == [
        "SCHDT",
        "SCHDIV2",
        "SCHDDV2",
        "SCHDSV2",
    ]


def test_set_app_schedule_plan_enabled_rejects_failed_write_response() -> None:
    client = _client()
    cloud = _FakeAppScheduleCloud()
    client._sync_get_cloud_protocol = lambda: cloud

    def failing_call(payload: dict[str, object]) -> dict[str, object]:
        if payload["t"] == "SCHDSV2":
            return {"m": "r", "r": 0, "d": {"r": 1, "v": 19383}}
        return cloud.call_app_action(payload)["out"][0]

    client._sync_call_app_action = failing_call

    with pytest.raises(DreameLawnMowerConnectionError, match="Schedule write failed"):
        client._sync_set_app_schedule_plan_enabled(
            map_index=0,
            plan_id=1,
            enabled=True,
            execute=True,
            confirm_write=True,
        )
