"""Per-day task attributes consumed by schedule cards."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import schedule_tables
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.schedule import (
    decode_schedule_week_payload,
    encode_schedule_week_payload,
)
from custom_components.dreame_lawn_mower.switch import (
    DreameLawnMowerSchedulePlanSwitch,
    schedule_plan_entries,
)


@pytest.mark.parametrize("enabled", [True, False], ids=["enabled", "disabled"])
def test_plan_tasks_preserve_document_day_and_time_pairs(enabled):
    weeks = decode_schedule_week_payload(
        encode_schedule_week_payload(
            [
                {
                    "week_day": 1,
                    "tasks": [
                        {"type": 0, "start": 480, "end": 540},
                        {"type": 0, "start": 750, "end": 810},
                    ],
                },
                {
                    "week_day": 3,
                    "tasks": [{"type": 0, "start": 1020, "end": 1080}],
                },
            ]
        )
    )
    schedules = {
        "schedules": [
            {
                "idx": 0,
                "available": True,
                "plans": [{"plan_id": 2, "enabled": enabled, "weeks": weeks}],
            }
        ]
    }
    original = deepcopy(schedules)

    entry = schedule_plan_entries(schedules)[0]

    assert entry["tasks"] == [
        {"weekday": "mon", "start_time": "08:00", "end_time": "09:00"},
        {"weekday": "mon", "start_time": "12:30", "end_time": "13:30"},
        {"weekday": "wed", "start_time": "17:00", "end_time": "18:00"},
    ]
    assert entry["weekdays"] == ["mon", "wed"]
    assert entry["start_times"] == ["08:00", "12:30", "17:00"]
    assert entry["task_count"] == 3
    assert entry["enabled"] is enabled
    switch = DreameLawnMowerSchedulePlanSwitch(
        SimpleNamespace(
            schedules=schedules,
            client=SimpleNamespace(descriptor=SimpleNamespace(unique_id="test")),
        ),
        map_index=0,
        plan_id=2,
    )
    assert switch.extra_state_attributes == entry
    assert switch.is_on is enabled
    assert schedules == original


def test_plan_tasks_preserve_table_task_ids_without_inventing_end_times():
    weeks = schedule_tables.combine_schedule_table_weeks(
        [
            schedule_tables.decode_schedule_table_task(
                [0, 1, 0, 480, [1, 3], []], task_id=0
            ),
            schedule_tables.decode_schedule_table_task(
                [7, 1, 0, 1020, [3], []], task_id=7
            ),
        ]
    )
    entry = schedule_plan_entries(
        {
            "schedules": [
                {
                    "idx": 1,
                    "available": True,
                    "plans": [{"plan_id": 4, "enabled": True, "weeks": weeks}],
                }
            ]
        }
    )[0]

    assert entry["tasks"] == [
        {"weekday": "mon", "start_time": "08:00", "task_id": 0},
        {"weekday": "wed", "start_time": "08:00", "task_id": 0},
        {"weekday": "wed", "start_time": "17:00", "task_id": 7},
    ]
    assert entry["weekdays"] == ["mon", "wed"]
    assert entry["start_times"] == ["08:00", "08:00", "17:00"]
    assert entry["task_count"] == 3


@pytest.mark.parametrize("enabled", [True, False], ids=["enabled", "disabled"])
@pytest.mark.parametrize(
    "weeks", [[], [{"week_day_name": "mon", "tasks": []}]], ids=["no-days", "empty-day"]
)
def test_empty_plan_tasks_are_an_empty_list(enabled, weeks):
    entry = schedule_plan_entries(
        {
            "schedules": [
                {
                    "idx": 0,
                    "available": True,
                    "plans": [{"plan_id": 1, "enabled": enabled, "weeks": weeks}],
                }
            ]
        }
    )[0]

    assert entry["tasks"] == []
    assert entry["weekdays"] == []
    assert entry["start_times"] == []
    assert entry["task_count"] == 0
    assert entry["enabled"] is enabled
