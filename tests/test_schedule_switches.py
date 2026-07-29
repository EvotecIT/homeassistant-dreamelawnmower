"""Contract checks for user-facing mower schedule controls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

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
    coordinator.schedules = {
        "schedules": [
            {
                "idx": 1,
                "plans": [{"plan_id": 2, "enabled": True}],
            }
        ]
    }

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
    assert coordinator.schedules["schedules"][0]["plans"][0]["enabled"] is False


def test_schedule_write_keeps_confirmed_state_when_readback_fails() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._schedule_write_lock = asyncio.Lock()
    coordinator.client = SimpleNamespace(
        async_set_app_schedule_plan_enabled=AsyncMock(
            return_value={"executed": True, "enabled": False}
        )
    )
    coordinator.async_refresh_schedules = AsyncMock(
        side_effect=RuntimeError("readback unavailable")
    )
    coordinator.async_update_listeners = Mock()
    coordinator.last_schedule_write_result = None
    coordinator.schedules = {
        "schedules": [
            {
                "idx": 1,
                "plans": [{"plan_id": 2, "enabled": True}],
            }
        ]
    }

    with pytest.raises(RuntimeError, match="readback unavailable"):
        asyncio.run(
            coordinator.async_set_schedule_plan_enabled(
                map_index=1,
                plan_id=2,
                enabled=False,
            )
        )

    assert coordinator.schedules["schedules"][0]["plans"][0]["enabled"] is False
    coordinator.async_update_listeners.assert_called_once()


def test_schedule_write_reconciles_unknown_active_fallback_by_version() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._schedule_write_lock = asyncio.Lock()
    coordinator.client = SimpleNamespace(
        async_set_app_schedule_plan_enabled=AsyncMock(
            return_value={
                "executed": True,
                "enabled": False,
                "version": 8,
            }
        )
    )
    coordinator.async_refresh_schedules = AsyncMock(
        side_effect=RuntimeError("readback unavailable")
    )
    coordinator.async_update_listeners = Mock()
    coordinator.last_schedule_write_result = None
    coordinator.schedules = {
        "active_schedule_version": 8,
        "schedules": [
            {
                "idx": None,
                "version": 8,
                "writable": False,
                "plans": [{"plan_id": 2, "enabled": True}],
            }
        ],
    }

    with pytest.raises(RuntimeError, match="readback unavailable"):
        asyncio.run(
            coordinator.async_set_schedule_plan_enabled(
                map_index=1,
                plan_id=2,
                enabled=False,
            )
        )

    assert coordinator.schedules["schedules"][0]["plans"][0]["enabled"] is False
    coordinator.async_update_listeners.assert_called_once()


def test_schedule_upload_invalidates_unknown_active_fallback_by_version() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._schedule_write_lock = asyncio.Lock()
    coordinator.client = SimpleNamespace(
        async_plan_app_schedule_upload=AsyncMock(
            return_value={
                "executed": True,
                "request_count": 2,
                "version": 8,
            }
        )
    )
    coordinator.async_refresh_schedules = AsyncMock(
        side_effect=RuntimeError("readback unavailable")
    )
    coordinator.async_update_listeners = Mock()
    coordinator.last_schedule_write_result = None
    coordinator.schedules_refreshed_at = object()
    coordinator.schedules = {
        "active_schedule_version": 8,
        "current_task": {"version": 8},
        "schedules": [
            {"idx": -1, "available": True, "version": 7, "plans": []},
            {
                "idx": None,
                "available": True,
                "version": 8,
                "writable": False,
                "plans": [{"plan_id": 2, "enabled": True}],
            },
        ],
    }

    with pytest.raises(RuntimeError, match="readback unavailable"):
        asyncio.run(
            coordinator.async_plan_schedule_upload(
                map_index=1,
                plans=[{"plan_id": 2, "enabled": False, "weeks": []}],
                chunk_size=100,
                execute=True,
                confirm_write=True,
            )
        )

    assert [schedule["idx"] for schedule in coordinator.schedules["schedules"]] == [
        -1
    ]
    assert "active_schedule_version" not in coordinator.schedules
    assert "current_task" not in coordinator.schedules
    assert coordinator.schedules_refreshed_at is None
    coordinator.async_update_listeners.assert_called_once()


def test_forced_schedule_readback_propagates_cloud_failure() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.client = SimpleNamespace(
        async_get_app_schedules=AsyncMock(
            side_effect=RuntimeError("readback unavailable")
        )
    )
    coordinator.schedules = {"schedules": []}
    coordinator.schedules_refreshed_at = None

    with pytest.raises(RuntimeError, match="readback unavailable"):
        asyncio.run(coordinator.async_refresh_schedules(force=True))

    assert coordinator.schedules == {"schedules": []}


def test_schedule_upload_force_refreshes_shared_cache_after_execution() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator._schedule_write_lock = asyncio.Lock()
    coordinator.client = SimpleNamespace(
        async_plan_app_schedule_upload=AsyncMock(
            return_value={"executed": True, "request_count": 2}
        ),
        async_get_app_schedules=AsyncMock(
            return_value={
                "source": "app_action_schedule",
                "schedules": [
                    {"idx": -1, "available": True, "version": 7, "plans": []},
                    {"idx": 0, "available": False, "error": "timed out"},
                    {"idx": 1, "available": True, "version": 10, "plans": []},
                ],
                "errors": [
                    {"idx": 0, "stage": "schedule", "error": "timed out"}
                ],
            }
        ),
        async_get_batch_schedules=AsyncMock(side_effect=TimeoutError),
    )
    coordinator.async_update_listeners = Mock()
    coordinator.last_schedule_write_result = None
    coordinator.schedules_refreshed_at = object()
    coordinator.selected_map_index = 0
    coordinator.app_maps = {
        "current_map_index": 0,
        "maps": [
            {"idx": 0, "created": True},
            {"idx": 1, "created": True},
        ],
    }
    coordinator.schedules = {
        "active_schedule_version": 8,
        "current_task": {"version": 8},
        "schedules": [
            {"idx": -1, "available": True, "version": 7, "plans": []},
            {
                "idx": 0,
                "available": True,
                "version": 8,
                "plans": [{"plan_id": 9, "enabled": True}],
            },
            {
                "idx": None,
                "available": True,
                "version": 8,
                "plans": [{"plan_id": 9, "enabled": True}],
                "writable": False,
            },
            {"idx": 1, "available": True, "version": 10, "plans": []},
        ],
    }
    plans = [{"plan_id": 1, "enabled": True, "weeks": []}]

    result = asyncio.run(
        coordinator.async_plan_schedule_upload(
            map_index=0,
            plans=plans,
            chunk_size=100,
            execute=True,
            confirm_write=True,
        )
    )

    assert result == {"executed": True, "request_count": 2}
    coordinator.client.async_plan_app_schedule_upload.assert_awaited_once_with(
        map_index=0,
        plans=plans,
        chunk_size=100,
        execute=True,
        confirm_write=True,
    )
    assert coordinator.schedules_refreshed_at is not None
    assert [schedule["idx"] for schedule in coordinator.schedules["schedules"]] == [
        -1,
        1,
        0,
    ]
    uploaded_slot = next(
        schedule
        for schedule in coordinator.schedules["schedules"]
        if schedule["idx"] == 0
    )
    assert uploaded_slot["error"] == "timed out"
    assert "plans" not in uploaded_slot
    assert "active_schedule_version" not in coordinator.schedules
    assert "current_task" not in coordinator.schedules
    coordinator.client.async_get_app_schedules.assert_awaited_once_with(
        include_current_task=False,
        map_indices=[-1, 0, 1],
    )
    coordinator.async_update_listeners.assert_called_once()


def test_schedule_upload_discards_refresh_started_before_confirmed_write() -> None:
    async def exercise() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        coordinator._schedule_write_lock = asyncio.Lock()
        coordinator._schedule_cache_generation = 0
        coordinator.async_update_listeners = Mock()
        coordinator.last_schedule_write_result = None
        coordinator.schedules_refreshed_at = None
        coordinator.selected_map_index = 0
        coordinator.app_maps = {
            "current_map_index": 0,
            "maps": [
                {"idx": 0, "created": True},
                {"idx": 1, "created": True},
            ],
        }
        coordinator.schedules = {
            "source": "app_action_schedule",
            "schedules": [
                {"idx": -1, "available": True, "version": 7, "plans": []},
                {
                    "idx": 0,
                    "available": True,
                    "version": 8,
                    "plans": [{"plan_id": 9}],
                },
                {"idx": 1, "available": True, "version": 10, "plans": []},
            ],
            "errors": [],
        }
        stale_read_started = asyncio.Event()
        release_stale_read = asyncio.Event()
        read_count = 0

        async def read_schedules(**_kwargs: object) -> dict[str, object]:
            nonlocal read_count
            read_count += 1
            if read_count == 1:
                stale_read_started.set()
                await release_stale_read.wait()
                return {
                    "source": "app_action_schedule",
                    "schedules": [
                        {"idx": -1, "available": True, "version": 7, "plans": []},
                        {
                            "idx": 0,
                            "available": True,
                            "version": 8,
                            "plans": [{"plan_id": 9}],
                        },
                        {"idx": 1, "available": True, "version": 10, "plans": []},
                    ],
                    "errors": [],
                }
            return {
                "source": "app_action_schedule",
                "schedules": [
                    {"idx": -1, "available": True, "version": 7, "plans": []},
                    {"idx": 0, "available": False, "error": "timed out"},
                    {"idx": 1, "available": True, "version": 10, "plans": []},
                ],
                "errors": [
                    {"idx": 0, "stage": "schedule", "error": "timed out"}
                ],
            }

        coordinator.client = SimpleNamespace(
            async_plan_app_schedule_upload=AsyncMock(
                return_value={"executed": True, "request_count": 2}
            ),
            async_get_app_schedules=read_schedules,
            async_get_batch_schedules=AsyncMock(side_effect=TimeoutError),
        )

        stale_refresh = asyncio.create_task(
            coordinator.async_refresh_schedules(force=True)
        )
        await stale_read_started.wait()
        await coordinator.async_plan_schedule_upload(
            map_index=0,
            plans=[{"plan_id": 1, "enabled": True, "weeks": []}],
            chunk_size=100,
            execute=True,
            confirm_write=True,
        )
        release_stale_read.set()
        await stale_refresh

        uploaded_slot = next(
            schedule
            for schedule in coordinator.schedules["schedules"]
            if schedule["idx"] == 0
        )
        assert uploaded_slot["error"] == "timed out"
        assert "plans" not in uploaded_slot
        assert coordinator._schedule_cache_generation == 1

    asyncio.run(exercise())


def test_executed_schedule_upload_waits_for_shared_write_lock() -> None:
    async def exercise() -> None:
        coordinator = object.__new__(DreameLawnMowerCoordinator)
        coordinator._schedule_write_lock = asyncio.Lock()
        coordinator.client = SimpleNamespace(
            async_plan_app_schedule_upload=AsyncMock(
                return_value={"executed": True, "request_count": 1}
            )
        )
        coordinator.async_refresh_schedules = AsyncMock(return_value={"schedules": []})
        coordinator.async_update_listeners = Mock()
        coordinator.last_schedule_write_result = None
        coordinator.schedules_refreshed_at = object()

        await coordinator._schedule_write_lock.acquire()
        upload = asyncio.create_task(
            coordinator.async_plan_schedule_upload(
                map_index=0,
                plans=[{"plan_id": 1, "enabled": True, "weeks": []}],
                chunk_size=100,
                execute=True,
                confirm_write=True,
            )
        )
        await asyncio.sleep(0)

        coordinator.client.async_plan_app_schedule_upload.assert_not_awaited()
        coordinator._schedule_write_lock.release()
        await upload
        coordinator.client.async_plan_app_schedule_upload.assert_awaited_once()

    asyncio.run(exercise())
