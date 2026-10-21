"""A2 docked task state remains stable between five-minute standby messages."""

import asyncio
from dataclasses import replace
from threading import RLock
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
client_core = load_internal_module("client_core")
DreameLawnMowerClient = load_internal_module("client").DreameLawnMowerClient
device_plan_cleanup = load_internal_module("device_plan_cleanup")

# The 20-byte idle frame in issue #294 contains no garden or account data.
_A2_STANDBY_FRAME = (
    206, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 100, 113, 255, 0, 0, 128, 200, 186, 206,
)


def _snapshot(model="dreame.mower.g2408"):
    return DreameLawnMowerSnapshot(
        descriptor=DreameLawnMowerDescriptor(
            did="test-a2", name="Mower", model=model, display_model="A2",
            account_type="dreame", country="eu",
        ),
        available=True, state="charging_completed", state_name="charging_completed",
        activity="docked", task_status="unknown", task_resumable=None,
        raw_started=True, raw_docked=False, docked=True,
    )


def _standby_blob():
    blob = decode_mower_status_blob(
        _A2_STANDBY_FRAME, source="realtime",
        property_key=MOWER_RAW_STATUS_PROPERTY_KEY,
    )
    assert blob is not None
    assert blob.task_status == "idle"
    assert blob.heartbeat_docked is True
    return replace(blob, received_at="1970-01-01T00:00:20+00:00")


@pytest.mark.parametrize("model", ["dreame.mower.g2408", "g2408"])
def test_a2_docked_state_covers_the_standby_cycle_and_expires(model):
    """An unchanged standby frame stays valid, without renewing its deadline."""
    snapshot = _snapshot(model)
    blob = _standby_blob()
    for age in (0.0, 120.0, 131.0, 299.0, 300.0, 365.0):
        result = snapshot_with_heartbeat_task_state(
            snapshot, blob, observed_at=20.0 + age,
        )
        assert result.task_status == "idle", age
        assert result.mowing_session_active is False, age
        assert result.task_resumable is False, age
        assert result.raw_docked is True, age
        assert result.activity == "docked", age
        assert result.state == "charging_completed", age
        assert result.task_status_event_at == 20.0, age

    assert snapshot_with_heartbeat_task_state(
        snapshot, blob, observed_at=385.000001,
    ) is snapshot


@pytest.mark.parametrize(
    "newer_evidence",
    ["state_event_at", "task_status_event_at", "active_state_observed_at"],
)
def test_a2_standby_cannot_erase_newer_paused_task(newer_evidence):
    """The longer idle window must not clear a newer resumable task."""
    snapshot = replace(
        _snapshot(), state="paused", state_name="paused", activity="paused",
        paused=True, task_status="paused", mowing_session_active=True,
        task_resumable=True, state_event_at=19.0, task_status_event_at=19.0,
    )
    observed = 19.0
    if newer_evidence == "active_state_observed_at":
        observed = 21.0
    else:
        snapshot = replace(snapshot, **{newer_evidence: 21.0})
    result = snapshot_with_heartbeat_task_state(
        snapshot, _standby_blob(), observed_at=320.0,
        active_state_observed_at=observed,
    )
    assert result is snapshot
    assert result.task_status == "paused"
    assert result.task_resumable is True


def test_a2_frame_does_not_extend_other_models_standby_window():
    """The A2 3000 retains its current limit without cadence evidence."""
    snapshot = _snapshot("dreame.mower.g2568d")
    assert snapshot_with_heartbeat_task_state(
        snapshot, _standby_blob(), observed_at=151.0,
    ) is snapshot


@pytest.mark.parametrize("read_path", ["cached", "refresh"])
def test_a2_reads_keep_idle_task_and_docking_between_standby_messages(read_path):
    """Both snapshot readers expose stable idle/resumability and raw docking."""
    raw_snapshot = _snapshot()
    device = SimpleNamespace(
        _state_lock=RLock(),
        _plan_cleanup=device_plan_cleanup._DevicePlanCleanup(),
        info=SimpleNamespace(raw={}, model=raw_snapshot.descriptor.model),
        name="Mower", host=None, mac=None, token=None,
        realtime_properties={MOWER_RAW_STATUS_PROPERTY_KEY: {
            "value": list(_A2_STANDBY_FRAME), "last_seen": 20.0,
        }},
    )
    client = object.__new__(DreameLawnMowerClient)
    client._runtime_geometry_lock = RLock()
    client._closing = False
    client._async_cloud_read = lambda read: read(None)
    client._device = device
    client._ensure_device = lambda **kwargs: device
    client._descriptor = raw_snapshot.descriptor
    client._latest_snapshot = None
    client._async_update_device = AsyncMock(return_value=device)
    client.async_get_status_blob = AsyncMock(return_value=_standby_blob())
    client._async_get_cached_cloud_device_info = AsyncMock(return_value=None)

    with (
        patch.object(client_core, "snapshot_from_device", return_value=raw_snapshot),
        patch.object(client_core.time, "time", return_value=320.0),
    ):
        result = asyncio.run(
            client.async_refresh() if read_path == "refresh"
            else client.async_get_cached_snapshot()
        )

    assert result.task_status == "idle"
    assert result.task_resumable is False
    assert result.mowing_session_active is False
    assert result.docked is True
    assert result.raw_docked is True
