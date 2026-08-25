"""Regression tests for safe mower docking orchestration."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerTaskStatus,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.docking import (
    async_stop_then_dock,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DeviceCommandRejectedException,
    DeviceUpdateFailedException,
    DreameLawnMowerCommandRejectedError,
)


def test_active_mowing_session_stops_before_docking() -> None:
    calls: list[str] = []
    states = iter(("mowing", "idle"))

    async def stop() -> None:
        calls.append("stop")

    async def refresh_state() -> str:
        calls.append("refresh")
        return next(states)

    async def dock() -> None:
        calls.append("dock")

    stopped = asyncio.run(
        async_stop_then_dock(
            initial_state="mowing",
            stop=stop,
            dock=dock,
            refresh_state=refresh_state,
            initial_delay=0,
            poll_interval=0,
        )
    )

    assert stopped is True
    assert calls == ["stop", "refresh", "refresh", "dock"]


def test_idle_mower_docks_without_stop_or_polling() -> None:
    stop = AsyncMock()
    refresh_state = AsyncMock()
    dock = AsyncMock()

    stopped = asyncio.run(
        async_stop_then_dock(
            initial_state="idle",
            stop=stop,
            dock=dock,
            refresh_state=refresh_state,
            initial_delay=0,
            poll_interval=0,
        )
    )

    assert stopped is True
    stop.assert_not_awaited()
    refresh_state.assert_not_awaited()
    dock.assert_awaited_once()


def test_interrupted_session_stops_before_normal_docking() -> None:
    for initial_state in ("paused", "monitoring_paused", "returning"):
        stop = AsyncMock()
        refresh_state = AsyncMock(return_value="idle")
        dock = AsyncMock()

        stopped = asyncio.run(
            async_stop_then_dock(
                initial_state=initial_state,
                stop=stop,
                dock=dock,
                refresh_state=refresh_state,
                initial_delay=0,
                poll_interval=0,
            )
        )

        assert stopped is True
        stop.assert_awaited_once()
        refresh_state.assert_awaited_once()
        dock.assert_awaited_once()


def test_docking_continues_when_stop_state_times_out() -> None:
    stop = AsyncMock()
    refresh_state = AsyncMock(return_value="mowing")
    dock = AsyncMock()

    stopped = asyncio.run(
        async_stop_then_dock(
            initial_state="mowing",
            stop=stop,
            dock=dock,
            refresh_state=refresh_state,
            timeout=0,
            initial_delay=0,
            poll_interval=0,
        )
    )

    assert stopped is False
    stop.assert_awaited_once()
    dock.assert_awaited_once()


def test_docking_continues_when_stop_state_refresh_fails() -> None:
    stop = AsyncMock()
    refresh_state = AsyncMock(side_effect=OSError("cloud unavailable"))
    dock = AsyncMock()

    stopped = asyncio.run(
        async_stop_then_dock(
            initial_state="mowing",
            stop=stop,
            dock=dock,
            refresh_state=refresh_state,
            initial_delay=0,
            poll_interval=0,
        )
    )

    assert stopped is False
    stop.assert_awaited_once()
    refresh_state.assert_awaited_once()
    dock.assert_awaited_once()


def test_docking_continues_when_stop_action_fails() -> None:
    stop = AsyncMock(side_effect=RuntimeError("stop unavailable"))
    refresh_state = AsyncMock()
    dock = AsyncMock()

    stopped = asyncio.run(
        async_stop_then_dock(
            initial_state="paused",
            stop=stop,
            dock=dock,
            refresh_state=refresh_state,
            initial_delay=0,
            poll_interval=0,
        )
    )

    assert stopped is False
    stop.assert_awaited_once()
    refresh_state.assert_not_awaited()
    dock.assert_awaited_once()


def test_dock_without_stopping_preserves_session_by_docking_directly() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client._async_call_device_method = AsyncMock()

    asyncio.run(client.async_dock_without_stopping())

    client._async_call_device_method.assert_awaited_once_with("dock")


def test_start_resumes_heartbeat_confirmed_paused_session() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client.async_get_status_blob = AsyncMock(
        return_value=SimpleNamespace(task_resumable=True)
    )
    client._sync_resume_mowing = Mock(return_value={"r": 0})
    client._async_call_device_method = AsyncMock()

    started_new_session = asyncio.run(client.async_start_mowing())

    client.async_get_status_blob.assert_awaited_once_with(
        refresh=True,
        include_cloud=True,
    )
    client._sync_resume_mowing.assert_called_once_with()
    client._async_call_device_method.assert_not_awaited()
    assert started_new_session is False


def test_lost_resume_acknowledgement_requires_transition_out_of_paused() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client.async_get_status_blob = AsyncMock(
        return_value=SimpleNamespace(task_resumable=True)
    )
    client._sync_resume_mowing = Mock(
        side_effect=DreameLawnMowerConnectionError("resume reply lost")
    )
    client._async_refresh_authoritative_snapshot = AsyncMock(
        return_value=SimpleNamespace(
            state="paused",
            task_status="paused",
            task_resumable=True,
            started=True,
            mowing=False,
            mowing_session_active=True,
        )
    )

    with (
        patch(
            "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
            "client_core.asyncio.sleep",
            AsyncMock(),
        ),
        pytest.raises(
            DreameLawnMowerConnectionError,
            match="could not be confirmed",
        ),
    ):
        asyncio.run(client.async_start_mowing())

    client._sync_resume_mowing.assert_called_once_with()
    assert client._async_refresh_authoritative_snapshot.await_count == 3


def test_lost_resume_acknowledgement_accepts_active_mowing_transition() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client.async_get_status_blob = AsyncMock(
        return_value=SimpleNamespace(task_resumable=True)
    )
    client._sync_resume_mowing = Mock(
        side_effect=DreameLawnMowerConnectionError("resume reply lost")
    )
    client._async_refresh_authoritative_snapshot = AsyncMock(
        return_value=SimpleNamespace(
            state="mowing",
            task_status="mowing",
            task_resumable=False,
            started=True,
            mowing=True,
            mowing_session_active=True,
        )
    )

    with patch(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
        "client_core.asyncio.sleep",
        AsyncMock(),
    ):
        asyncio.run(client.async_start_mowing())

    client._sync_resume_mowing.assert_called_once_with()
    client._async_refresh_authoritative_snapshot.assert_awaited_once_with()


def test_start_uses_fresh_action_without_resumable_session() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client.async_get_status_blob = AsyncMock(
        return_value=SimpleNamespace(
            task_status="idle",
            task_resumable=False,
            mowing_session_active=False,
        )
    )
    client._sync_resume_mowing = Mock()
    client._async_call_start_mowing_with_session_identity = AsyncMock(
        return_value=True
    )

    started_new_session = asyncio.run(client.async_start_mowing())

    client.async_get_status_blob.assert_awaited_once_with(
        refresh=True,
        include_cloud=True,
    )
    client._sync_resume_mowing.assert_not_called()
    client._async_call_start_mowing_with_session_identity.assert_awaited_once_with()
    assert started_new_session is True


def test_duplicate_start_preserves_active_mission_identity() -> None:
    """Starting an already active mower is not a fresh mission boundary."""
    client = object.__new__(DreameLawnMowerClient)
    client.async_get_status_blob = AsyncMock(
        return_value=SimpleNamespace(
            task_status="mowing",
            task_resumable=False,
            mowing_session_active=True,
        )
    )
    client._async_get_cached_start_mowing_session_identity = AsyncMock(
        return_value=False
    )
    client._async_call_start_mowing_with_session_identity = AsyncMock()

    started_new_session = asyncio.run(client.async_start_mowing())

    client._async_call_start_mowing_with_session_identity.assert_not_awaited()
    assert started_new_session is False


def test_start_while_returning_forwards_resume_without_new_session() -> None:
    """Start can abort a return-to-dock without replacing mission identity."""
    client = object.__new__(DreameLawnMowerClient)
    client.async_get_status_blob = AsyncMock(
        return_value=SimpleNamespace(
            task_status="returning_to_dock",
            task_resumable=False,
            mowing_session_active=True,
        )
    )
    client._async_call_start_mowing_with_session_identity = AsyncMock(
        return_value=False
    )

    started_new_session = asyncio.run(client.async_start_mowing())

    client._async_call_start_mowing_with_session_identity.assert_awaited_once_with()
    assert started_new_session is False


def test_stale_inactive_heartbeat_uses_device_start_identity() -> None:
    """A retained inactive blob cannot reset a mission already in progress."""
    start_mowing = Mock(return_value={"result": 0})
    client = object.__new__(DreameLawnMowerClient)
    client.async_get_status_blob = AsyncMock(
        return_value=SimpleNamespace(
            task_status="idle",
            task_resumable=False,
            mowing_session_active=False,
        )
    )
    client._ensure_device = Mock(
        return_value=SimpleNamespace(
            status=SimpleNamespace(
                task_status=DreameMowerTaskStatus.AUTO_CLEANING,
                started=True,
            ),
            start_mowing=start_mowing,
        )
    )

    started_new_session = asyncio.run(client.async_start_mowing())

    start_mowing.assert_called_once_with()
    assert started_new_session is False


def test_stale_active_heartbeat_cannot_hide_fresh_device_start() -> None:
    """A cached active blob is skipped only when device state still agrees."""
    start_mowing = Mock(return_value={"result": 0})
    client = object.__new__(DreameLawnMowerClient)
    client.async_get_status_blob = AsyncMock(
        return_value=SimpleNamespace(
            task_status="mowing",
            task_resumable=False,
            mowing_session_active=True,
        )
    )
    client._ensure_device = Mock(
        return_value=SimpleNamespace(
            status=SimpleNamespace(
                task_status=DreameMowerTaskStatus.COMPLETED,
                started=False,
            ),
            start_mowing=start_mowing,
        )
    )

    started_new_session = asyncio.run(client.async_start_mowing())

    start_mowing.assert_called_once_with()
    assert started_new_session is True


def test_start_with_unrecognized_heartbeat_uses_cached_identity() -> None:
    """A decoded blob without task state cannot declare a fresh mission."""
    client = object.__new__(DreameLawnMowerClient)
    client.async_get_status_blob = AsyncMock(
        return_value=SimpleNamespace(
            task_status=None,
            task_resumable=None,
            mowing_session_active=None,
        )
    )
    client._async_call_start_mowing_with_session_identity = AsyncMock(
        return_value=None
    )

    result = asyncio.run(client.async_start_mowing())

    client._async_call_start_mowing_with_session_identity.assert_awaited_once_with()
    assert result is None


@pytest.mark.parametrize("expected_new_session", (False, True, None))
def test_start_without_heartbeat_preserves_cached_session_identity(
    expected_new_session: bool | None,
) -> None:
    """Heartbeat failure returns the fallback command's session identity."""
    client = object.__new__(DreameLawnMowerClient)
    client.async_get_status_blob = AsyncMock(
        side_effect=DreameLawnMowerConnectionError("status unavailable")
    )
    client._async_call_start_mowing_with_session_identity = AsyncMock(
        return_value=expected_new_session
    )

    started_new_session = asyncio.run(client.async_start_mowing())

    client._async_call_start_mowing_with_session_identity.assert_awaited_once_with()
    assert started_new_session is expected_new_session


