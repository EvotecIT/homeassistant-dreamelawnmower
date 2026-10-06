"""Command outcomes when native task metadata lags fresh mowing heartbeats."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client as client_module,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerCommandRejectedError,
)


def _snapshot(**changes: object) -> SimpleNamespace:
    values = {
        "state": "charging",
        "activity": "docked",
        "task_status": "idle",
        "task_status_source": "heartbeat_property_read",
        "task_operation": None,
        "task_region_ids": None,
        "task_area_ids": None,
        "mowing_session_active": False,
        "started": False,
        "mowing": False,
        "paused": False,
    }
    return SimpleNamespace(**(values | changes))


def _new_mowing_session(**changes: object) -> SimpleNamespace:
    return _snapshot(
        **(
            {
                "state": "mowing",
                "activity": "mowing",
                "task_status": "mowing",
                "mowing_session_active": True,
                "started": True,
                "mowing": True,
            }
            | changes
        )
    )


@pytest.fixture(autouse=True)
def _immediate_readbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        client_module, "_TARGETED_TASK_CONFIRMATION_OFFSETS_SECONDS", (0,) * 6
    )


@pytest.mark.parametrize(
    ("method", "sync_method", "arguments"),
    [
        ("async_start_zone_mowing", "_sync_start_zone_mowing", ([2],)),
        ("async_start_spot_mowing", "_sync_start_spot_mowing", ([4],)),
        ("async_start_edge_mowing", "_sync_start_edge_mowing", ([[3, 0]],)),
    ],
)
def test_acknowledged_new_session_accepts_delayed_native_metadata(
    method: str,
    sync_method: str,
    arguments: tuple[object, ...],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The A1 Pro can publish TASK targets after the service deadline."""
    client = object.__new__(DreameLawnMowerClient)
    response = {"r": 0, "d": {"r": 0}}
    setattr(client, sync_method, Mock(return_value=response))
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[_snapshot(), *[_new_mowing_session() for _ in range(6)]]
    )

    result = asyncio.run(getattr(client, method)(*arguments))

    assert result is response
    getattr(client, sync_method).assert_called_once_with(*arguments)
    assert "exact task targets remain unverified" in caplog.text


@pytest.mark.parametrize(
    "readbacks",
    [
        [_new_mowing_session(task_operation=1)] * 6,
        [_new_mowing_session(task_operation=102, task_region_ids=(3,))] * 6,
        [
            _new_mowing_session(task_operation=1),
            *[_new_mowing_session() for _ in range(5)],
        ],
        [_new_mowing_session(task_status_source="heartbeat_realtime")] * 6,
        [_new_mowing_session(task_status="paused", activity="paused")] * 6,
        [_snapshot()] * 6,
    ],
    ids=[
        "wrong-mode",
        "wrong-zone",
        "conflict-then-missing",
        "cached-heartbeat",
        "paused-only",
        "never-started",
    ],
)
def test_acknowledgement_does_not_hide_a_conflict_or_unconfirmed_start(
    readbacks: list[SimpleNamespace],
) -> None:
    client = object.__new__(DreameLawnMowerClient)
    client._sync_start_zone_mowing = Mock(return_value={"r": 0})
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[_snapshot(), *readbacks]
    )

    with pytest.raises(DreameLawnMowerCommandRejectedError):
        asyncio.run(client.async_start_zone_mowing([2]))

    client._sync_start_zone_mowing.assert_called_once_with([2])


def test_lost_acknowledgement_still_requires_exact_native_task() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client._sync_start_zone_mowing = Mock(
        side_effect=DreameLawnMowerConnectionError("reply lost")
    )
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[_snapshot(), *[_new_mowing_session() for _ in range(6)]]
    )

    with pytest.raises(DreameLawnMowerConnectionError, match="could not be confirmed"):
        asyncio.run(client.async_start_zone_mowing([2]))


def test_readback_failure_does_not_accept_a_cached_new_session() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client._sync_start_zone_mowing = Mock(return_value={"r": 0})
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[
            _snapshot(),
            *[DreameLawnMowerConnectionError("readback failed") for _ in range(6)],
        ]
    )
    client._async_cached_authoritative_snapshot = AsyncMock(
        return_value=_new_mowing_session()
    )

    with pytest.raises(DreameLawnMowerCommandRejectedError):
        asyncio.run(client.async_start_zone_mowing([2]))


def test_already_active_unclassified_task_is_not_dispatched_again() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client._sync_start_zone_mowing = Mock(return_value={"r": 0})
    client._async_refresh_authoritative_snapshot = AsyncMock(
        return_value=_new_mowing_session()
    )

    with pytest.raises(DreameLawnMowerCommandRejectedError, match="active task"):
        asyncio.run(client.async_start_zone_mowing([2]))

    client._sync_start_zone_mowing.assert_not_called()
