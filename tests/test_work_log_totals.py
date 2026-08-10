"""Contracts for mower-native lifetime work-log totals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.dreame_lawn_mower.coordinator import (
    WORK_LOG_TOTALS_REFRESH_INTERVAL,
    DreameLawnMowerCoordinator,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client import (
    DreameLawnMowerClient,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerConnectionError,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.work_log import (
    WORK_LOG_TOTALS_REQUEST,
    DreameLawnMowerWorkLogTotals,
    work_log_totals_from_app_data,
)
from custom_components.dreame_lawn_mower.reporting import (
    build_coordinator_diagnostics,
)
from custom_components.dreame_lawn_mower.sensor_work_log import (
    DreameLawnMowerTotalMowedAreaSensor,
    DreameLawnMowerTotalMowingSessionsSensor,
    DreameLawnMowerTotalMowingTimeSensor,
)


def _client() -> DreameLawnMowerClient:
    return DreameLawnMowerClient(
        username="user@example.invalid",
        password="secret",
        country="eu",
        account_type="dreame",
        descriptor=DreameLawnMowerDescriptor(
            did="device-1",
            name="Garden Mower",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
    )


def _totals() -> DreameLawnMowerWorkLogTotals:
    return DreameLawnMowerWorkLogTotals(
        total_mowed_area_sqm=7136.0,
        total_mowing_time_minutes=3073,
        total_mowing_sessions=13,
        history_start_epoch=1_704_038_400,
    )


def test_work_log_totals_decode_vendor_summary() -> None:
    totals = work_log_totals_from_app_data(
        {
            "m": "r",
            "r": 0,
            "d": {
                "area": 7136,
                "time": 3073,
                "count": 13,
                "start": 1_704_038_400,
            },
        }
    )

    assert totals == _totals()


@pytest.mark.parametrize(
    "response",
    [
        {"r": 1, "d": {"area": 1, "time": 1, "count": 1}},
        {"r": 0},
        {"r": 0, "d": {"area": -1, "time": 1, "count": 1}},
        {"r": 0, "d": {"area": 1, "time": 1.5, "count": 1}},
        {"r": 0, "d": {"area": 1, "time": 1, "count": True}},
    ],
)
def test_work_log_totals_reject_invalid_or_partial_summaries(response: dict) -> None:
    with pytest.raises(DreameLawnMowerConnectionError):
        work_log_totals_from_app_data(response)


def test_client_requests_mower_owned_mihis_summary() -> None:
    client = _client()
    calls: list[tuple[dict, dict]] = []

    def call(payload: dict, **kwargs) -> dict:
        calls.append((payload, kwargs))
        return {
            "r": 0,
            "d": {"area": 7136, "time": 3073, "count": 13, "start": 10},
        }

    client._sync_call_app_action = call  # type: ignore[method-assign]

    totals = client._sync_get_work_log_totals()

    assert totals.total_mowing_sessions == 13
    assert calls == [(WORK_LOG_TOTALS_REQUEST, {"redact_response": True})]


@pytest.mark.asyncio
async def test_coordinator_caches_totals_and_preserves_them_on_failure() -> None:
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.work_log_totals = None
    coordinator.work_log_totals_refreshed_at = None
    coordinator.client = SimpleNamespace(
        async_get_work_log_totals=AsyncMock(return_value=_totals())
    )

    first = await coordinator.async_refresh_work_log_totals()
    second = await coordinator.async_refresh_work_log_totals()

    assert first == _totals()
    assert second is first
    coordinator.client.async_get_work_log_totals.assert_awaited_once()
    assert WORK_LOG_TOTALS_REFRESH_INTERVAL == timedelta(minutes=15)

    coordinator.work_log_totals_refreshed_at = None
    coordinator.client.async_get_work_log_totals = AsyncMock(
        side_effect=DreameLawnMowerConnectionError("offline")
    )

    retained = await coordinator.async_refresh_work_log_totals()

    assert retained is first
    assert coordinator.work_log_totals_refreshed_at is None


def test_work_log_sensors_expose_vendor_totals() -> None:
    coordinator = SimpleNamespace(
        data=SimpleNamespace(available=True),
        work_log_totals=_totals(),
    )
    area = object.__new__(DreameLawnMowerTotalMowedAreaSensor)
    area.coordinator = coordinator
    mowing_time = object.__new__(DreameLawnMowerTotalMowingTimeSensor)
    mowing_time.coordinator = coordinator
    sessions = object.__new__(DreameLawnMowerTotalMowingSessionsSensor)
    sessions.coordinator = coordinator

    assert area.native_value == 7136.0
    assert mowing_time.native_value == 3073
    assert sessions.native_value == 13
    assert area.available is True


def test_coordinator_diagnostics_include_only_aggregate_work_log_totals() -> None:
    refreshed_at = datetime(2026, 8, 10, 12, 30, tzinfo=UTC)
    diagnostics = build_coordinator_diagnostics(
        SimpleNamespace(
            last_update_success=True,
            last_exception=None,
            update_interval=timedelta(seconds=30),
            work_log_totals=_totals(),
            work_log_totals_refreshed_at=refreshed_at,
        )
    )

    assert diagnostics["work_log_totals"] == {
        "source": "app_action_mihis",
        "total_mowed_area_sqm": 7136.0,
        "total_mowing_time_minutes": 3073,
        "total_mowing_sessions": 13,
        "refreshed_at": "2026-08-10T12:30:00+00:00",
    }
    assert "history_start" not in repr(diagnostics["work_log_totals"])
