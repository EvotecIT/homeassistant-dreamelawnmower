"""Regression checks for heartbeat/live snapshot reconciliation."""

from __future__ import annotations

from dataclasses import replace

from dreame_lawn_mower_client import (
    DreameLawnMowerDescriptor,
    DreameLawnMowerSnapshot,
    decode_mower_status_blob,
)
from dreame_lawn_mower_client._loader import load_internal_module

snapshot_with_heartbeat_task_state = load_internal_module(
    "runtime_state"
).snapshot_with_heartbeat_task_state


def _snapshot() -> DreameLawnMowerSnapshot:
    return DreameLawnMowerSnapshot(
        descriptor=DreameLawnMowerDescriptor(
            did="device-127",
            name="Garden Mower",
            model="dreame.mower.g2568d",
            display_model="A2 3000",
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


def test_idle_in_station_heartbeat_corrects_stale_paused_snapshot() -> None:
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
    )

    assert status_blob is not None
    reconciled = snapshot_with_heartbeat_task_state(_snapshot(), status_blob)

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


def test_idle_in_station_heartbeat_preserves_bare_active_error() -> None:
    status_blob = decode_mower_status_blob(
        [206, 0, 0, 0, 0, 0, 0, 0, 0, 0, 128, 87, 113, 255, 0, 0, 128, 206, 186, 206]
    )

    assert status_blob is not None
    error_snapshot = replace(_snapshot(), activity="error", error_code=None)
    reconciled = snapshot_with_heartbeat_task_state(error_snapshot, status_blob)

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