@pytest.mark.parametrize(
    ("task_status", "started", "expected_new_session"),
    (
        (DreameMowerTaskStatus.AUTO_CLEANING, True, False),
        (DreameMowerTaskStatus.COMPLETED, False, True),
        (DreameMowerTaskStatus.UNKNOWN, True, None),
        (None, None, None),
    ),
)
def test_fallback_start_captures_device_session_decision(
    task_status: DreameMowerTaskStatus | None,
    started: bool | None,
    expected_new_session: bool | None,
) -> None:
    """The fallback result mirrors device.start_mowing's cached-state branch."""
    start_mowing = Mock(return_value={"result": 0})
    client = object.__new__(DreameLawnMowerClient)
    client._ensure_device = Mock(
        return_value=SimpleNamespace(
            status=SimpleNamespace(task_status=task_status, started=started),
            start_mowing=start_mowing,
        )
    )

    result = asyncio.run(client._async_call_start_mowing_with_session_identity())

    start_mowing.assert_called_once_with()
    assert result is expected_new_session


def test_fallback_start_locks_identity_decision_through_dispatch() -> None:
    """MQTT cannot change the start branch between inspection and dispatch."""

    class StateLock:
        held = False

        def __enter__(self) -> None:
            self.held = True

        def __exit__(self, *_args: object) -> None:
            self.held = False

    state_lock = StateLock()

    class LockedStatus:
        @property
        def task_status(self) -> DreameMowerTaskStatus:
            assert state_lock.held
            return DreameMowerTaskStatus.COMPLETED

        @property
        def started(self) -> bool:
            assert state_lock.held
            return False

    def start_mowing() -> dict[str, int]:
        assert state_lock.held
        return {"result": 0}

    client = object.__new__(DreameLawnMowerClient)
    client._ensure_device = Mock(
        return_value=SimpleNamespace(
            _state_lock=state_lock,
            status=LockedStatus(),
            start_mowing=start_mowing,
        )
    )

    result = asyncio.run(client._async_call_start_mowing_with_session_identity())

    assert result is True
    assert state_lock.held is False


