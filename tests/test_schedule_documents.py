"""Contracts for current document schedules and framed start-only tasks."""

from __future__ import annotations

import base64
import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.calendar import schedule_calendar_events
from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    decode_batch_schedule_payload,
    decode_schedule_payload_text,
    encode_schedule_payload_text,
)
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor
from dreame_lawn_mower_client.schedule import decode_schedule_week_payload


def _frame(day=0, start=570, task_type=0, regions=b""):
    return (
        bytes(
            (0xAA, 7 + len(regions), (day << 4) | task_type, start & 255, start >> 8, 0)
        )
        + regions
        + b"\xed"
    )


class _DocumentCloud:
    """Synthetic network peer with a shorter-than-requested chunk boundary."""

    logged_in = True

    def __init__(self):
        self.calls = []
        self.payload = json.dumps(
            {
                "d": [
                    [
                        0,
                        1,
                        "Season A",
                        base64.b64encode(
                            b"".join(_frame(day) for day in range(7))
                        ).decode(),
                    ],
                    [1, 0, "Season B"],
                ]
            },
            separators=(",", ":"),
        )
        self.chunk_override = {}

    def call_app_action(self, payload, **_kwargs):
        self.calls.append(deepcopy(payload))
        command = payload["t"]
        data = payload.get("d", {})
        if command == "SCHDIV2":
            raise DreameLawnMowerConnectionError("Old document read is unsupported.")
        if command == "SCHDIV3":
            response = {"i": data["i"], "l": len(self.payload), "v": 42}
        elif command == "SCHDDV3":
            offset = data["s"]
            chunk = self.payload[offset : offset + min(data["l"], 86)]
            response = {
                "s": offset,
                "v": 42,
                "l": len(chunk),
                "d": chunk,
                **self.chunk_override,
            }
        elif command == "SCHDT":
            response = []
        elif command == "SCHDSV3" and payload["m"] == "s":
            response = {"r": 0, "v": 42}
        else:
            raise AssertionError(f"Unexpected command {command}")
        return {"out": [{"m": "r", "r": 0, "d": response}]}


