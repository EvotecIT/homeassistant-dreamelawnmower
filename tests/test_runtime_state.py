"""Regression checks for heartbeat/live snapshot reconciliation."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from threading import RLock
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from dreame_lawn_mower_client import (
    MOWER_RAW_STATUS_PROPERTY_KEY,
    DreameLawnMowerDescriptor,
    DreameLawnMowerSnapshot,
    decode_mower_status_blob,
)
from dreame_lawn_mower_client._loader import load_internal_module

snapshot_with_heartbeat_task_state = load_internal_module(
    "runtime_state"
).snapshot_with_heartbeat_task_state
client_core_module = load_internal_module("client_core")
DreameLawnMowerClient = load_internal_module("client").DreameLawnMowerClient
DreameLawnMowerCommandRejectedError = load_internal_module(
    "exceptions"
).DreameLawnMowerCommandRejectedError

_A3_STANDBY_FRAME = (
    206,
    0,
    0,
    0,
    0,
    0,
    0,
    5,
    0,
    0,
    0,
    50,
    177,
    255,
    0,
    0,
    128,
    200,
    186,
    0,
    128,
    206,
)


def _snapshot(
    *,
    model: str = "dreame.mower.g2568d",
    display_model: str = "A2 3000",
) -> DreameLawnMowerSnapshot:
    return DreameLawnMowerSnapshot(
        descriptor=DreameLawnMowerDescriptor(
            did="device-127",
            name="Garden Mower",
            model=model,
            display_model=display_model,
            account_type="dreame",
            country="eu",
        ),
        available=True,
        state="paused",
        state_name="paused",
        activity="paused",
        battery_level=87,
        charging=False,
        raw_charging=False,
        started=False,
        raw_started=True,
        docked=False,
        raw_docked=False,
        paused=True,
    )


@pytest.mark.parametrize(
    ("model", "display_model"),
    [
        ("dreame.mower.g2408", "A2"),
        ("dreame.mower.g2568d", "A2 3000"),
    ],
)
def test_idle_in_station_heartbeat_corrects_stale_paused_snapshot(
    model: str,
    display_model: str,
) -> None:
    status_blob = decode_mower_status_blob(
        [
            206,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            128,
            87,
            113,
            255,
            0,
            0,
            128,
            206,
            186,
            206,
        ],
        source="realtime",
        property_key=MOWER_RAW_STATUS_PROPERTY_KEY,
    )

    assert status_blob is not None
    status_blob = replace(status_blob, received_at="1970-01-01T00:00:20+00:00")
    reconciled = snapshot_with_heartbeat_task_state(
        _snapshot(model=model, display_model=display_model),
        status_blob,
        observed_at=20.0,
        active_state_observed_at=19.0,
    )

    assert reconciled.state == "idle"
    assert reconciled.state_name == "idle"
    assert reconciled.activity == "docked"
    assert reconciled.docked is True
    assert reconciled.raw_docked is True
    assert reconciled.charging is False
    assert reconciled.raw_charging is False
    assert reconciled.paused is False
    assert reconciled.task_status == "idle"
    assert reconciled.task_status_source == "heartbeat_realtime"
    assert reconciled.mowing_session_active is False
    assert reconciled.mission_task_id == status_blob.candidate_runtime_task_id


def test_a3_standby_heartbeat_corrects_stale_paused_snapshot() -> None:
    status_blob = decode_mower_status_blob(
        [
            206,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            99,
            161,
            255,
            0,
            0,
            128,
            204,
            186,
            0,
            128,
            206,
        ],
        source="realtime",
        property_key=MOWER_RAW_STATUS_PROPERTY_KEY,
    )

    assert status_blob is not None
    status_blob = replace(status_blob, received_at="1970-01-01T00:00:20+00:00")
    reconciled = snapshot_with_heartbeat_task_state(
        _snapshot(
            model="dreame.mower.g2541e",
            display_model="A3 AWD Pro 3500",
        ),
        status_blob,
        observed_at=20.0,
        active_state_observed_at=19.0,
    )

    assert reconciled.state == "idle"
    assert reconciled.activity == "docked"
    assert reconciled.docked is True
    assert reconciled.raw_docked is True
    assert reconciled.started is False
    assert reconciled.task_status == "idle"
    assert reconciled.mowing_session_active is False
    assert reconciled.task_resumable is False


def test_a3_realtime_standby_is_shared_by_callback_and_map_guard() -> None:
    """MQTT publication and safety reads must share the reconciled state."""
    raw_snapshot = _snapshot(
        model="dreame.mower.g2541e",
        display_model="A3 AWD Pro 3500",
    )
    first_heartbeat_received_at = 100.0
    device = SimpleNamespace(
        _state_lock=RLock(),
        realtime_properties={
            MOWER_RAW_STATUS_PROPERTY_KEY: {
                "value": list(_A3_STANDBY_FRAME),
                "last_seen": first_heartbeat_received_at,
            }
        },
    )
    client = object.__new__(DreameLawnMowerClient)
    client._descriptor = raw_snapshot.descriptor
    client._latest_snapshot = None
    client._ensure_device = lambda: device
    client._sync_update_device = lambda force=False: device  # noqa: ARG005
    client._sync_switch_current_map = Mock(
        side_effect=lambda map_index: {"map_index": map_index}
    )
    client.async_get_current_app_map_index = AsyncMock(return_value=1)

    with (
        patch.object(
            client_core_module,
            "snapshot_from_device",
            return_value=raw_snapshot,
        ),
        patch.object(client_core_module.time, "time", return_value=101.0),
    ):
        first_snapshot = asyncio.run(client.async_get_cached_snapshot())
        with pytest.raises(
            DreameLawnMowerCommandRejectedError,
            match="Finish or cancel",
        ):
            asyncio.run(client.async_switch_current_map(1))

    assert first_snapshot.state == "paused"
    assert first_snapshot.activity == "paused"
    client._sync_switch_current_map.assert_not_called()

    heartbeat_received_at = 102.0
    device.realtime_properties[MOWER_RAW_STATUS_PROPERTY_KEY]["last_seen"] = (
        heartbeat_received_at
    )
    # The reporter's unchanged A3 standby frame arrived every five minutes.
    # At diagnostic capture it was 154 seconds old, beyond the generic
    # two-refresh freshness window but still the current supervised frame.
    with (
        patch.object(
            client_core_module,
            "snapshot_from_device",
            return_value=raw_snapshot,
        ),
        patch.object(client_core_module.time, "time", return_value=256.614),
    ):
        reconciled = asyncio.run(client.async_get_cached_snapshot())
        switch_result = asyncio.run(client.async_switch_current_map(1))

    assert reconciled.state == "idle"
    assert reconciled.activity == "docked"
    assert reconciled.docked is True
    assert reconciled.started is False
    assert reconciled.paused is False
    assert reconciled.task_status == "idle"
    assert reconciled.task_status_source == "heartbeat_realtime"
    assert reconciled.task_status_event_at == pytest.approx(
        heartbeat_received_at,
        abs=0.000001,
    )
    assert reconciled.mowing_session_active is False
    assert client._latest_snapshot.state == "idle"
    assert client._latest_snapshot.activity == "docked"
    assert switch_result == {"map_index": 1}
    client._sync_switch_current_map.assert_called_once_with(1)
    client.async_get_current_app_map_index.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("state", "activity", "paused", "mowing"),
    [
        ("paused", "paused", True, False),
        ("mowing", "mowing", False, True),
    ],
)
def test_fresh_inactive_heartbeat_cannot_weaken_new_untimestamped_active_state(
    state: str,
    activity: str,
    paused: bool,
    mowing: bool,
) -> None:
    """A first local active observation outranks an older cached heartbeat."""
    status_blob = decode_mower_status_blob(
        _A3_STANDBY_FRAME,
        source="realtime",
        property_key=MOWER_RAW_STATUS_PROPERTY_KEY,
    )

    assert status_blob is not None
    status_blob = replace(
        status_blob,
        received_at="1970-01-01T00:01:40+00:00",
    )
    active_snapshot = replace(
        _snapshot(),
        state=state,
        state_name=state,
        activity=activity,
        paused=paused,
        mowing=mowing,
        started=True,
        state_event_at=None,
        task_status_event_at=None,
    )

    reconciled = snapshot_with_heartbeat_task_state(
        active_snapshot,
        status_blob,
        observed_at=101.0,
        active_state_observed_at=101.0,
    )

    assert reconciled is active_snapshot
    assert reconciled.activity == activity
    assert reconciled.mowing_session_active is None


def test_refresh_keeps_new_untimestamped_active_observation() -> None:
    raw_snapshot = _snapshot(
        model="dreame.mower.g2541e",
        display_model="A3 AWD Pro 3500",
    )
    status_blob = decode_mower_status_blob(
        _A3_STANDBY_FRAME,
        source="realtime",
        property_key=MOWER_RAW_STATUS_PROPERTY_KEY,
    )
    assert status_blob is not None
    status_blob = replace(
        status_blob,
        received_at="1970-01-01T00:01:40+00:00",
    )
    device = SimpleNamespace(
        _state_lock=RLock(),
        info=SimpleNamespace(raw={}, model=raw_snapshot.descriptor.model),
        name=raw_snapshot.descriptor.name,
        host=None,
        mac=None,
        token=None,
        realtime_properties={
            MOWER_RAW_STATUS_PROPERTY_KEY: {
                "value": list(_A3_STANDBY_FRAME),
                "last_seen": 100.0,
            }
        },
    )
    client = object.__new__(DreameLawnMowerClient)
    client._descriptor = raw_snapshot.descriptor
    client._latest_snapshot = None
    client._sync_update_device = lambda force=False: device  # noqa: ARG005
    client._sync_get_status_blob = lambda include_cloud, refresh: status_blob  # noqa: ARG005
    client._sync_get_cached_cloud_device_info = lambda: None

    with (
        patch.object(
            client_core_module,
            "snapshot_from_device",
            return_value=raw_snapshot,
        ),
        patch.object(client_core_module.time, "time", return_value=101.0),
    ):
        refreshed = asyncio.run(client.async_refresh())

    assert refreshed.state == "paused"
    assert refreshed.activity == "paused"
    assert refreshed.mowing_session_active is None


@pytest.mark.parametrize(
    ("received_at", "state_event_at", "task_status_event_at", "observed_at"),
    [
        (None, None, None, 21.0),
        ("1970-01-01T00:00:20+00:00", 21.0, None, 21.0),
        ("1970-01-01T00:00:20+00:00", None, 21.0, 21.0),
        ("1970-01-01T00:00:20+00:00", None, None, 151.0),
        ("1970-01-01T00:00:30+00:00", None, None, 20.0),
    ],
    ids=("missing-time", "newer-state", "newer-task", "stale", "future"),
)
def test_unproven_inactive_heartbeat_does_not_clear_paused_state(
    received_at: str | None,
    state_event_at: float | None,
    task_status_event_at: float | None,
    observed_at: float,
) -> None:
    status_blob = decode_mower_status_blob(
        _A3_STANDBY_FRAME,
        source="realtime",
        property_key=MOWER_RAW_STATUS_PROPERTY_KEY,
    )

    assert status_blob is not None
    status_blob = replace(status_blob, received_at=received_at)
    paused_snapshot = replace(
        _snapshot(),
        state_event_at=state_event_at,
        task_status_event_at=task_status_event_at,
    )

    reconciled = snapshot_with_heartbeat_task_state(
        paused_snapshot,
        status_blob,
        observed_at=observed_at,
    )

    assert reconciled is paused_snapshot
    assert reconciled.state == "paused"
    assert reconciled.activity == "paused"
    assert reconciled.paused is True
    assert reconciled.mowing_session_active is None


def test_submicrosecond_same_message_heartbeat_remains_current() -> None:
    status_blob = decode_mower_status_blob(
        _A3_STANDBY_FRAME,
        source="realtime",
        property_key=MOWER_RAW_STATUS_PROPERTY_KEY,
    )

    assert status_blob is not None
    status_blob = replace(
        status_blob,
        received_at="2023-11-14T22:13:20.750000+00:00",
    )
    paused_snapshot = replace(
        _snapshot(),
        state_event_at=1_700_000_000.7500002,
        task_status_event_at=1_700_000_000.7500002,
    )

    reconciled = snapshot_with_heartbeat_task_state(
        paused_snapshot,
        status_blob,
        observed_at=1_700_000_001.0,
    )

    assert reconciled.state == "idle"
    assert reconciled.activity == "docked"
    assert reconciled.paused is False
    assert reconciled.mowing_session_active is False


def test_genuinely_newer_subsecond_state_still_blocks_inactive_heartbeat() -> None:
    status_blob = decode_mower_status_blob(
        _A3_STANDBY_FRAME,
        source="realtime",
        property_key=MOWER_RAW_STATUS_PROPERTY_KEY,
    )

    assert status_blob is not None
    status_blob = replace(
        status_blob,
        received_at="2023-11-14T22:13:20.750000+00:00",
    )
    paused_snapshot = replace(
        _snapshot(),
        state_event_at=1_700_000_000.750002,
    )

    reconciled = snapshot_with_heartbeat_task_state(
        paused_snapshot,
        status_blob,
        observed_at=1_700_000_001.0,
    )

    assert reconciled is paused_snapshot


def test_expired_a3_heartbeat_cannot_bypass_map_switch_guard() -> None:
    raw_snapshot = _snapshot(
        model="dreame.mower.g2541e",
        display_model="A3 AWD Pro 3500",
    )
    device = SimpleNamespace(
        _state_lock=RLock(),
        realtime_properties={
            MOWER_RAW_STATUS_PROPERTY_KEY: {
                "value": list(_A3_STANDBY_FRAME),
                "last_seen": time.time() - 366.0,
            }
        },
    )
    client = object.__new__(DreameLawnMowerClient)
    client._descriptor = raw_snapshot.descriptor
    client._latest_snapshot = None
    client._sync_update_device = lambda force=False: device  # noqa: ARG005
    client._sync_switch_current_map = Mock()

    with patch.object(
        client_core_module,
        "snapshot_from_device",
        return_value=raw_snapshot,
    ):
        with pytest.raises(
            DreameLawnMowerCommandRejectedError,
            match="Finish or cancel",
        ):
            asyncio.run(client.async_switch_current_map(1))

    client._sync_switch_current_map.assert_not_called()


@pytest.mark.parametrize("age_seconds", [154.614, 300.013, 365.0])
def test_a3_idle_heartbeat_covers_observed_cadence_and_boundary(
    age_seconds: float,
) -> None:
    """Supervised A3 standby evidence remains current through its full window."""
    status_blob = decode_mower_status_blob(
        _A3_STANDBY_FRAME,
        source="realtime",
        property_key=MOWER_RAW_STATUS_PROPERTY_KEY,
    )

    assert status_blob is not None
    status_blob = replace(status_blob, received_at="1970-01-01T00:00:20+00:00")

    reconciled = snapshot_with_heartbeat_task_state(
        _snapshot(),
        status_blob,
        observed_at=20.0 + age_seconds,
        active_state_observed_at=19.0,
    )

    assert reconciled.state == "idle"
    assert reconciled.activity == "docked"
    assert reconciled.mowing_session_active is False


def test_a3_idle_heartbeat_expires_immediately_above_boundary() -> None:
    status_blob = decode_mower_status_blob(
        _A3_STANDBY_FRAME,
        source="realtime",
        property_key=MOWER_RAW_STATUS_PROPERTY_KEY,
    )

    assert status_blob is not None
    status_blob = replace(status_blob, received_at="1970-01-01T00:00:20+00:00")
    paused_snapshot = _snapshot()

    reconciled = snapshot_with_heartbeat_task_state(
        paused_snapshot,
        status_blob,
        observed_at=385.000001,
        active_state_observed_at=19.0,
    )

    assert reconciled is paused_snapshot


def test_standard_inactive_heartbeat_keeps_short_freshness_window() -> None:
    """The A3 cadence allowance must not weaken generic heartbeat safety."""
    status_blob = decode_mower_status_blob(
        [
            206,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            128,
            87,
            113,
            255,
            0,
            0,
            128,
            206,
            186,
            206,
        ],
        source="realtime",
        property_key=MOWER_RAW_STATUS_PROPERTY_KEY,
    )

    assert status_blob is not None
    status_blob = replace(status_blob, received_at="1970-01-01T00:00:20+00:00")
    paused_snapshot = _snapshot()

    reconciled = snapshot_with_heartbeat_task_state(
        paused_snapshot,
        status_blob,
        observed_at=151.0,
        active_state_observed_at=19.0,
    )

    assert reconciled is paused_snapshot
    assert reconciled.state == "paused"
    assert reconciled.activity == "paused"


def test_active_paused_session_keeps_task_activity_while_recording_dock() -> None:
    status_blob = decode_mower_status_blob(
        [206, 0, 0, 0, 0, 0, 0, 0, 128, 0, 128, 100, 21, 36, 0, 0, 128, 211, 196, 206]
    )

    assert status_blob is not None
    reconciled = snapshot_with_heartbeat_task_state(_snapshot(), status_blob)

    assert reconciled.state == "paused"
    assert reconciled.activity == "paused"
    assert reconciled.docked is True
    assert reconciled.raw_docked is True
    assert reconciled.task_status == "paused"
    assert reconciled.mowing_session_active is True
    assert reconciled.mission_task_id == status_blob.candidate_runtime_task_id


def test_heartbeat_runtime_task_identity_is_retained_when_available() -> None:
    status_blob = SimpleNamespace(
        task_status="mowing",
        source="realtime",
        mowing_session_active=True,
        task_resumable=False,
        heartbeat_docked=False,
        candidate_runtime_task_id=101,
    )

    reconciled = snapshot_with_heartbeat_task_state(_snapshot(), status_blob)

    assert reconciled.mission_task_id == 101


def test_heartbeat_task_state_replaces_retained_property_timestamp() -> None:
    """Heartbeat state uses only its own reception time for mission identity."""
    property_snapshot = replace(
        _snapshot(),
        task_status="starting",
        task_status_source="property",
        task_status_event_at=10.0,
    )
    status_blob = SimpleNamespace(
        task_status="starting",
        source="realtime",
        received_at="1970-01-01T00:00:20+00:00",
        mowing_session_active=True,
        task_resumable=False,
        heartbeat_docked=False,
        candidate_runtime_task_id=None,
    )

    reconciled = snapshot_with_heartbeat_task_state(property_snapshot, status_blob)

    assert reconciled.task_status_source == "heartbeat_realtime"
    assert reconciled.task_status_event_at == 20.0


def test_heartbeat_task_state_clears_unowned_property_timestamp() -> None:
    """Missing heartbeat timing never borrows the retained property event."""
    property_snapshot = replace(
        _snapshot(),
        task_status="starting",
        task_status_source="property",
        task_status_event_at=10.0,
    )
    status_blob = SimpleNamespace(
        task_status="starting",
        source="realtime",
        received_at=None,
        mowing_session_active=True,
        task_resumable=False,
        heartbeat_docked=False,
        candidate_runtime_task_id=None,
    )

    reconciled = snapshot_with_heartbeat_task_state(property_snapshot, status_blob)

    assert reconciled.task_status_source == "heartbeat_realtime"
    assert reconciled.task_status_event_at is None


def test_idle_in_station_heartbeat_preserves_bare_active_error() -> None:
    status_blob = decode_mower_status_blob(
        [206, 0, 0, 0, 0, 0, 0, 0, 0, 0, 128, 87, 113, 255, 0, 0, 128, 206, 186, 206]
    )

    assert status_blob is not None
    status_blob = replace(status_blob, received_at="1970-01-01T00:00:20+00:00")
    error_snapshot = replace(_snapshot(), activity="error", error_code=None)
    reconciled = snapshot_with_heartbeat_task_state(
        error_snapshot,
        status_blob,
        observed_at=20.0,
        active_state_observed_at=19.0,
    )

    assert reconciled.state == "paused"
    assert reconciled.activity == "error"
    assert reconciled.docked is True
    assert reconciled.raw_docked is True


def test_idle_in_station_heartbeat_preserves_dock_lifecycle_state() -> None:
    status_blob = decode_mower_status_blob(
        [206, 0, 0, 0, 0, 0, 0, 0, 0, 0, 128, 87, 113, 255, 0, 0, 128, 206, 186, 206]
    )

    assert status_blob is not None
    charging_snapshot = replace(
        _snapshot(),
        state="charging",
        state_name="charging",
        activity="docked",
        charging=True,
        raw_charging=True,
        paused=False,
    )
    reconciled = snapshot_with_heartbeat_task_state(charging_snapshot, status_blob)

    assert reconciled.state == "charging"
    assert reconciled.state_name == "charging"
    assert reconciled.activity == "docked"
    assert reconciled.charging is True