def test_fallback_start_does_not_reclassify_paused_return_to_dock() -> None:
    """A special resume branch remains part of the existing mower task."""
    start_mowing = Mock(return_value={"result": 0})
    client = object.__new__(DreameLawnMowerClient)
    client._ensure_device = Mock(
        return_value=SimpleNamespace(
            status=SimpleNamespace(started=False, returning_paused=True),
            start_mowing=start_mowing,
        )
    )

    result = asyncio.run(client._async_call_start_mowing_with_session_identity())

    start_mowing.assert_called_once_with()
    assert result is False


def test_lost_start_acknowledgement_reconciles_without_resending() -> None:
    client = object.__new__(DreameLawnMowerClient)
    start = Mock(
        side_effect=DeviceUpdateFailedException(
            "The mower did not acknowledge START_MOWING."
        )
    )
    client._ensure_device = Mock(return_value=SimpleNamespace(start_mowing=start))
    client._async_refresh_authoritative_snapshot = AsyncMock(
        return_value=SimpleNamespace(
            started=True,
            mowing=True,
            mowing_session_active=True,
        )
    )

    with patch(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
        "client_core.asyncio.sleep",
        AsyncMock(),
    ):
        asyncio.run(client._async_call_device_method("start_mowing"))

    start.assert_called_once_with()
    client._async_refresh_authoritative_snapshot.assert_awaited_once_with()


def test_lost_start_acknowledgement_fails_when_state_cannot_be_confirmed() -> None:
    client = object.__new__(DreameLawnMowerClient)
    start = Mock(side_effect=DeviceUpdateFailedException("reply lost"))
    client._ensure_device = Mock(return_value=SimpleNamespace(start_mowing=start))
    client._async_refresh_authoritative_snapshot = AsyncMock(
        return_value=SimpleNamespace(
            started=False,
            mowing=False,
            mowing_session_active=False,
        )
    )

    with (
        patch(
            "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
            "client_core.asyncio.sleep",
            AsyncMock(),
        ),
        pytest.raises(
            DreameLawnMowerConnectionError,
            match="could not be confirmed",
        ),
    ):
        asyncio.run(client._async_call_device_method("start_mowing"))

    start.assert_called_once_with()
    assert client._async_refresh_authoritative_snapshot.await_count == 3


