"""Preserved native start-time edits through the real client/network boundary."""

from __future__ import annotations

import asyncio
import base64
import json
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.dreame_lawn_mower.coordinator import DreameLawnMowerCoordinator
from custom_components.dreame_lawn_mower.services import (
    SET_SCHEDULE_TASK_START_TIME_SCHEMA,
)
from custom_components.dreame_lawn_mower.switch import schedule_plan_entries
from tests.test_schedule_documents import _client_and_cloud, _frame


class _RowTransport:
    logged_in = True

    def __init__(self, *, reject_last=False, contradict_readback=False):
        blob = bytearray(b"".join(_frame(day, 658) for day in range(7)))
        for offset in range(0, len(blob), 7):
            blob[offset + 4] |= 0xC0
            blob[offset + 5] = 0xAB
        self.native = {
            "d": [
                [0, 1, "Wiosna – lato 🌱", base64.b64encode(blob).decode()],
                [1, 0, "Autumn"],
            ],
            "v": 13626,
        }
        self.original = deepcopy(self.native)
        self.calls = []
        self.reject_last = reject_last
        self.contradict_readback = contradict_readback
        self.transfer = bytearray()
        self.transfer_token = None
        self.transfer_length = None

    def call_app_action(self, payload, **kwargs):
        self.calls.append(deepcopy(payload))
        command, data = payload["t"], payload.get("d", {})
        text = json.dumps(self.native, ensure_ascii=False, separators=(",", ":"))
        raw = text.encode()
        if payload["m"] == "g":
            if command == "SCHDIV2":
                return {"out": [{"r": 3}]}
            if command == "SCHDIV3":
                answer = {"i": 0, "l": len(raw), "v": self.native["v"]}
            elif command == "SCHDDV3":
                chunk = raw[data["s"] : data["s"] + data["l"]].decode()
                answer = {
                    "s": data["s"],
                    "l": len(chunk.encode()),
                    "v": self.native["v"],
                    "d": chunk,
                }
            else:
                raise AssertionError(command)
        elif command == "SCHDIV3":
            assert kwargs["retry_count"] == 0
            assert data["i"] == 0 and data["v"] > 1_000_000_000_000
            self.transfer_token, self.transfer_length = data["v"], data["l"]
            self.transfer.clear()
            answer = {}
        elif command == "SCHDDV3":
            assert kwargs["retry_count"] == 0
            assert data["v"] == self.transfer_token
            assert data["s"] == len(self.transfer)
            assert len(data["d"].encode()) == data["l"] <= 50
            last = len(self.transfer) + data["l"] == self.transfer_length
            if last and self.reject_last:
                answer = {"r": 2, "v": self.native["v"]}
            else:
                self.transfer.extend(data["d"].encode())
                if last:
                    row = json.loads(self.transfer)
                    assert isinstance(row, list) and row[0] == 0 and len(row) == 4
                    self.native["d"][0] = row
                    self.native["v"] = 43168
                    if self.contradict_readback:
                        self.native["d"][1][2] = "External edit"
                answer = {"r": 0, "v": self.native["v"]}
        elif command == "SCHDSV3":
            assert data == {"i": 0, "v": self.native["v"], "s": [1, 0]}
            answer = {"r": 0, "v": self.native["v"]}
        else:
            raise AssertionError(command)
        return {"out": [{"r": 0, "d": answer}]}


def _peer(**kwargs):
    client, _ = _client_and_cloud()
    peer = _RowTransport(**kwargs)
    client._sync_get_cloud_protocol = lambda **kwargs: peer
    return client, peer


def _edit(client, **kwargs):
    defaults = dict(
        map_index=0,
        plan_id=0,
        week_day=2,
        task_index=0,
        start=663,
        execute=True,
        confirm_write=True,
    )
    defaults.update(kwargs)
    return client._sync_set_app_schedule_task_start_time(**defaults)


def test_real_start_edit_preserves_native_unknown_bytes_and_other_plan():
    client, peer = _peer()
    result = _edit(client)
    assert result["confirmed"] and result["executed"]
    assert result["version"] == 43168
    before = base64.b64decode(peer.original["d"][0][3])
    after = base64.b64decode(peer.native["d"][0][3])
    assert [
        i
        for i, pair in enumerate(zip(before, after, strict=True))
        if pair[0] != pair[1]
    ] == [17]
    assert peer.native["d"][0][:3] == peer.original["d"][0][:3]
    assert peer.native["d"][1] == peer.original["d"][1]
    assert (
        result["confirmed_schedule"]["plans"][0]["weeks"][2]["tasks"][0]["start"] == 663
    )
    assert "raw_text" not in result["confirmed_schedule"]


