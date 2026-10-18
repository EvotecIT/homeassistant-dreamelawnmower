"""Command outcomes when native task metadata lags fresh mowing heartbeats."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client as client_module,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    DreameLawnMowerDescriptor,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerCommandRejectedError,
)


def _client() -> DreameLawnMowerClient:
    client = DreameLawnMowerClient(
        username="test", password="test", country="eu", account_type="dreame",
        descriptor=DreameLawnMowerDescriptor(
            did="42", name="Garden", model="dreame.mower.g2408",
            display_model="A2", account_type="dreame", country="eu",
        ),
    )
    client.async_get_current_app_map_index = AsyncMock(return_value=0)
    return client


def _run(client, operation):
    async def scenario():
        try:
            return await operation
        finally:
            await client.async_close()
    return asyncio.run(scenario())


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
    ("method", "operation", "field", "arguments"),
    [
        ("async_start_zone_mowing", 102, "region", ([2],)),
        ("async_start_spot_mowing", 103, "area", ([4],)),
        ("async_start_edge_mowing", 101, "edge", ([[3, 0]],)),
    ],
)
def test_acknowledged_new_session_accepts_delayed_native_metadata(
    method: str,
    operation: int,
    field: str,
    arguments: tuple[object, ...],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The A1 Pro can publish TASK targets after the service deadline."""
    client = _client()
    response = {"r": 0, "d": {"r": 0}}
    client._async_call_mowing_task = AsyncMock(return_value=response)
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[_snapshot(), *[_new_mowing_session() for _ in range(6)]]
    )

    result = _run(client, getattr(client, method)(*arguments))

    assert result is response
    client._async_call_mowing_task.assert_awaited_once_with(
        {"m": "a", "p": 0, "o": operation, "d": {field: arguments[0]}},
        task_name=method.removeprefix("async_start_").replace("_", " "),
    )
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
    client = _client()
    client._async_call_mowing_task = AsyncMock(return_value={"r": 0})
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[_snapshot(), *readbacks]
    )

    with pytest.raises(DreameLawnMowerCommandRejectedError):
        _run(client, client.async_start_zone_mowing([2]))

    client._async_call_mowing_task.assert_awaited_once_with(
        {"m": "a", "p": 0, "o": 102, "d": {"region": [2]}},
        task_name="zone mowing",
    )


def test_lost_acknowledgement_still_requires_exact_native_task() -> None:
    client = _client()
    client._async_call_mowing_task = AsyncMock(
        side_effect=DreameLawnMowerConnectionError("reply lost")
    )
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[_snapshot(), *[_new_mowing_session() for _ in range(6)]]
    )

    with pytest.raises(DreameLawnMowerConnectionError, match="could not be confirmed"):
        _run(client, client.async_start_zone_mowing([2]))


def test_readback_failure_does_not_accept_a_cached_new_session() -> None:
    client = _client()
    client._async_call_mowing_task = AsyncMock(return_value={"r": 0})
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
        _run(client, client.async_start_zone_mowing([2]))


def test_already_active_unclassified_task_is_not_dispatched_again() -> None:
    client = _client()
    client._async_call_mowing_task = AsyncMock(return_value={"r": 0})
    client._async_refresh_authoritative_snapshot = AsyncMock(
        return_value=_new_mowing_session()
    )

    with pytest.raises(DreameLawnMowerCommandRejectedError, match="active task"):
        _run(client, client.async_start_zone_mowing([2]))

    client._async_call_mowing_task.assert_not_awaited()


def _leaving_station() -> SimpleNamespace:
    """Measured +0 s after a LiDAX zone start: mowing state, task starting."""
    return _new_mowing_session(task_status="starting")


def _driving_to_zone() -> SimpleNamespace:
    """Measured +15 s and +30 s: the drive to the zone reads as remote control."""
    return _snapshot(
        state="remote_control", activity="idle", task_status="starting",
        mowing_session_active=True,
    )


def _zone_two_mowing() -> SimpleNamespace:
    """Measured +45 s: the requested zone task is published."""
    return _new_mowing_session(task_operation=102, task_region_ids=(2,))


@pytest.fixture
def _fast_transit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "_TARGETED_TASK_TRANSIT_POLL_SECONDS", 0)


@pytest.mark.usefixtures("_fast_transit")
def test_zone_start_waits_through_the_drive_from_the_station() -> None:
    client = _client()
    response = {"r": 0}
    client._async_call_mowing_task = AsyncMock(return_value=response)
    client._async_refresh_authoritative_snapshot = AsyncMock(side_effect=[
        _snapshot(),  # docked baseline
        _leaving_station(),
        *[_driving_to_zone() for _ in range(5)],  # the 15 s window ends here
        _driving_to_zone(),
        _driving_to_zone(),
        _zone_two_mowing(),
    ])

    assert _run(client, client.async_start_zone_mowing([2])) is response
    assert client._async_refresh_authoritative_snapshot.await_count == 10


