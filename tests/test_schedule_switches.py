"""Contract checks for user-facing mower schedule controls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from custom_components.dreame_lawn_mower.coordinator import (
    DreameLawnMowerCoordinator,
)
from custom_components.dreame_lawn_mower.switch import schedule_plan_entries


def test_schedule_plan_entries_flatten_stable_switch_metadata() -> None:
    result = schedule_plan_entries(
        {
            "schedules": [
                {
                    "idx": 0,
                    "label": "garden",
                    "available": True,
                    "version": 44,
                    "plans": [
                        {
                            "plan_id": 2,
                            "enabled": True,
                            "name": "Morning",
                            "weeks": [
                                {
                                    "week_day_name": "mon",
                                    "tasks": [
                                        {
                                            "start_time": "08:15",
                                            "end_time": "09:00",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )

    assert result == [
        {
            "schedule_control": True,
            "map_index": 0,
            "map_label": "garden",
            "plan_id": 2,
            "name": "Morning",
            "enabled": True,
            "version": 44,
            "weekdays": ["mon"],
            "start_times": ["08:15"],
            "task_count": 1,
        }
    ]


def test_schedule_plan_entries_ignore_unavailable_or_malformed_plans() -> None:
    assert (
        schedule_plan_entries(
            {
                "schedules": [
                    {"idx": 0, "available": False, "plans": [{"plan_id": 1}]},
                    {"idx": "bad", "available": True, "plans": [{"plan_id": 1}]},
                    {"idx": 1, "available": True, "plans": [{"plan_id": "bad"}]},
                ]
            }
        )
        == []
    )


def test_schedule_write_reconciles_shared_cache_and_listeners() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._schedule_write_lock = asyncio.Lock()
    coordinator.client = SimpleNamespace(
        async_set_app_schedule_plan_enabled=AsyncMock(
            return_value={"executed": True, "enabled": False}
        )
    )
    coordinator.async_refresh_schedules = AsyncMock(return_value={"available": True})
    coordinator.async_update_listeners = Mock()
    coordinator.last_schedule_write_result = None

    result = asyncio.run(
        coordinator.async_set_schedule_plan_enabled(
            map_index=1,
            plan_id=2,
            enabled=False,
        )
    )

    assert result == {"executed": True, "enabled": False}
    coordinator.client.async_set_app_schedule_plan_enabled.assert_awaited_once_with(
        map_index=1,
        plan_id=2,
        enabled=False,
        execute=True,
        confirm_write=True,
    )
    coordinator.async_refresh_schedules.assert_awaited_once_with(force=True)
    coordinator.async_update_listeners.assert_called_once()
    assert coordinator.last_schedule_write_result == result
