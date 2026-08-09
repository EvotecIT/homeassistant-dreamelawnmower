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
        return_value=SimpleNamespace(task_resumable=False)
    )
    client._sync_resume_mowing = Mock()
    client._async_call_device_method = AsyncMock()

    started_new_session = asyncio.run(client.async_start_mowing())

    client.async_get_status_blob.assert_awaited_once_with(
        refresh=True,
        include_cloud=True,
    )
    client._sync_resume_mowing.assert_not_called()
    client._async_call_device_method.assert_awaited_once_with("start_mowing")
    assert started_new_session is True


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


def test_explicit_zone_rejection_is_not_reconciled_from_old_state() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client.async_get_cached_snapshot = AsyncMock(
        return_value=SimpleNamespace(task_status="idle")
    )
    client._sync_start_zone_mowing = Mock(
        side_effect=DreameLawnMowerCommandRejectedError("mower is busy")
    )
    client._async_refresh_authoritative_snapshot = AsyncMock()

    with pytest.raises(
        DreameLawnMowerCommandRejectedError,
        match="mower is busy",
    ):
        asyncio.run(client.async_start_zone_mowing([1]))

    client._sync_start_zone_mowing.assert_called_once_with([1])
    client._async_refresh_authoritative_snapshot.assert_not_awaited()


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


@pytest.mark.parametrize(
    ("method_name", "sync_name", "arguments", "task_status"),
    [
        ("async_start_zone_mowing", "_sync_start_zone_mowing", ([2],), "zone_cleaning"),
        (
            "async_start_edge_mowing",
            "_sync_start_edge_mowing",
            ([[3, 0]],),
            "segment_cleaning",
        ),
        ("async_start_spot_mowing", "_sync_start_spot_mowing", ([4],), "spot_cleaning"),
    ],
)
def test_lost_targeted_task_acknowledgement_rejects_unchanged_active_task(
    method_name: str,
    sync_name: str,
    arguments: tuple[object, ...],
    task_status: str,
) -> None:
    client = object.__new__(DreameLawnMowerClient)
    baseline = SimpleNamespace(
        state="mowing",
        task_status=task_status,
        current_zone_id=1,
        active_segment_count=1,
        mowing_session_active=True,
        started=True,
        mowing=True,
    )
    client.async_get_cached_snapshot = AsyncMock(return_value=baseline)
    setattr(
        client,
        sync_name,
        Mock(side_effect=DreameLawnMowerConnectionError("reply lost")),
    )
    client._async_refresh_authoritative_snapshot = AsyncMock(
        return_value=baseline
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
        asyncio.run(getattr(client, method_name)(*arguments))

    getattr(client, sync_name).assert_called_once()
    assert client._async_refresh_authoritative_snapshot.await_count == 3


def test_lost_zone_acknowledgement_accepts_requested_task_transition() -> None:
    client = object.__new__(DreameLawnMowerClient)
    client.async_get_cached_snapshot = AsyncMock(
        return_value=SimpleNamespace(
            state="mowing",
            task_status="auto_cleaning",
            current_zone_id=None,
            active_segment_count=0,
            mowing_session_active=True,
        )
    )
    client._sync_start_zone_mowing = Mock(
        side_effect=DreameLawnMowerConnectionError("reply lost")
    )
    client._async_refresh_authoritative_snapshot = AsyncMock(
        return_value=SimpleNamespace(
            state="mowing",
            task_status="zone_cleaning",
            current_zone_id=2,
            active_segment_count=1,
            mowing_session_active=True,
            started=True,
            mowing=True,
        )
    )

    with patch(
        "custom_components.dreame_lawn_mower.dreame_lawn_mower_client."
        "client_core.asyncio.sleep",
        AsyncMock(),
    ):
        asyncio.run(client.async_start_zone_mowing([2]))

    client._sync_start_zone_mowing.assert_called_once_with([2])
    client._async_refresh_authoritative_snapshot.assert_awaited_once_with()


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