def test_dry_run_and_unchanged_target_send_no_physical_setter():
    client, peer = _peer()
    preview = _edit(client, execute=False, confirm_write=False)
    assert not preview["executed"] and preview["changed"]
    matched = _edit(client, start=658)
    assert matched["confirmed"] and not matched["executed"]
    assert all(call["m"] == "g" for call in peer.calls)


@pytest.mark.parametrize(
    "changes",
    [
        {"start": True},
        {"start": 1440},
        {"week_day": 7},
        {"task_index": 1},
        {"map_index": 1},
        {"plan_id": 1},
        {"confirm_write": False},
    ],
)
def test_unqualified_targets_never_send_setters(changes):
    client, peer = _peer()
    with pytest.raises(ValueError):
        _edit(client, **changes)
    assert all(call["m"] == "g" for call in peer.calls)


def test_final_chunk_rejection_is_not_reported_as_success_or_committed():
    client, peer = _peer(reject_last=True)
    with pytest.raises(Exception, match="rejected"):
        _edit(client)
    assert peer.native == peer.original
    assert not any(call["t"] == "SCHDSV3" for call in peer.calls)


def test_ack_without_exact_native_readback_stops_before_status_commit():
    client, peer = _peer(contradict_readback=True)
    with pytest.raises(Exception, match="exact schedule edit"):
        _edit(client)
    assert not any(call["t"] == "SCHDSV3" for call in peer.calls)


def test_paused_unfinished_task_blocks_before_any_setter():
    client, peer = _peer()
    client._sync_update_device.return_value.task_resumable = True
    with pytest.raises(Exception, match="Finish the current"):
        _edit(client)
    assert all(call["m"] == "g" for call in peer.calls)


def test_home_assistant_schema_preserves_whole_minute_time_and_sunday_index():
    parsed = SET_SCHEDULE_TASK_START_TIME_SCHEMA(
        {
            "map_index": 0,
            "plan_id": 0,
            "week_day": 0,
            "start_time": "10:58:00",
        }
    )
    assert parsed["start_time"].hour == 10 and parsed["start_time"].minute == 58
    assert parsed["task_index"] == 0
    assert parsed["execute"] is False and parsed["confirm_schedule_write"] is False


@pytest.mark.parametrize("known_active", [False, True])
def test_time_edit_shared_cache_preserves_readback_and_existing_active_ownership(
    known_active,
):
    client, _ = _peer()
    before = client._sync_get_app_schedules(
        include_raw=False, map_indices=[0], include_current_task=False
    )
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.client = client
    coordinator._schedule_write_lock = asyncio.Lock()
    coordinator.schedules = deepcopy(before)
    coordinator.schedules.update(
        active_schedule_index=0 if known_active else None,
        active_schedule_version=13626,
        active_selection_available=known_active,
    )
    coordinator.app_maps = {
        "current_map_index": 0,
        "map_list_valid": True,
        "maps": [{"idx": 0, "created": True}],
    }
    coordinator.app_maps_refresh_succeeded = True
    coordinator.selected_map_index = 0
    coordinator.schedules_refreshed_at = None
    coordinator.async_refresh_schedules = AsyncMock()
    coordinator.async_update_listeners = Mock()
    result = asyncio.run(
        coordinator.async_set_schedule_task_start_time(
            map_index=0,
            plan_id=0,
            week_day=2,
            task_index=0,
            start=663,
            execute=True,
            confirm_write=True,
        )
    )
    assert result["confirmed"]
    assert coordinator._pending_schedule_status_active_indices == (
        {0} if known_active else set()
    )
    stale = deepcopy(before)
    stale["schedules"][0]["idx"] = None
    coordinator._cache_batch_schedules(
        stale, now=datetime.now(UTC), allow_incomplete=True, allowed_hint_indices=[0]
    )
    entry = schedule_plan_entries(coordinator.schedules)[0]
    assert entry["version"] == 43168
    assert "11:03" in entry["start_times"]
    # The stale cloud token cannot reselect old calendar content. A prior active
    # owner remains bound in the ACK guard; an unknown owner is never invented.
    assert coordinator.schedules.get("active_selection_available") is False