def test_explicit_start_rejection_is_not_reconciled_from_old_state() -> None:
    client = object.__new__(DreameLawnMowerClient)
    start = Mock(side_effect=DeviceCommandRejectedException("mower is busy"))
    client._ensure_device = Mock(return_value=SimpleNamespace(start_mowing=start))
    client._async_refresh_authoritative_snapshot = AsyncMock()

    with pytest.raises(
        DreameLawnMowerCommandRejectedError,
        match="mower is busy",
    ):
        asyncio.run(client._async_call_device_method("start_mowing"))

    start.assert_called_once_with()
    client._async_refresh_authoritative_snapshot.assert_not_awaited()


def test_explicit_zone_rejection_is_not_reconciled_after_preflight() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client._sync_start_zone_mowing = Mock(
        side_effect=DreameLawnMowerCommandRejectedError("mower is busy")
    )
    client._async_refresh_authoritative_snapshot = AsyncMock(
        return_value=SimpleNamespace(task_status="idle")
    )

    with pytest.raises(
        DreameLawnMowerCommandRejectedError,
        match="mower is busy",
    ):
        asyncio.run(client.async_start_zone_mowing([1]))

    client._sync_start_zone_mowing.assert_called_once_with([1])
    client._async_refresh_authoritative_snapshot.assert_awaited_once_with()


def test_authoritative_confirmation_forces_device_property_request() -> None:
    client = object.__new__(DreameLawnMowerClient)
    device = SimpleNamespace(update=Mock())
    snapshot = SimpleNamespace(state="paused")
    client._ensure_device = Mock(return_value=device)
    client._snapshot_from_device = Mock(return_value=snapshot)

    result = asyncio.run(client._async_refresh_authoritative_snapshot())

    assert result is snapshot
    device.update.assert_called_once_with(force_request_properties=True)
    client._snapshot_from_device.assert_called_once_with(device)


def test_authoritative_confirmation_forwards_shared_deadline() -> None:
    client = object.__new__(DreameLawnMowerClient)
    device = SimpleNamespace(update=Mock())
    snapshot = SimpleNamespace(state="paused")
    client._ensure_device = Mock(return_value=device)
    client._snapshot_from_device = Mock(return_value=snapshot)

    result = asyncio.run(
        client._async_refresh_authoritative_snapshot(deadline=123.0)
    )

    assert result is snapshot
    device.update.assert_called_once_with(
        force_request_properties=True,
        deadline=123.0,
    )
    client._snapshot_from_device.assert_called_once_with(device)


@pytest.mark.parametrize(
    (
        "method_name",
        "sync_name",
        "arguments",
        "task_status",
        "task_operation",
        "task_region_ids",
        "task_area_ids",
    ),
    [
        (
            "async_start_zone_mowing",
            "_sync_start_zone_mowing",
            ([1, 2],),
            "zone_cleaning",
            102,
            (1, 2),
            None,
        ),
        (
            "async_start_edge_mowing",
            "_sync_start_edge_mowing",
            ([[3, 0]],),
            "segment_cleaning",
            101,
            None,
            None,
        ),
        (
            "async_start_spot_mowing",
            "_sync_start_spot_mowing",
            ([4],),
            "spot_cleaning",
            103,
            None,
            (4,),
        ),
    ],
)
def test_targeted_task_preflight_rejects_same_active_task_before_dispatch(
    method_name: str,
    sync_name: str,
    arguments: tuple[object, ...],
    task_status: str,
    task_operation: int,
    task_region_ids: tuple[int, ...] | None,
    task_area_ids: tuple[int, ...] | None,
) -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="mowing",
        task_status=task_status,
        task_operation=task_operation,
        task_region_ids=task_region_ids,
        task_area_ids=task_area_ids,
        current_zone_id=1,
        active_segment_count=1,
        mowing_session_active=True,
        started=True,
        mowing=True,
    )
    setattr(
        client,
        sync_name,
        Mock(return_value={"r": 0}),
    )
    client._async_refresh_authoritative_snapshot = AsyncMock(
        return_value=baseline
    )

    with pytest.raises(
        DreameLawnMowerCommandRejectedError,
        match="already executing",
    ):
        asyncio.run(getattr(client, method_name)(*arguments))

    getattr(client, sync_name).assert_not_called()
    client._async_refresh_authoritative_snapshot.assert_awaited_once_with()


def test_lost_zone_acknowledgement_accepts_requested_task_transition() -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="mowing",
        task_status="auto_cleaning",
        task_operation=1,
        current_zone_id=None,
        active_segment_count=0,
        mowing_session_active=True,
    )
    client._sync_start_zone_mowing = Mock(
        side_effect=DreameLawnMowerConnectionError("reply lost")
    )
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[
            baseline,
            SimpleNamespace(
                state="mowing",
                task_status="zone_cleaning",
                task_operation=102,
                task_region_ids=(2,),
                current_zone_id=2,
                active_segment_count=1,
                mowing_session_active=True,
                started=True,
                mowing=True,
            ),
        ]
    )

    with patch(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
        "client.asyncio.sleep",
        AsyncMock(),
    ):
        asyncio.run(client.async_start_zone_mowing([2]))

    client._sync_start_zone_mowing.assert_called_once_with([2])
    assert client._async_refresh_authoritative_snapshot.await_count == 2
    assert (
        client._async_refresh_authoritative_snapshot.await_args_list[1].kwargs[
            "deadline"
        ]
        > 0
    )


