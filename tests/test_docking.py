"""Regression tests for safe mower docking orchestration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client import (
    DreameLawnMowerClient,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.docking import (
    async_stop_then_dock,
)


@pytest.mark.asyncio
async def test_active_mowing_session_stops_before_docking() -> None:
    calls: list[str] = []
    states = iter(("mowing", "idle"))

    async def stop() -> None:
        calls.append("stop")

    async def refresh_state() -> str:
        calls.append("refresh")
        return next(states)

    async def dock() -> None:
        calls.append("dock")

    stopped = await async_stop_then_dock(
        initial_state="mowing",
        stop=stop,
        dock=dock,
        refresh_state=refresh_state,
        initial_delay=0,
        poll_interval=0,
    )

    assert stopped is True
    assert calls == ["stop", "refresh", "refresh", "dock"]


@pytest.mark.asyncio
async def test_idle_mower_docks_without_stop_or_polling() -> None:
    stop = AsyncMock()
    refresh_state = AsyncMock()
    dock = AsyncMock()

    stopped = await async_stop_then_dock(
        initial_state="idle",
        stop=stop,
        dock=dock,
        refresh_state=refresh_state,
        initial_delay=0,
        poll_interval=0,
    )

    assert stopped is True
    stop.assert_not_awaited()
    refresh_state.assert_not_awaited()
    dock.assert_awaited_once()


@pytest.mark.asyncio
async def test_docking_continues_when_stop_state_times_out() -> None:
    stop = AsyncMock()
    refresh_state = AsyncMock(return_value="mowing")
    dock = AsyncMock()

    stopped = await async_stop_then_dock(
        initial_state="mowing",
        stop=stop,
        dock=dock,
        refresh_state=refresh_state,
        timeout=0,
        initial_delay=0,
        poll_interval=0,
    )

    assert stopped is False
    stop.assert_awaited_once()
    dock.assert_awaited_once()


@pytest.mark.asyncio
async def test_dock_without_stopping_preserves_session_by_docking_directly() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client._async_call_device_method = AsyncMock()

    await client.async_dock_without_stopping()

    client._async_call_device_method.assert_awaited_once_with("dock")


@pytest.mark.asyncio
async def test_start_resumes_heartbeat_confirmed_paused_session() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client.async_get_status_blob = AsyncMock(
        return_value=SimpleNamespace(task_resumable=True)
    )
    client._sync_resume_mowing = Mock(return_value={"r": 0})
    client._async_call_device_method = AsyncMock()

    await client.async_start_mowing()

    client._sync_resume_mowing.assert_called_once_with()
    client._async_call_device_method.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_uses_fresh_action_without_resumable_session() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client.async_get_status_blob = AsyncMock(
        return_value=SimpleNamespace(task_resumable=False)
    )
    client._sync_resume_mowing = Mock()
    client._async_call_device_method = AsyncMock()

    await client.async_start_mowing()

    client._sync_resume_mowing.assert_not_called()
    client._async_call_device_method.assert_awaited_once_with("start_mowing")
