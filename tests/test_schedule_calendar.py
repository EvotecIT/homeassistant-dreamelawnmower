"""Calendar event helpers for mower-native schedules."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.dreame_lawn_mower.calendar import schedule_calendar_events


def test_schedule_calendar_events_include_enabled_plan_tasks() -> None:
    payload = {
        "schedules": [
            {
                "idx": 0,
                "plans": [
                    {
                        "plan_id": 0,
                        "enabled": True,
                        "weeks": [
                            {
                                "week_day": 0,
                                "tasks": [
                                    {
                                        "type_name": "all_area_mowing",
                                        "start": 658,
                                        "end": 1257,
                                        "cyclic": False,
                                        "regions": [],
                                    }
                                ],
                            }
                        ],
                    },
                    {"plan_id": 1, "enabled": False, "weeks": []},
                ],
            }
        ]
    }

    events = schedule_calendar_events(
        payload,
        datetime(2026, 4, 19, 0, 0, tzinfo=UTC),
        datetime(2026, 4, 20, 0, 0, tzinfo=UTC),
        mower_name="Bodzio",
    )

    assert len(events) == 1
    assert events[0].summary == "All area mowing (map 0 plan 0)"
    assert events[0].start == datetime(2026, 4, 19, 10, 58, tzinfo=UTC)
    assert events[0].end == datetime(2026, 4, 19, 20, 57, tzinfo=UTC)
    assert "Mower: Bodzio" in (events[0].description or "")
    assert events[0].uid == "dreame-mower-0-0-2026-04-19-0"


def test_schedule_calendar_events_include_overnight_overlap() -> None:
    payload = {
        "schedules": [
            {
                "idx": -1,
                "plans": [
                    {
                        "plan_id": 3,
                        "enabled": True,
                        "weeks": [
                            {
                                "week_day": 6,
                                "tasks": [
                                    {
                                        "type_name": "edge_mowing",
                                        "start": 23 * 60 + 30,
                                        "end": 60,
                                        "cyclic": True,
                                        "regions": [[1, 2]],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }

    events = schedule_calendar_events(
        payload,
        datetime(2026, 4, 19, 0, 30, tzinfo=UTC),
        datetime(2026, 4, 19, 2, 0, tzinfo=UTC),
    )

    assert len(events) == 1
    assert events[0].summary == "Edge mowing (default schedule plan 3)"
    assert events[0].start == datetime(2026, 4, 18, 23, 30, tzinfo=UTC)
    assert events[0].end == datetime(2026, 4, 19, 1, 0, tzinfo=UTC)
    assert "Cyclic: yes" in (events[0].description or "")
    assert "Regions: [[1, 2]]" in (events[0].description or "")


def test_schedule_calendar_events_skip_disabled_plans() -> None:
    payload = {
        "schedules": [
            {
                "idx": 0,
                "plans": [
                    {
                        "plan_id": 0,
                        "enabled": False,
                        "weeks": [
                            {
                                "week_day": 0,
                                "tasks": [
                                    {
                                        "type_name": "all_area_mowing",
                                        "start": 600,
                                        "end": 700,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }

    assert (
        schedule_calendar_events(
            payload,
            datetime(2026, 4, 19, 0, 0, tzinfo=UTC),
            datetime(2026, 4, 20, 0, 0, tzinfo=UTC),
        )
        == []
    )