@pytest.mark.parametrize(
    ("method_name", "sync_name", "arguments"),
    [
        ("async_start_zone_mowing", "_sync_start_zone_mowing", ([2],)),
        ("async_start_edge_mowing", "_sync_start_edge_mowing", ([[3, 0]],)),
        ("async_start_spot_mowing", "_sync_start_spot_mowing", ([4],)),
    ],
)
def test_lost_targeted_acknowledgement_uses_bounded_confirmation_owner(
    method_name: str,
    sync_name: str,
    arguments: tuple[object, ...],
) -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="charging",
        task_status="idle",
        task_operation=None,
        task_region_ids=None,
        task_area_ids=None,
        current_zone_id=None,
        active_segment_count=0,
        mowing_session_active=False,
    )
    connection_error = DreameLawnMowerConnectionError("reply lost")
    setattr(client, sync_name, Mock(side_effect=connection_error))
    client._async_refresh_authoritative_snapshot = AsyncMock(
        return_value=baseline
    )
    client._async_require_targeted_task_confirmation = AsyncMock()

    result = asyncio.run(getattr(client, method_name)(*arguments))

    assert result is None
    getattr(client, sync_name).assert_called_once()
    client._async_require_targeted_task_confirmation.assert_awaited_once()
    assert (
        client._async_require_targeted_task_confirmation.await_args.kwargs[
            "original_error"
        ]
        is connection_error
    )


@pytest.mark.parametrize(
    ("method_name", "sync_name", "arguments"),
    [
        ("async_start_zone_mowing", "_sync_start_zone_mowing", ([2],)),
        ("async_start_edge_mowing", "_sync_start_edge_mowing", ([[3, 0]],)),
        ("async_start_spot_mowing", "_sync_start_spot_mowing", ([4],)),
    ],
)
def test_acknowledged_targeted_task_rejects_wrong_mowing_mode(
    method_name: str,
    sync_name: str,
    arguments: tuple[object, ...],
) -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="charging",
        task_status="idle",
        task_operation=None,
        current_zone_id=None,
        active_segment_count=0,
        mowing_session_active=False,
        started=False,
        mowing=False,
    )
    setattr(client, sync_name, Mock(return_value={"r": 0}))
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[
            baseline,
            *[
                SimpleNamespace(
                    state="mowing",
                    task_status="auto_cleaning",
                    task_operation=1,
                    current_zone_id=None,
                    active_segment_count=0,
                    mowing_session_active=True,
                    started=True,
                    mowing=True,
                )
                for _ in range(5)
            ],
        ]
    )

    with (
        patch(
            "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
            "client.asyncio.sleep",
            AsyncMock(),
        ),
        pytest.raises(
            DreameLawnMowerCommandRejectedError,
            match="acknowledged.*did not enter the requested task",
        ),
    ):
        asyncio.run(getattr(client, method_name)(*arguments))

    getattr(client, sync_name).assert_called_once()
    assert client._async_refresh_authoritative_snapshot.await_count == 6


def test_acknowledged_zone_task_accepts_generic_heartbeat_while_transiting() -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="charging",
        task_status="idle",
        task_operation=None,
        current_zone_id=None,
        active_segment_count=0,
        mowing_session_active=False,
    )
    response = {"r": 0, "d": {"r": 0}}
    client._sync_start_zone_mowing = Mock(return_value=response)
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[
            baseline,
            SimpleNamespace(
                state="mowing",
                task_status="mowing",
                task_operation=102,
                task_region_ids=(2,),
                current_zone_id=1,
                active_segment_count=1,
                mowing_session_active=True,
                started=True,
                mowing=True,
            ),
        ]
    )

    with patch(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
        "client.asyncio.sleep",
        AsyncMock(),
    ):
        result = asyncio.run(client.async_start_zone_mowing([2]))

    assert result is response
    client._sync_start_zone_mowing.assert_called_once_with([2])
    assert client._async_refresh_authoritative_snapshot.await_count == 2


def test_acknowledged_zone_task_accepts_late_realtime_heartbeat() -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="charging",
        task_status="idle",
        task_operation=None,
        task_region_ids=None,
        current_zone_id=None,
        active_segment_count=0,
        mowing_session_active=False,
    )
    pending = SimpleNamespace(
        state="mowing",
        task_status="starting",
        task_operation=None,
        task_region_ids=None,
        current_zone_id=None,
        active_segment_count=0,
        mowing_session_active=True,
        started=True,
        mowing=True,
    )
    confirmed = SimpleNamespace(
        state="mowing",
        task_status="zone_cleaning",
        task_operation=102,
        task_region_ids=(2,),
        current_zone_id=2,
        active_segment_count=1,
        mowing_session_active=True,
        started=True,
        mowing=True,
    )
    response = {"r": 0, "d": {"r": 0}}
    client._sync_start_zone_mowing = Mock(return_value=response)
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[baseline, pending, pending, pending, confirmed]
    )

    with patch(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
        "client.asyncio.sleep",
        AsyncMock(),
    ):
        result = asyncio.run(client.async_start_zone_mowing([2]))

    assert result is response
    client._sync_start_zone_mowing.assert_called_once_with([2])
    assert client._async_refresh_authoritative_snapshot.await_count == 5