def _client_and_cloud():
    client = DreameLawnMowerClient(
        username="user@example.invalid",
        password="unused",
        country="eu",
        account_type="dreame",
        descriptor=DreameLawnMowerDescriptor(
            did="synthetic-mower",
            name="Mower",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
    )
    cloud = _DocumentCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud
    client._sync_update_device = Mock(
        return_value=SimpleNamespace(
            available=True,
            activity="docked",
            state="idle",
            mowing_session_active=False,
            task_resumable=False,
        )
    )
    client._snapshot_from_device = lambda device, **kwargs: device
    return client, cloud


def test_v3_read_recovers_two_plans_and_remembers_the_successful_generation():
    client, cloud = _client_and_cloud()
    result = client._sync_get_app_schedules(map_indices=[0], include_current_task=False)
    assert result["available"] is True and result["errors"] == []
    slot = result["schedules"][0]
    assert slot["protocol"] == "document" and slot["document_version"] == 3
    assert slot["downloaded_size"] == len(cloud.payload) and slot["chunk_count"] == 2
    plans = slot["plans"]
    assert [(plan["plan_id"], plan["enabled"]) for plan in plans] == [
        (0, True),
        (1, False),
    ]
    assert plans[1]["weeks"] == []
    assert [week["week_day"] for week in plans[0]["weeks"]] == list(range(7))
    assert all(week["tasks"][0]["start_time"] == "09:30" for week in plans[0]["weeks"])
    assert all(week["tasks"][0]["end"] is None for week in plans[0]["weeks"])
    cloud.calls.clear()
    client._sync_get_app_schedules(map_indices=[0], include_current_task=False)
    assert cloud.calls[0]["t"] == "SCHDIV3"
    assert all(call["t"] != "SCHDIV2" for call in cloud.calls)


def test_slow_v3_metadata_gets_a_full_rotating_recovery_window(monkeypatch):
    client, _cloud = _client_and_cloud()
    clock = [100.0]
    deadlines = []

    class _SlowV3Cloud(_DocumentCloud):
        def call_app_action(self, payload, **kwargs):
            deadline = float(kwargs["deadline"])
            deadlines.append(deadline)
            if payload["t"] == "SCHDIV2":
                clock[0] = deadline
                raise TimeoutError("Unsupported generation consumes its deadline.")
            if payload["t"] == "SCHDIV3" and payload["d"]["i"] == -1:
                if deadline - clock[0] < 3.0:
                    clock[0] = deadline
                    raise TimeoutError("Metadata needs three seconds.")
                clock[0] += 3.0
            return super().call_app_action(payload, **kwargs)

    cloud = _SlowV3Cloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud
    schedule_time = client._sync_get_app_schedules.__func__.__globals__["time"]
    monkeypatch.setattr(schedule_time, "monotonic", lambda: clock[0])

    first = client._sync_get_app_schedules(
        map_indices=[-1, 0, 1], include_current_task=False
    )
    assert first["schedules"][0]["available"] is False
    assert max(deadlines) <= 110.0 and clock[0] <= 110.0
    deadlines.clear()
    second_started = clock[0]
    second = client._sync_get_app_schedules(
        map_indices=[-1, 0, 1], include_current_task=False
    )
    assert second["errors"] == []
    assert second["schedules"][0]["document_version"] == 3
    assert len(second["schedules"][0]["plans"][0]["weeks"]) == 7
    assert max(deadlines) <= second_started + 10.0
    assert clock[0] <= second_started + 10.0


@pytest.mark.parametrize("map_indices", [[0], [-1, 0, 1]])
def test_remembered_v2_can_renegotiate_after_elapsed_timeout(monkeypatch, map_indices):
    client, _cloud = _client_and_cloud()
    clock = [100.0]

    class _ChangingCloud(_DocumentCloud):
        generation = 2

        def call_app_action(self, payload, **kwargs):
            if payload["t"] in {"SCHDIV2", "SCHDDV2"}:
                if self.generation == 3:
                    clock[0] = min(
                        float(kwargs["deadline"]), clock[0] + float(kwargs["timeout"])
                    )
                    raise TimeoutError("Previous generation no longer responds.")
                payload = {**payload, "t": payload["t"][:-1] + "3"}
            return super().call_app_action(payload, **kwargs)

    cloud = _ChangingCloud()
    client._sync_get_cloud_protocol = lambda **_kwargs: cloud
    schedule_time = client._sync_get_app_schedules.__func__.__globals__["time"]
    monkeypatch.setattr(schedule_time, "monotonic", lambda: clock[0])
    first = client._sync_get_app_schedules(
        map_indices=map_indices, include_current_task=False
    )
    assert first["schedules"][0]["document_version"] == 2
    cloud.generation = 3
    second = client._sync_get_app_schedules(
        map_indices=map_indices, include_current_task=False
    )
    assert second["errors"] == []
    assert second["schedules"][0]["document_version"] == 3
    assert client._schedule_document_versions[0] == 3
    assert clock[0] <= 110.0


def test_framed_batch_recovery_and_calendar_share_the_same_daily_start_times():
    _client, cloud = _client_and_cloud()
    payload = decode_batch_schedule_payload(
        {"SCHEDULE.0": cloud.payload}, map_index_hint=0
    )
    assert payload["available"] is True
    events = schedule_calendar_events(
        payload,
        datetime(2026, 10, 4, tzinfo=UTC),
        datetime(2026, 10, 11, tzinfo=UTC),
        mower_name="Mower",
    )
    assert len(events) == 7
    assert all(event.start.hour == 9 and event.start.minute == 30 for event in events)
    assert all((event.end - event.start).total_seconds() == 60 for event in events)
    assert all("start" in event.summary for event in events)


def test_v3_enable_dry_run_uses_the_qualified_status_command_without_a_write():
    client, cloud = _client_and_cloud()
    result = client._sync_set_app_schedule_plan_enabled(0, 1, False)
    assert result["request"] == {
        "m": "s",
        "t": "SCHDSV3",
        "d": {"i": 0, "v": 42, "s": [1, 0]},
    }
    assert result["executed"] is False and result["changed"] is False
    assert all(call["m"] == "g" for call in cloud.calls)


def test_v3_document_write_uses_the_qualified_v3_status_command():
    client, cloud = _client_and_cloud()
    result = client._sync_set_app_schedule_plan_enabled(
        0, 1, False, execute=True, confirm_write=True
    )
    assert result["executed"] is True and result["changed"] is False
    assert result["response_data"] == {"r": 0, "v": 42}
    assert [call for call in cloud.calls if call["m"] == "s"] == [
        {"m": "s", "t": "SCHDSV3", "d": {"i": 0, "v": 42, "s": [1, 0]}}
    ]
    assert client._schedule_document_versions[0] == 3


def test_v3_full_upload_cannot_convert_to_legacy_task_encoding():
    client, cloud = _client_and_cloud()
    replacement = [{"plan_id": 0, "enabled": True, "weeks": []}]
    with pytest.raises(ValueError, match="V3/framed"):
        client._sync_plan_app_schedule_upload(
            0, replacement, execute=True, confirm_write=True
        )
    assert all(call["m"] == "g" for call in cloud.calls)
    with pytest.raises(ValueError, match="framed"):
        encode_schedule_payload_text(decode_schedule_payload_text(cloud.payload))


@pytest.mark.parametrize("override", [{"s": 1}, {"v": 43}, {"l": 1}])
def test_v3_read_rejects_foreign_or_misreported_chunks(override):
    client, cloud = _client_and_cloud()
    cloud.chunk_override = override
    result = client._sync_get_app_schedules(map_indices=[0], include_current_task=False)
    assert result["available"] is False
    assert "plans" not in result["schedules"][0]


@pytest.mark.parametrize(
    "block",
    [
        b"\xaa",
        b"\xaa\x06\x00\x3a\x02\x00\xed",
        b"\xaa\x08\x00\x3a\x02\x00\xed",
        _frame()[:-1] + b"\x00",
        _frame(day=7),
        _frame(start=1440),
        _frame(task_type=2, regions=b"\x01"),
        _frame() + bytes(7),
        _frame() + b"\xaa",
    ],
)
def test_framed_tasks_reject_invalid_or_partial_records(block):
    with pytest.raises(ValueError):
        decode_schedule_week_payload(base64.b64encode(block).decode())


def test_framed_tasks_preserve_cyclic_flag_and_complete_contour_pairs():
    block = _frame(day=2, task_type=10, regions=bytes((1, 2, 3, 4)))
    task = decode_schedule_week_payload(base64.b64encode(block).decode())[0]["tasks"][0]
    assert task["cyclic"] is True and task["type_name"] == "edge_mowing"
    assert task["regions"] == [[1, 2], [3, 4]]


def test_schedule_document_reader_retains_only_validated_bytes():
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        schedule_document,
    )

    reader = schedule_document.ScheduleDocumentReader(
        size=7, version=42, chunk_size=5, document_version=3,
    )
    reader.append_response({"r": 0, "d": {"d": "ż", "l": 2, "s": 0, "v": 42}})
    assert reader.request() == {
        "m": "g", "t": "SCHDDV3", "d": {"s": 2, "l": 5, "v": 42},
    }
    with pytest.raises(
        schedule_document.DreameLawnMowerConnectionError, match="incomplete",
    ):
        reader.result()
    with pytest.raises(
        schedule_document.DreameLawnMowerConnectionError, match="chunk identity",
    ):
        reader.append_response({"r": 0, "d": {"d": "ółw", "s": 0, "v": 42}})
    assert reader.offset == 2
    assert reader.chunk_count == 1
    reader.append_response({"r": 0, "d": {"d": "ółw", "l": 5, "s": 2, "v": 42}})
    assert reader.complete
    assert reader.result() == ("żółw", 2, 7)
