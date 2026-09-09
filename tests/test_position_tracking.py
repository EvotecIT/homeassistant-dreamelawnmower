"""Position provenance and docking require ordered, map-scoped evidence."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    position_tracking,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.app_protocol import (
    decode_mower_status_blob,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerStatusBlob,
)

NOW = datetime(2026, 9, 7, 8, tzinfo=UTC)
MowerPositionTracker = position_tracking.MowerPositionTracker
snapshot_is_docked = position_tracking.snapshot_is_docked


def state(*, docked=False, at=NOW):
    return SimpleNamespace(
        available=True,
        docked=docked,
        activity="docked" if docked else "mowing",
        state="charging_completed" if docked else "mowing",
        state_event_at=at.timestamp(),
    )


def pose(at=NOW, *, x=20):
    return DreameLawnMowerStatusBlob(
        supported=True,
        frame_valid=True,
        received_at=at.isoformat(),
        candidate_runtime_pose_x=x,
        candidate_runtime_pose_y=30,
        candidate_runtime_heading_deg=45,
    )


def resolve(tracker, *, snapshot=None, now=NOW, index=0, geometry="lawn-a"):
    return tracker.resolve(
        map_index=index,
        geometry=geometry,
        contains=lambda x, y: 0 <= x <= 100 and 0 <= y <= 100,
        snapshot=snapshot or state(),
        now=now,
    )


@pytest.mark.parametrize("docked", [False, True])
def test_foreign_map_projection_preserves_owned_position_evidence(docked):
    tracker = MowerPositionTracker()
    tracker.record(pose(), map_index=0, now=NOW)
    snapshot = state(docked=docked)
    original = resolve(tracker, snapshot=snapshot)
    assert original is not None
    checkpoint = tracker.checkpoint(now=NOW)
    assert resolve(tracker, index=1, geometry="foreign", snapshot=snapshot) is None
    assert tracker.checkpoint(now=NOW) == checkpoint
    assert resolve(tracker, snapshot=snapshot) == original


def test_new_verified_input_allows_actual_map_transition():
    tracker = MowerPositionTracker()
    tracker.record(pose(), map_index=0, now=NOW)
    assert resolve(tracker).map_index == 0
    later = NOW + timedelta(seconds=1)
    tracker.record(pose(at=later), map_index=1, now=later)
    assert resolve(tracker, index=1, geometry="new-map", now=later).map_index == 1


@pytest.mark.parametrize("charging_state", ["charging", "charging_completed"])
def test_charging_establishes_docking_but_not_map_coordinates(charging_state):
    snapshot = state(docked=True)
    snapshot.state = charging_state
    assert snapshot_is_docked(snapshot)
    blob = decode_mower_status_blob(
        list(bytes.fromhex("ce000000000000000000806401ff000000bc7fce"))
    )
    tracker = MowerPositionTracker()
    tracker.record(replace(blob, received_at=NOW.isoformat()), map_index=0, now=NOW)
    assert resolve(tracker, snapshot=snapshot) is None


def test_pose_from_before_docking_is_only_last_known_not_dock_evidence():
    tracker = MowerPositionTracker()
    tracker.record(pose(), map_index=0, now=NOW)
    assert resolve(tracker).status == "current"
    later = NOW + timedelta(seconds=60)
    result = resolve(tracker, snapshot=state(docked=True, at=later), now=later)
    assert result.status == "last_known"
    assert result.heading is None
    assert result.observed_at == NOW.timestamp()


def test_docked_heartbeat_cannot_borrow_a_returning_state_timestamp():
    """Arrival heartbeat can lead the charging property, as on the live A2."""
    tracker = MowerPositionTracker()
    tracker.record(pose(), map_index=0, now=NOW)
    assert resolve(tracker).status == "current"
    arrival = state(docked=True, at=NOW - timedelta(seconds=30))
    arrival.state = "returning"
    result = resolve(tracker, snapshot=arrival, now=NOW + timedelta(seconds=5))
    assert snapshot_is_docked(arrival)
    assert result.status == "last_known"
    assert result.observed_at == NOW.timestamp()


def test_fresh_pose_while_charging_learns_dock_and_survives_missing_packets():
    tracker = MowerPositionTracker()
    tracker.record(pose(), map_index=0, now=NOW)
    result = resolve(
        tracker, snapshot=state(docked=True, at=NOW - timedelta(seconds=5))
    )
    assert result.status == "known_dock"
    tracker.record(None, map_index=0, now=NOW)
    result = resolve(tracker, snapshot=state(docked=True), now=NOW + timedelta(hours=2))
    assert result.status == "known_dock"
    assert (result.x, result.y) == (20, 30)
    assert result.details()["position_source"] == "observed_docked_pose"


def test_stale_pose_can_only_be_retained_if_it_was_validated_while_fresh():
    tracker = MowerPositionTracker()
    tracker.record(pose(), map_index=0, now=NOW)
    assert resolve(tracker, now=NOW + timedelta(minutes=10)) is None
    tracker = MowerPositionTracker()
    tracker.record(pose(), map_index=0, now=NOW)
    resolve(tracker)
    assert resolve(tracker, now=NOW + timedelta(minutes=10)).status == "last_known"
    assert resolve(tracker, now=NOW + timedelta(days=2)) is None


@pytest.mark.parametrize("index,geometry", [(1, "lawn-b"), (0, "lawn-a-recreated")])
def test_replaced_map_geometry_invalidates_retained_coordinates(index, geometry):
    tracker = MowerPositionTracker()
    tracker.record(pose(), map_index=0, now=NOW)
    assert resolve(tracker) is not None
    later = NOW + timedelta(seconds=30)
    assert resolve(tracker, index=index, geometry=geometry, now=later) is None
    # Re-reading the original packet must not bind it to a different map.
    tracker.record(pose(), map_index=index, now=later)
    assert resolve(tracker, index=index, geometry=geometry, now=later) is None


def test_unbound_or_invalid_input_never_becomes_a_live_position():
    tracker = MowerPositionTracker()
    tracker.record(pose(), map_index=None, now=NOW)
    tracker.record(pose(), map_index=0, now=NOW)
    assert resolve(tracker) is None
    tracker.record(pose(NOW + timedelta(seconds=1), x=1000), map_index=0, now=NOW)
    assert resolve(tracker) is None
    tracker.record(pose(NOW + timedelta(hours=1)), map_index=0, now=NOW)
    assert resolve(tracker) is None


def test_offline_snapshot_never_labels_a_cached_pose_as_current():
    tracker = MowerPositionTracker()
    tracker.record(pose(), map_index=0, now=NOW)
    assert resolve(tracker).status == "current"
    snapshot = state()
    snapshot.available = False
    result = resolve(tracker, snapshot=snapshot, now=NOW + timedelta(seconds=10))
    assert result.status == "last_known"
    assert result.heading is None


@pytest.mark.parametrize("seconds", [10, 100])
def test_offline_snapshot_demotes_an_observed_dock_to_last_known(seconds):
    tracker = MowerPositionTracker()
    tracker.record(pose(), map_index=0, now=NOW)
    snapshot = state(docked=True)
    assert resolve(tracker, snapshot=snapshot).status == "known_dock"
    snapshot.available = False
    result = resolve(tracker, snapshot=snapshot, now=NOW + timedelta(seconds=seconds))
    assert result.status == "last_known"
    assert result.heading is None


def test_fresh_non_dock_pose_cannot_extend_expired_dock_evidence():
    tracker = MowerPositionTracker()
    tracker.record(pose(), map_index=0, now=NOW)
    assert resolve(tracker, snapshot=state(docked=True)).status == "known_dock"
    later = NOW + timedelta(days=8)
    tracker.record(pose(later - timedelta(seconds=10), x=40), map_index=0, now=later)
    result = resolve(tracker, snapshot=state(docked=True, at=later), now=later)
    assert result.status == "last_known"
    assert result.x == 40