def test_acknowledged_zone_task_confirmation_has_shared_deadline() -> None:
    async def run() -> None:
        client = object.__new__(DreameLawnMowerClient)
        baseline = SimpleNamespace(
            state="charging",
            task_status="idle",
            task_operation=None,
            task_region_ids=None,
            current_zone_id=None,
            active_segment_count=0,
            mowing_session_active=False,
        )
        worker_finished = asyncio.Event()
        refresh_count = 0

        async def refresh(*, deadline: float | None = None) -> SimpleNamespace:
            nonlocal refresh_count
            refresh_count += 1
            if refresh_count == 1:
                assert deadline is None
                return baseline
            assert deadline is not None
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0)
            worker_finished.set()
            raise DreameLawnMowerConnectionError("readback deadline expired")

        client._sync_start_zone_mowing = Mock(return_value={"r": 0})
        client._async_refresh_authoritative_snapshot = AsyncMock(side_effect=refresh)
        client._async_cached_authoritative_snapshot = AsyncMock(
            return_value=baseline
        )

        with (
            patch(
                "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
                "client._TARGETED_TASK_CONFIRMATION_OFFSETS_SECONDS",
                (0.0,),
            ),
            patch(
                "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
                "client._TARGETED_TASK_CONFIRMATION_TIMEOUT_SECONDS",
                0.01,
            ),
            pytest.raises(
                DreameLawnMowerConnectionError,
                match="every state readback failed",
            ),
        ):
            await asyncio.wait_for(
                client.async_start_zone_mowing([2]),
                timeout=0.25,
            )

        client._sync_start_zone_mowing.assert_called_once_with([2])
        assert client._async_refresh_authoritative_snapshot.await_count == 2
        assert worker_finished.is_set()
        client._async_cached_authoritative_snapshot.assert_awaited_once_with()

    asyncio.run(run())


def test_acknowledged_zone_task_accepts_cached_heartbeat_after_failed_read() -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="charging",
        task_status="idle",
        task_operation=None,
        task_region_ids=None,
        current_zone_id=None,
        active_segment_count=0,
        mowing_session_active=False,
    )
    confirmed = SimpleNamespace(
        state="mowing",
        task_status="zone_cleaning",
        task_operation=102,
        task_region_ids=(2,),
        current_zone_id=2,
        active_segment_count=1,
        mowing_session_active=True,
        started=True,
        mowing=True,
    )
    response = {"r": 0, "d": {"r": 0}}
    client._sync_start_zone_mowing = Mock(return_value=response)
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[baseline, DreameLawnMowerConnectionError("timed out")]
    )
    client._async_cached_authoritative_snapshot = AsyncMock(
        return_value=confirmed
    )

    with patch(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
        "client.asyncio.sleep",
        AsyncMock(),
    ):
        result = asyncio.run(client.async_start_zone_mowing([2]))

    assert result is response
    client._sync_start_zone_mowing.assert_called_once_with([2])
    assert client._async_refresh_authoritative_snapshot.await_count == 2
    client._async_cached_authoritative_snapshot.assert_awaited_once_with()


def test_zone_preflight_allows_different_region_target() -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="mowing",
        task_status="zone_cleaning",
        task_operation=102,
        task_region_ids=(1,),
        current_zone_id=1,
        active_segment_count=1,
        mowing_session_active=True,
        started=True,
        mowing=True,
    )
    confirmed_task = SimpleNamespace(
        state="mowing",
        task_status="zone_cleaning",
        task_operation=102,
        task_region_ids=(2,),
        current_zone_id=2,
        active_segment_count=1,
        mowing_session_active=True,
        started=True,
        mowing=True,
    )
    response = {"r": 0}
    client._sync_start_zone_mowing = Mock(return_value=response)
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[baseline, confirmed_task]
    )

    with patch(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
        "client.asyncio.sleep",
        AsyncMock(),
    ):
        result = asyncio.run(client.async_start_zone_mowing([2]))

    assert result is response
    client._sync_start_zone_mowing.assert_called_once_with([2])
    assert client._async_refresh_authoritative_snapshot.await_count == 2


def test_spot_preflight_allows_different_area_target() -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="mowing",
        activity="mowing",
        task_status="spot_cleaning",
        task_operation=103,
        task_area_ids=(4,),
        mowing_session_active=True,
        started=True,
        mowing=True,
    )
    confirmed_task = SimpleNamespace(
        state="mowing",
        activity="mowing",
        task_status="spot_cleaning",
        task_operation=103,
        task_area_ids=(5,),
        mowing_session_active=True,
        started=True,
        mowing=True,
    )
    response = {"r": 0}
    client._sync_start_spot_mowing = Mock(return_value=response)
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[baseline, confirmed_task]
    )

    with patch(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
        "client.asyncio.sleep",
        AsyncMock(),
    ):
        result = asyncio.run(client.async_start_spot_mowing([5]))

    assert result is response
    client._sync_start_spot_mowing.assert_called_once_with([5])
    assert client._async_refresh_authoritative_snapshot.await_count == 2


