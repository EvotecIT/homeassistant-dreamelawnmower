"""Native map calendar isolation and entity discovery contracts."""

from __future__ import annotations

import asyncio
import base64
import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.calendar import (
    DreameLawnMowerMapScheduleCalendar,
    async_setup_entry,
    schedule_calendar_events,
    schedule_calendar_selection,
)
from custom_components.dreame_lawn_mower.schedule_cache import (
    merge_app_schedule_payload,
)
from dreame_lawn_mower_client import decode_schedule_payload_text
from tests.runtime_fixtures import runtime_hass
from tests.test_schedule_documents import _frame


def _native_schedule(index: int, start: int = 658) -> dict:
    text = json.dumps(
        {
            "d": [
                [
                    0,
                    1,
                    "Spring",
                    base64.b64encode(
                        b"".join(_frame(day, start) for day in range(7))
                    ).decode(),
                ],
                [1, 0, "Autumn"],
            ],
            "v": 13626,
        }
    )
    return {
        "idx": index,
        "version": 13626,
        "protocol": "document",
        "read_status": "complete",
        "plans": decode_schedule_payload_text(text),
    }


def test_map_calendar_renders_native_starts_without_active_selection() -> None:
    payload = {
        "active_selection_available": False,
        "active_schedule_version": 23416,
        "schedules": [
            _native_schedule(-1, 540),
            _native_schedule(0),
            _native_schedule(1, 600),
        ],
    }
    before = deepcopy(payload)
    start, end = datetime(2026, 10, 5, tzinfo=UTC), datetime(2026, 10, 12, tzinfo=UTC)
    assert schedule_calendar_events(payload, start, end) == []
    events = schedule_calendar_events(payload, start, end, map_index=0)
    assert len(events) == 7
    assert [(event.start.hour, event.start.minute) for event in events] == [
        (10, 58)
    ] * 7
    assert all((event.end - event.start).total_seconds() == 60 for event in events)
    assert all("map 0 plan 0" in event.summary for event in events)
    selection = schedule_calendar_selection(payload, map_index=0)
    assert selection["mode"] == "map_schedule"
    assert selection["map_index"] == 0
    assert selection["native_schedule_available"] is True
    assert [schedule["idx"] for schedule in selection["included_schedules"]] == [0]
    assert payload == before

    edited = {**_native_schedule(0, 663), "version": 43168}
    refreshed = merge_app_schedule_payload(
        payload, {"schedules": [edited]},
        expected_indices=[-1, 0, 1],
    )
    edited_events = schedule_calendar_events(refreshed, start, end, map_index=0)
    assert len(edited_events) == 7
    assert all((event.start.hour, event.start.minute) == (11, 3)
               for event in edited_events)
    failed = merge_app_schedule_payload(
        refreshed, {"schedules": [{"idx": 0, "error": "Timed out"}]},
        expected_indices=[-1, 0, 1],
    )
    assert schedule_calendar_events(failed, start, end, map_index=0) == edited_events


@pytest.mark.parametrize(
    "state", ["empty", "removed", "failed", "batch_hint", "incomplete"]
)
def test_map_calendar_distinguishes_empty_schedule_from_unknown(state: str) -> None:
    schedule = _native_schedule(0)
    if state == "empty":
        schedule["plans"] = []
    elif state == "failed":
        schedule["error"] = "Read failed"
    elif state == "batch_hint":
        schedule.pop("protocol")
        schedule.pop("read_status")
    elif state == "incomplete":
        schedule["read_status"] = "unknown"
    payload = {"schedules": [] if state == "removed" else [schedule]}
    coordinator = SimpleNamespace(
        data=SimpleNamespace(available=True),
        schedules=payload,
        client=SimpleNamespace(
            descriptor=SimpleNamespace(unique_id="mower", name="Mower")
        ),
    )
    entity = DreameLawnMowerMapScheduleCalendar(coordinator, 0)
    entity._refresh_cached_event_from_payload(
        payload, now=datetime(2026, 10, 5, tzinfo=UTC)
    )
    assert entity.available is (state == "empty")
    assert entity.event is None
    assert entity.extra_state_attributes["schedule_selection"][
        "native_schedule_available"
    ] is (state == "empty")


def test_map_calendars_discover_late_native_slots_once_and_unload_listener() -> None:
    listeners, unloads, entities = [], [], []
    coordinator = SimpleNamespace(
        data=SimpleNamespace(available=True),
        schedules={"schedules": []},
        client=SimpleNamespace(
            descriptor=SimpleNamespace(unique_id="mower", name="Mower")
        ),
        async_add_listener=Mock(
            side_effect=lambda listener: listeners.append(listener) or "unsubscribe"
        ),
    )
    entry = SimpleNamespace(entry_id="entry", async_on_unload=unloads.append)
    entry.runtime_data = coordinator
    hass = runtime_hass(coordinators={"entry": coordinator})
    asyncio.run(async_setup_entry(hass, entry, entities.extend))
    assert len(entities) == 2
    assert unloads == ["unsubscribe"]
    coordinator.schedules = {"schedules": [_native_schedule(-1), _native_schedule(0)]}
    listeners[0]()
    listeners[0]()
    assert len(entities) == 3
    map_zero = entities[-1]
    assert map_zero.name == "Map 0 Schedule"
    assert map_zero.unique_id == "mower_map_0_schedule_calendar"
    coordinator.schedules["schedules"].append(
        {**_native_schedule(1), "protocol": "tables"}
    )
    listeners[0]()
    assert entities[-1].unique_id == "mower_map_1_schedule_calendar"
    coordinator.schedules = {"schedules": [_native_schedule(1)]}
    map_zero._refresh_cached_event_from_payload(coordinator.schedules)
    assert map_zero.available is False
    assert map_zero.event is None