@pytest.mark.usefixtures("_fast_transit")
@pytest.mark.parametrize("other", [
    _new_mowing_session(task_operation=1),  # whole-lawn mowing
    _new_mowing_session(task_operation=101),  # edge mowing
    _new_mowing_session(task_operation=102, task_region_ids=(3,)),  # other zone
])
def test_drive_from_the_station_never_hides_another_mode(other) -> None:
    client = _client()
    client._async_call_mowing_task = AsyncMock(return_value={"r": 0})
    client._async_refresh_authoritative_snapshot = AsyncMock(side_effect=[
        _snapshot(), *[_driving_to_zone() for _ in range(7)], other, _zone_two_mowing(),
    ])

    with pytest.raises(DreameLawnMowerCommandRejectedError, match="did not enter"):
        _run(client, client.async_start_zone_mowing([2]))
    # The conflicting readback ends the wait; the later one is never read.
    assert client._async_refresh_authoritative_snapshot.await_count == 9


def test_drive_from_the_station_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "_TARGETED_TASK_TRANSIT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(client_module, "_TARGETED_TASK_TRANSIT_TIMEOUT_SECONDS", 0.1)
    client = _client()
    client._async_call_mowing_task = AsyncMock(return_value={"r": 0})
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[_snapshot(), *[_driving_to_zone() for _ in range(1000)]]
    )

    with pytest.raises(DreameLawnMowerCommandRejectedError, match="did not enter"):
        _run(client, client.async_start_zone_mowing([2]))
    assert client._async_refresh_authoritative_snapshot.await_count < 1000


@pytest.mark.usefixtures("_fast_transit")
def test_pending_control_takes_over_during_the_drive() -> None:
    client = _client()
    client._async_call_mowing_task = AsyncMock(return_value={"r": 0})
    reads = 0

    async def readback(**kwargs):
        nonlocal reads
        reads += 1
        if reads == 8:
            # A pause or dock request is now waiting for the control gate.
            await client._task_control_admission_gate.acquire()
        return _snapshot() if reads == 1 else _driving_to_zone()

    client._async_refresh_authoritative_snapshot = readback

    assert _run(client, client.async_start_zone_mowing([2])) == {"r": 0}
    assert reads == 8


def test_station_without_transit_still_fails_after_the_short_window() -> None:
    client = _client()
    client._async_call_mowing_task = AsyncMock(return_value={"r": 0})
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[_snapshot(), *[_snapshot() for _ in range(6)]]
    )

    with pytest.raises(DreameLawnMowerCommandRejectedError, match="did not enter"):
        _run(client, client.async_start_zone_mowing([2]))
    assert client._async_refresh_authoritative_snapshot.await_count == 7


def test_failed_transit_reads_cannot_confirm_an_old_departure(monkeypatch) -> None:
    monkeypatch.setattr(client_module, "_TARGETED_TASK_TRANSIT_POLL_SECONDS", 0.002)
    monkeypatch.setattr(client_module, "_TARGETED_TASK_TRANSIT_TIMEOUT_SECONDS", 0.04)
    client = _client()
    client._async_call_mowing_task = AsyncMock(return_value={"r": 0})
    reads = 0

    async def readback(**kwargs):
        nonlocal reads
        reads += 1
        if reads == 1:
            return _snapshot()
        if reads <= 7:
            return _leaving_station()
        raise DreameLawnMowerConnectionError("link unavailable")

    client._async_refresh_authoritative_snapshot = readback
    with pytest.raises(DreameLawnMowerConnectionError):
        _run(client, client.async_start_zone_mowing([2]))
    assert reads > 7


@pytest.mark.usefixtures("_fast_transit")
def test_transit_can_recover_with_a_new_authoritative_task() -> None:
    client = _client()
    client._async_call_mowing_task = AsyncMock(return_value={"r": 0})
    client._async_refresh_authoritative_snapshot = AsyncMock(side_effect=[
        _snapshot(), *[_driving_to_zone() for _ in range(6)],
        DreameLawnMowerConnectionError("temporary read failure"), _zone_two_mowing(),
    ])
    assert _run(client, client.async_start_zone_mowing([2])) == {"r": 0}


@pytest.mark.usefixtures("_fast_transit")
def test_queued_pause_outlives_a_stalled_transit_read(monkeypatch) -> None:
    monkeypatch.setattr(
        client_module, "_TARGETED_TASK_TRANSIT_READ_TIMEOUT_SECONDS", 0.02,
        raising=False,
    )
    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
        "client_task_control._WAIT_TIMEOUT_SECONDS", 0.15,
    )
    dispatch_pause = AsyncMock()
    monkeypatch.setattr(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
        "client_device_actions.async_run_device_plan", dispatch_pause,
    )

    async def scenario():
        client = _client()
        client._async_call_mowing_task = AsyncMock(return_value={"r": 0})
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        never = asyncio.Event()
        reads = 0

        async def readback(**kwargs):
            nonlocal reads
            reads += 1
            if reads == 1:
                return _snapshot()
            if reads <= 7:
                return _driving_to_zone()
            entered.set()
            try:
                await never.wait()
            finally:
                cancelled.set()

        client._async_refresh_authoritative_snapshot = readback
        start = asyncio.create_task(client.async_start_zone_mowing([2]))
        pause = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            pause = asyncio.create_task(client.async_pause())
            await asyncio.wait_for(pause, 0.5)
            result = (await asyncio.gather(start, return_exceptions=True))[0]
            assert isinstance(result, DreameLawnMowerConnectionError)
            assert cancelled.is_set()
            dispatch_pause.assert_awaited_once()
            assert not client._task_control_gate.locked()
        finally:
            for task in (start, pause):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *[task for task in (start, pause) if task is not None],
                return_exceptions=True,
            )
            await client.async_close()

    asyncio.run(scenario())