def test_zone_preflight_ignores_stale_task_identity_after_session_ends() -> None:
    client = object.__new__(DreameLawnMowerClient)
    idle_baseline = SimpleNamespace(
        state="idle",
        activity="docked",
        task_status="idle",
        task_operation=102,
        task_region_ids=(2,),
        current_zone_id=2,
        active_segment_count=1,
        mowing_session_active=False,
        started=False,
        mowing=False,
        paused=False,
    )
    confirmed_task = SimpleNamespace(
        state="mowing",
        activity="mowing",
        task_status="zone_cleaning",
        task_operation=102,
        task_region_ids=(2,),
        current_zone_id=2,
        active_segment_count=1,
        mowing_session_active=True,
        started=True,
        mowing=True,
        paused=False,
    )
    response = {"r": 0}
    client._sync_start_zone_mowing = Mock(return_value=response)
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[idle_baseline, confirmed_task]
    )

    with patch(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
        "client.asyncio.sleep",
        AsyncMock(),
    ):
        result = asyncio.run(client.async_start_zone_mowing([2]))

    assert result is response
    client._sync_start_zone_mowing.assert_called_once_with([2])
    assert client._async_refresh_authoritative_snapshot.await_count == 2


def test_acknowledged_zone_task_fails_closed_when_readback_is_unavailable() -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="charging",
        task_status="idle",
        task_operation=None,
        current_zone_id=None,
        active_segment_count=0,
        mowing_session_active=False,
    )
    client._sync_start_zone_mowing = Mock(return_value={"r": 0})
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[
            baseline,
            *[DreameLawnMowerConnectionError("offline") for _ in range(5)],
        ]
    )
    client._async_cached_authoritative_snapshot = AsyncMock(
        return_value=baseline
    )

    with (
        patch(
            "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
            "client.asyncio.sleep",
            AsyncMock(),
        ),
        pytest.raises(
            DreameLawnMowerConnectionError,
            match="every state readback failed",
        ),
    ):
        asyncio.run(client.async_start_zone_mowing([2]))

    client._sync_start_zone_mowing.assert_called_once_with([2])
    assert client._async_refresh_authoritative_snapshot.await_count == 6
    assert client._async_cached_authoritative_snapshot.await_count == 5


def test_targeted_task_does_not_dispatch_without_authoritative_preflight() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client._sync_start_zone_mowing = Mock(return_value={"r": 0})
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=DreameLawnMowerConnectionError("offline")
    )

    with pytest.raises(DreameLawnMowerConnectionError, match="offline"):
        asyncio.run(client.async_start_zone_mowing([2]))

    client._sync_start_zone_mowing.assert_not_called()
    client._async_refresh_authoritative_snapshot.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("method_name", "sync_name", "arguments", "task_status", "wrong_operation"),
    [
        (
            "async_start_zone_mowing",
            "_sync_start_zone_mowing",
            ([2],),
            "segment_cleaning",
            101,
        ),
        (
            "async_start_edge_mowing",
            "_sync_start_edge_mowing",
            ([[3, 0]],),
            "zone_cleaning",
            102,
        ),
    ],
)
def test_acknowledged_targeted_task_rejects_cross_target_operation(
    method_name: str,
    sync_name: str,
    arguments: tuple[object, ...],
    task_status: str,
    wrong_operation: int,
) -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="charging",
        task_status="idle",
        task_operation=None,
        current_zone_id=None,
        active_segment_count=0,
        mowing_session_active=False,
    )
    wrong_task = SimpleNamespace(
        state="mowing",
        task_status=task_status,
        task_operation=wrong_operation,
        current_zone_id=2,
        active_segment_count=1,
        mowing_session_active=True,
        started=True,
        mowing=True,
    )
    setattr(client, sync_name, Mock(return_value={"r": 0}))
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[baseline, *[wrong_task for _ in range(5)]]
    )

    with (
        patch(
            "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
            "client.asyncio.sleep",
            AsyncMock(),
        ),
        pytest.raises(DreameLawnMowerCommandRejectedError),
    ):
        asyncio.run(getattr(client, method_name)(*arguments))

    getattr(client, sync_name).assert_called_once()
    assert client._async_refresh_authoritative_snapshot.await_count == 6


@pytest.mark.parametrize(
    ("method_name", "sync_name", "arguments", "task_status", "task_operation"),
    [
        (
            "async_start_zone_mowing",
            "_sync_start_zone_mowing",
            ([2],),
            "segment_cleaning",
            102,
        ),
        (
            "async_start_edge_mowing",
            "_sync_start_edge_mowing",
            ([[3, 0]],),
            "zone_cleaning",
            101,
        ),
    ],
)
def test_acknowledged_targeted_task_accepts_status_alias_with_matching_operation(
    method_name: str,
    sync_name: str,
    arguments: tuple[object, ...],
    task_status: str,
    task_operation: int,
) -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="charging",
        task_status="idle",
        task_operation=None,
        current_zone_id=None,
        active_segment_count=0,
        mowing_session_active=False,
    )
    confirmed_task = SimpleNamespace(
        state="mowing",
        task_status=task_status,
        task_operation=task_operation,
        task_region_ids=(2,),
        current_zone_id=2,
        active_segment_count=1,
        mowing_session_active=True,
        started=True,
        mowing=True,
    )
    response = {"r": 0}
    setattr(client, sync_name, Mock(return_value=response))
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[baseline, confirmed_task]
    )

    with patch(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
        "client.asyncio.sleep",
        AsyncMock(),
    ):
        result = asyncio.run(getattr(client, method_name)(*arguments))

    assert result is response
    getattr(client, sync_name).assert_called_once()
    assert client._async_refresh_authoritative_snapshot.await_count == 2


