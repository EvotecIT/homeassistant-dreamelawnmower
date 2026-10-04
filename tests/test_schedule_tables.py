"""Contracts for table schedules, protocol discovery, and confirmed flags."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.dreame_lawn_mower.calendar import schedule_calendar_events
from custom_components.dreame_lawn_mower.coordinator import DreameLawnMowerCoordinator
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import schedule_tables
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client import (
    DreameLawnMowerClient,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerConnectionError,
    attempted_write_fields,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
)
from custom_components.dreame_lawn_mower.schedule_cache import (
    merge_batch_schedule_payload,
    select_table_schedule_for_map,
)


class TableCloud:
    """Synthetic protocol peer; no captured vendor implementation or data."""

    logged_in = True

    def __init__(self):
        self.calls = []
        self.tables = [[4, 1, 0, "Morning", [[10, 2]]], [5, 0, 0, "Evening", [[11, 3]]]]
        self.tasks = {
            10: [10, 1, 0, 480, [1], []],
            11: [11, 1, 10, 1080, [1, 4], [[7, 0]]],
        }
        self.table_response = None
        self.ignore_write = False
        self.missing_task = False
        self.document_supported = False

    def call_app_action(self, payload, **kwargs):
        self.calls.append(deepcopy(payload))
        command = payload["t"]
        data = payload["d"]
        if command == "SCHDT":
            response = {"r": 0, "d": []}
        elif command == "SCHDI":
            assert data == [4, 5]
            response = (
                self.table_response
                if self.table_response is not None
                else {"r": 0, "d": deepcopy(self.tables)}
            )
        elif command == "SCHDC":
            if self.missing_task:
                raise TimeoutError("Task read timed out")
            response = {"r": 0, "d": deepcopy(self.tasks[data[1]])}
        elif command == "SCHDS":
            if not self.ignore_write:
                for table in self.tables:
                    if table[0] == data[0]:
                        table[1] = data[1]
                    elif data[1]:
                        table[1] = 0
            response = {"r": 0, "d": None}
        elif command == "SCHDIV2":
            response = (
                {"r": 0, "d": {"i": data["i"], "l": 0, "v": 65535}}
                if self.document_supported
                else {"r": 7, "d": None}
            )
        else:
            raise AssertionError(command)
        return {"out": [{"m": "r", **response}]}


def client_and_cloud():
    descriptor = DreameLawnMowerDescriptor(
        did="synthetic",
        name="Test mower",
        model="mova.mower.g2529c",
        display_model="LiDAX Ultra 1000",
        account_type="mova",
        country="eu",
    )
    client = DreameLawnMowerClient(
        username="test@example.invalid",
        password="test",
        country="eu",
        account_type="mova",
        descriptor=descriptor,
    )
    cloud = TableCloud()
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
    client._snapshot_from_device = lambda device: device
    return client, cloud


def test_table_tasks_preserve_start_only_timing_and_contour_targets():
    weeks = schedule_tables.decode_schedule_table_task(
        [11, 1, 10, 1080, [4, 1, 4], [[7, 0]], "future"],
        task_id=11,
    )
    assert [week["week_day"] for week in weeks] == [1, 4]
    task = weeks[0]["tasks"][0]
    assert task["cyclic"] is True
    assert task["type_name"] == "edge_mowing"
    assert task["regions"] == [[7, 0]]
    assert task["start_time"] == "18:00"
    assert task["end"] is None and task["timing"] == "start_only"
    assert (
        schedule_tables.decode_schedule_table_task(
            [11, 0, 0, 480, [1], []],
            task_id=11,
        )
        == []
    )


@pytest.mark.parametrize(
    "record",
    [
        [4, 1, 0, "Bad task", [[10]]],
        [3, 1, 0, "Wrong map", []],
        [4, 2, 0, "Invalid flag", []],
        [4, 1, 0, "Duplicate task", [[10, 1], [10, 2]]],
    ],
)
def test_table_inventory_rejects_ambiguous_identity_and_flags(record):
    with pytest.raises(ValueError):
        schedule_tables.decode_schedule_tables([record], map_index=2)


@pytest.mark.parametrize(
    "response",
    [
        {"m": "r", "d": 7},
        {"r": 7, "d": []},
        {"r": 0, "d": 7},
    ],
)
def test_failed_table_reply_is_unknown_not_an_empty_supported_schedule(response):
    client, cloud = client_and_cloud()
    cloud.table_response = response
    result = client._sync_get_app_schedules(map_indices=[2], include_current_task=False)
    assert result["schedules"][0]["read_status"] == "unknown"
    assert "plans" not in result["schedules"][0]
    assert client._schedule_protocols == {}


def test_valid_empty_inventory_establishes_table_protocol_without_synthetic_plans():
    client, cloud = client_and_cloud()
    cloud.tables = []
    result = client._sync_get_app_schedules(map_indices=[2], include_current_task=False)
    assert result["schedules"][0]["plans"] == []
    assert result["schedules"][0]["read_status"] == "complete"
    assert client._schedule_protocols == {2: "tables"}
    assert [call["t"] for call in cloud.calls] == ["SCHDI"]


def test_mova_can_discover_document_protocol_and_reuse_validated_choice():
    client, cloud = client_and_cloud()
    cloud.table_response = {"m": "r", "d": 7}
    cloud.document_supported = True
    result = client._sync_get_app_schedules(map_indices=[2], include_current_task=False)
    assert result["schedules"][0]["protocol"] == "document"
    cloud.calls.clear()
    client._sync_get_app_schedules(map_indices=[2], include_current_task=False)
    assert [call["t"] for call in cloud.calls] == ["SCHDIV2"]


def test_table_toggle_confirms_target_and_mower_disabled_sibling():
    client, cloud = client_and_cloud()
    preview = client._sync_set_app_schedule_plan_enabled(2, 1, True)
    assert preview["dry_run"] and not preview["executed"]
    assert all(call["m"] == "g" for call in cloud.calls)
    result = client._sync_set_app_schedule_plan_enabled(
        2,
        1,
        True,
        execute=True,
        confirm_write=True,
    )
    assert result["confirmed"] is True
    assert result["request"] == {"m": "s", "t": "SCHDS", "d": [5, 1]}
    assert [plan["enabled"] for plan in result["confirmed_schedule"]["plans"]] == [
        False,
        True,
    ]
    assert cloud.calls[-1]["t"] == "SCHDI"


def test_table_toggle_cannot_edit_an_unfinished_docked_task():
    client, cloud = client_and_cloud()
    client._sync_update_device.return_value.task_resumable = True
    with pytest.raises(DreameLawnMowerConnectionError, match="Finish the current"):
        asyncio.run(
            client.async_set_app_schedule_plan_enabled(
                map_index=2,
                plan_id=1,
                enabled=False,
                execute=True,
                confirm_write=True,
            )
        )
    assert all(call["m"] == "g" for call in cloud.calls)


def test_acknowledged_but_ignored_table_toggle_is_not_reported_successful():
    client, cloud = client_and_cloud()
    cloud.ignore_write = True
    with pytest.raises(DreameLawnMowerConnectionError, match="not confirmed") as error:
        client._sync_set_app_schedule_plan_enabled(
            2,
            1,
            True,
            execute=True,
            confirm_write=True,
        )
    assert attempted_write_fields(error.value) == ("schedule",)


def test_missing_task_blocks_enabling_but_allows_disabling_known_table():
    client, cloud = client_and_cloud()
    cloud.missing_task = True
    with pytest.raises(ValueError, match="missing"):
        client._sync_set_app_schedule_plan_enabled(
            2,
            1,
            True,
            execute=True,
            confirm_write=True,
        )
    assert not any(call["m"] == "s" for call in cloud.calls)
    result = client._sync_set_app_schedule_plan_enabled(
        2,
        0,
        False,
        execute=True,
        confirm_write=True,
    )
    assert result["confirmed"]


def test_table_upload_is_rejected_before_any_write():
    client, cloud = client_and_cloud()
    with pytest.raises(ValueError, match="Full plan upload is unavailable"):
        client._sync_plan_app_schedule_upload(2, [], execute=True, confirm_write=True)
    assert all(call["m"] == "g" for call in cloud.calls)


def test_table_calendar_marks_start_without_claiming_mowing_duration():
    client, _cloud = client_and_cloud()
    payload = client._sync_get_app_schedules(
        map_indices=[2], include_current_task=False
    )
    select_table_schedule_for_map(payload, 2)
    events = schedule_calendar_events(
        payload,
        datetime(2026, 4, 20, tzinfo=UTC),
        datetime(2026, 4, 21, tzinfo=UTC),
    )
    assert len(events) == 1
    assert events[0].end - events[0].start == timedelta(minutes=1)
    assert events[0].summary.endswith("— start")
    assert "no scheduled end time" in events[0].description
    select_table_schedule_for_map(payload, None)
    assert (
        schedule_calendar_events(
            payload,
            datetime(2026, 4, 20, tzinfo=UTC),
            datetime(2026, 4, 21, tzinfo=UTC),
        )
        == []
    )


def test_cached_document_batch_cannot_replace_table_plans():
    client, _cloud = client_and_cloud()
    payload = client._sync_get_app_schedules(
        map_indices=[2], include_current_task=False
    )
    assert (
        merge_batch_schedule_payload(
            payload,
            {"schedules": [{"idx": 2, "version": 123, "plans": []}]},
            captured_at=datetime.now(UTC),
            allow_unknown_slot=True,
            allowed_hint_indices=[2],
        )
        is None
    )


def test_ha_consumers_keep_both_confirmed_flags_when_followup_read_fails():
    client, _cloud = client_and_cloud()
    before = client._sync_get_app_schedules(map_indices=[2], include_current_task=False)
    result = client._sync_set_app_schedule_plan_enabled(
        2,
        1,
        True,
        execute=True,
        confirm_write=True,
    )
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._schedule_write_lock = asyncio.Lock()
    coordinator.client = SimpleNamespace(
        async_set_app_schedule_plan_enabled=AsyncMock(return_value=result),
    )
    coordinator.schedules = before
    retained = [
        {"idx": -1, "protocol": "document", "version": 21, "plans": []},
        {"idx": 0, "protocol": "document", "version": 22, "plans": []},
    ]
    coordinator.schedules["schedules"].extend(deepcopy(retained))
    coordinator.async_refresh_schedules = AsyncMock(side_effect=TimeoutError)
    coordinator.async_update_listeners = lambda: None
    with pytest.raises(TimeoutError):
        asyncio.run(
            coordinator.async_set_schedule_plan_enabled(
                map_index=2,
                plan_id=1,
                enabled=True,
            )
        )
    assert [
        plan["enabled"] for plan in coordinator.schedules["schedules"][0]["plans"]
    ] == [
        False,
        True,
    ]
    assert coordinator.schedules["schedules"][1:] == retained


def test_document_timeout_still_probes_tables_and_remembers_the_supported_protocol(
    monkeypatch,
):
    client, cloud = client_and_cloud()
    client._account_type = "dreame"
    cloud.tables = []
    original = cloud.call_app_action
    clock = [100.0]

    def call(payload, **kwargs):
        if payload["t"] == "SCHDIV2":
            cloud.calls.append(deepcopy(payload))
            clock[0] = kwargs["deadline"]
            raise TimeoutError("Document protocol timed out")
        return original(payload, **kwargs)

    cloud.call_app_action = call
    module = client._sync_get_app_schedule_slot.__func__.__globals__
    monkeypatch.setattr(module["time"], "monotonic", lambda: clock[0])
    result = client._sync_get_app_schedules(map_indices=[2], include_current_task=False)
    assert result["schedules"][0]["protocol"] == "tables"
    assert [call["t"] for call in cloud.calls] == ["SCHDIV2", "SCHDIV3", "SCHDI"]
    cloud.calls.clear()
    client._sync_get_app_schedules(map_indices=[2], include_current_task=False)
    assert [call["t"] for call in cloud.calls] == ["SCHDI"]