@pytest.mark.parametrize(
    (
        "method_name",
        "sync_name",
        "arguments",
        "task_status",
        "task_operation",
        "task_region_ids",
        "task_area_ids",
    ),
    [
        (
            "async_start_zone_mowing",
            "_sync_start_zone_mowing",
            ([2],),
            "zone_cleaning_paused",
            102,
            (2,),
            None,
        ),
        (
            "async_start_edge_mowing",
            "_sync_start_edge_mowing",
            ([[3, 0]],),
            "segment_cleaning_paused",
            101,
            None,
            None,
        ),
        (
            "async_start_spot_mowing",
            "_sync_start_spot_mowing",
            ([4],),
            "spot_cleaning_paused",
            103,
            None,
            (4,),
        ),
    ],
)
def test_acknowledged_targeted_task_accepts_immediate_paused_state(
    method_name: str,
    sync_name: str,
    arguments: tuple[object, ...],
    task_status: str,
    task_operation: int,
    task_region_ids: tuple[int, ...] | None,
    task_area_ids: tuple[int, ...] | None,
) -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="charging",
        activity="docked",
        task_status="idle",
        task_operation=None,
        mowing_session_active=False,
        started=False,
        mowing=False,
        paused=False,
    )
    paused_task = SimpleNamespace(
        state="paused",
        activity="paused",
        task_status=task_status,
        task_operation=task_operation,
        task_region_ids=task_region_ids,
        task_area_ids=task_area_ids,
        mowing_session_active=None,
        started=False,
        mowing=False,
        paused=True,
    )
    response = {"r": 0}
    setattr(client, sync_name, Mock(return_value=response))
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[baseline, paused_task]
    )

    with patch(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
        "client.asyncio.sleep",
        AsyncMock(),
    ):
        result = asyncio.run(getattr(client, method_name)(*arguments))

    assert result is response
    getattr(client, sync_name).assert_called_once()
    assert client._async_refresh_authoritative_snapshot.await_count == 2


def test_acknowledged_zone_task_rejects_different_region_ids() -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="charging",
        task_status="idle",
        task_operation=None,
        task_region_ids=None,
        current_zone_id=None,
        active_segment_count=0,
        mowing_session_active=False,
    )
    wrong_zone_task = SimpleNamespace(
        state="mowing",
        task_status="zone_cleaning",
        task_operation=102,
        task_region_ids=(3,),
        current_zone_id=3,
        active_segment_count=1,
        mowing_session_active=True,
        started=True,
        mowing=True,
    )
    client._sync_start_zone_mowing = Mock(return_value={"r": 0})
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[baseline, *[wrong_zone_task for _ in range(5)]]
    )

    with (
        patch(
            "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
            "client.asyncio.sleep",
            AsyncMock(),
        ),
        pytest.raises(DreameLawnMowerCommandRejectedError),
    ):
        asyncio.run(client.async_start_zone_mowing([2]))

    client._sync_start_zone_mowing.assert_called_once_with([2])
    assert client._async_refresh_authoritative_snapshot.await_count == 6


def test_acknowledged_spot_task_rejects_different_area_ids() -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="charging",
        activity="docked",
        task_status="idle",
        task_operation=None,
        task_area_ids=None,
        mowing_session_active=False,
    )
    wrong_spot_task = SimpleNamespace(
        state="mowing",
        activity="mowing",
        task_status="spot_cleaning",
        task_operation=103,
        task_area_ids=(5,),
        mowing_session_active=True,
        started=True,
        mowing=True,
    )
    client._sync_start_spot_mowing = Mock(return_value={"r": 0})
    client._async_refresh_authoritative_snapshot = AsyncMock(
        side_effect=[baseline, *[wrong_spot_task for _ in range(5)]]
    )

    with (
        patch(
            "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
            "client.asyncio.sleep",
            AsyncMock(),
        ),
        pytest.raises(DreameLawnMowerCommandRejectedError),
    ):
        asyncio.run(client.async_start_spot_mowing([4]))

    client._sync_start_spot_mowing.assert_called_once_with([4])
    assert client._async_refresh_authoritative_snapshot.await_count == 6


def test_normal_dock_uses_heartbeat_session_state_at_base() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client.async_refresh = AsyncMock(
        return_value=SimpleNamespace(
            state="charging_completed",
            task_status="paused",
            mowing_session_active=True,
        )
    )
    client._async_call_device_method = AsyncMock()
    orchestrator = AsyncMock()

    with patch(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client.client."
        "async_stop_then_dock",
        orchestrator,
    ):
        asyncio.run(client.async_dock())

    assert orchestrator.await_args.kwargs["initial_state"] == "paused"
    refresh_state = orchestrator.await_args.kwargs["refresh_state"]
    assert asyncio.run(refresh_state()) == "paused"
    assert client.async_refresh.await_count == 2


def test_normal_dock_falls_back_when_preflight_refresh_fails() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client.async_refresh = AsyncMock(
        side_effect=DreameLawnMowerConnectionError("status unavailable")
    )
    client._async_call_device_method = AsyncMock()

    asyncio.run(client.async_dock())

    client.async_refresh.assert_awaited_once()
    client._async_call_device_method.assert_awaited_once_with("dock")
