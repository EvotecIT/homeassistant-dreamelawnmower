"""Restart evidence and bounded writes without inventing unobserved activity."""

import asyncio
import copy
import json
import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.dreame_lawn_mower import observation_checkpoint as persistence
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    position_tracking,
    session_checkpoint,
    session_timing,
)
from custom_components.dreame_lawn_mower.runtime_cache import (
    DreameLawnMowerRuntimeTelemetryCache,
)
from custom_components.dreame_lawn_mower.sensor_session import (
    DreameLawnMowerLastObservedRunSensor,
    DreameLawnMowerObservedMowingTimeSensor,
)
from custom_components.dreame_lawn_mower.session_timing import observe_mowing_time

NOW = datetime.now(UTC) - timedelta(hours=1)
MowerPositionTracker = position_tracking.MowerPositionTracker
ObservedMowingTimer = session_timing.ObservedMowingTimer
decode_run = session_checkpoint.decode_run


def observe(timer, second, *, identity=71, generation=1, active=True, mowing=True):
    timer.observe(
        generation=generation,
        session_active=active,
        mowing=mowing,
        now=NOW + timedelta(seconds=second),
        monotonic=float(second),
        identity=identity,
        identity_observed_at=NOW.timestamp() + second,
    )


def measured_timer():
    timer = ObservedMowingTimer()
    observe(timer, 0)
    observe(timer, 60)
    return timer


def test_matching_fresh_mission_restores_without_counting_the_offline_gap():
    first = measured_timer()
    second = ObservedMowingTimer()
    second.restore_checkpoint(first.checkpoint(), now=NOW + timedelta(seconds=300))
    assert second.minutes is None
    observe(second, 300, generation=5)
    assert second.minutes == 1
    assert second.partial
    assert second.started_at == first.started_at
    observe(second, 360, generation=5)
    assert second.minutes == 2
    assert second.last_run is None


@pytest.mark.parametrize("identity", [None, 72])
def test_unknown_or_different_mission_keeps_an_interrupted_previous_summary(identity):
    second = ObservedMowingTimer()
    second.restore_checkpoint(
        measured_timer().checkpoint(), now=NOW + timedelta(seconds=300)
    )
    observe(second, 300, identity=identity)
    assert second.minutes == 0
    assert second.last_run.seconds == 60
    assert second.last_run.partial
    assert second.last_run.state == "interrupted"


@pytest.mark.parametrize("event_at,new_start", [(50, None), (300, 200)])
def test_stale_task_identity_or_new_start_cannot_join_saved_time(event_at, new_start):
    second = ObservedMowingTimer()
    second.restore_checkpoint(
        measured_timer().checkpoint(), now=NOW + timedelta(seconds=300)
    )
    second.observe(
        generation=1,
        session_active=True,
        mowing=True,
        identity=71,
        identity_observed_at=NOW.timestamp() + event_at,
        new_start_at=NOW.timestamp() + new_start if new_start is not None else None,
        now=NOW + timedelta(seconds=300),
        monotonic=0,
    )
    assert second.minutes == 0
    assert second.last_run.seconds == 60


def test_previous_run_is_retained_after_end_restart_and_new_session():
    timer = measured_timer()
    observe(timer, 90, active=False, mowing=False)
    assert timer.last_run.seconds == 90
    observe(timer, 150, active=False, mowing=False)
    assert timer.last_run.updated_at == NOW + timedelta(seconds=90)
    second = ObservedMowingTimer()
    second.restore_checkpoint(timer.checkpoint(), now=NOW + timedelta(seconds=300))
    observe(second, 300, identity=72)
    assert second.last_run.seconds == 90
    assert second.last_run.state == "ended"
    assert second.minutes == 0


@pytest.mark.parametrize(
    "key,value",
    [
        ("seconds", math.nan),
        ("seconds", math.inf),
        ("seconds", -1),
        ("seconds", 999999),
        ("seconds", True),
        ("identity", True),
        ("identity", 2**80),
        ("state", []),
        ("partial", "false"),
        ("started_at", "2026-09-07T00:00:00"),
        ("updated_at", "invalid"),
    ],
)
def test_corrupt_run_fields_are_rejected(key, value):
    record = measured_timer().checkpoint()["current"]
    record[key] = value
    assert decode_run(record, now=NOW + timedelta(seconds=300)) is None


def test_restored_position_is_not_live_and_requires_map_and_bounds_match():
    first = MowerPositionTracker()
    blob = SimpleNamespace(
        frame_valid=True,
        received_at=NOW.timestamp(),
        candidate_runtime_pose_x=10,
        candidate_runtime_pose_y=20,
        candidate_runtime_heading_deg=90,
    )
    first.record(blob, map_index=0, now=NOW)
    snapshot = SimpleNamespace(available=True, activity="mowing")
    first.resolve(
        map_index=0,
        geometry="a" * 64,
        contains=lambda *_: True,
        snapshot=snapshot,
        now=NOW,
    )
    record = first.checkpoint(now=NOW)
    second = MowerPositionTracker()
    second.restore_checkpoint(record, now=NOW + timedelta(seconds=10))
    kwargs = dict(
        map_index=0,
        geometry="a" * 64,
        contains=lambda *_: True,
        snapshot=snapshot,
        now=NOW + timedelta(seconds=10),
    )
    assert second.resolve(**kwargs).status == "last_known"
    assert second.resolve(**{**kwargs, "contains": lambda *_: False}) is None
    assert second.resolve(**{**kwargs, "map_index": 1}) is None
    assert second.resolve(**{**kwargs, "geometry": "b" * 64}) is None


class Clock:
    """Deterministic event-loop timer boundary; write tasks use real asyncio."""

    def __init__(self):
        self.now = 0
        self.handles = []

    def time(self):
        return self.now

    def call_later(self, delay, callback):
        handle = SimpleNamespace(
            at=self.now + delay, cancelled=False, callback=callback
        )
        handle.when = lambda: handle.at
        handle.cancel = lambda: setattr(handle, "cancelled", True)
        self.handles.append(handle)
        return handle

    async def advance(self, seconds):
        self.now += seconds
        due = [h for h in self.handles if not h.cancelled and h.at <= self.now]
        for handle in due:
            handle.cancel()
            handle.callback()
        await asyncio.sleep(0)


def setup_checkpoint(monkeypatch, record=None):
    saved = []

    async def save(value):
        saved.append(copy.deepcopy(value))

    store = SimpleNamespace(
        async_load=AsyncMock(return_value=record),
        async_save=AsyncMock(side_effect=save),
        async_remove=AsyncMock(),
    )
    monkeypatch.setattr(persistence, "checkpoint_store", lambda *_: store)
    clock = Clock()
    hass = SimpleNamespace(loop=clock, async_create_task=asyncio.create_task)
    coordinator = SimpleNamespace(
        client=SimpleNamespace(
            descriptor=SimpleNamespace(unique_id="mower-one"),
            _position_tracker=MowerPositionTracker(),
        ),
        observed_mowing_timer=measured_timer(),
    )
    return (
        persistence.ObservationCheckpoint(hass, "entry-one", coordinator),
        coordinator,
        clock,
        store,
        saved,
    )


def test_frequent_samples_cannot_starve_checkpoints_or_write_each_heartbeat(
    monkeypatch,
):
    async def scenario():
        checkpoint, coordinator, clock, store, saved = setup_checkpoint(monkeypatch)
        checkpoint.async_schedule_save()
        await clock.advance(2)
        await checkpoint._task
        assert len(saved) == 1
        for second in range(3, 123):
            observe(coordinator.observed_mowing_timer, 60 + second)
            checkpoint.async_schedule_save()
            await clock.advance(1)
            if checkpoint._task is not None:
                await checkpoint._task
        assert 2 <= len(saved) <= 3
        await checkpoint.async_close()
        assert saved[-1]["timing"]["current"]["seconds"] == 182
        assert persistence.checkpoint_fits(saved[-1], checkpoint._key)
        assert len(json.dumps(saved[-1]).encode()) < 2048
        assert set(saved[-1]) == {"scope", "saved_at", "timing", "position"}

    asyncio.run(scenario())


def test_paused_timestamps_do_not_produce_repeated_disk_writes(monkeypatch):
    async def scenario():
        checkpoint, coordinator, clock, store, saved = setup_checkpoint(monkeypatch)
        observe(coordinator.observed_mowing_timer, 90, mowing=False)
        checkpoint.async_schedule_save()
        await clock.advance(2)
        for second in range(100, 1000, 60):
            observe(coordinator.observed_mowing_timer, second, mowing=False)
            checkpoint.async_schedule_save()
            await clock.advance(60)
        await checkpoint.async_close()
        assert len(saved) == 1

    asyncio.run(scenario())


def test_store_failure_does_not_disable_control_or_spam_logs(monkeypatch, caplog):
    async def scenario():
        checkpoint, coordinator, clock, store, saved = setup_checkpoint(monkeypatch)
        store.async_save.side_effect = OSError("disk full")
        await checkpoint.async_flush()
        await checkpoint.async_flush()
        assert caplog.text.count("checkpoint unavailable") == 1
        store.async_save.side_effect = None
        await checkpoint.async_flush()
        assert store.async_save.await_count == 3

    asyncio.run(scenario())


def test_remove_waits_for_inflight_write_and_prevents_recreation(monkeypatch):
    async def scenario():
        checkpoint, coordinator, clock, store, saved = setup_checkpoint(monkeypatch)
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_save(record):
            entered.set()
            await release.wait()
            saved.append(record)

        store.async_save.side_effect = slow_save
        checkpoint.async_schedule_save()
        await clock.advance(2)
        await entered.wait()
        removing = asyncio.create_task(checkpoint.async_remove())
        await asyncio.sleep(0)
        checkpoint.async_schedule_save()
        assert store.async_remove.await_count == 0
        release.set()
        await removing
        await clock.advance(100)
        assert store.async_remove.await_count == 1
        assert len(saved) == 1

    asyncio.run(scenario())


def test_checkpoint_size_includes_storage_envelope_and_rejects_oversize():
    key = "dreame_lawn_mower." + "a" * 32 + ".observation_checkpoint"
    assert persistence.checkpoint_fits({"padding": "a" * 15000}, key)
    assert not persistence.checkpoint_fits({"padding": "a" * 16300}, key)
    assert not persistence.checkpoint_fits({"number": math.nan}, key)


def test_previous_run_sensor_and_recorder_exclusions():
    timer = measured_timer()
    observe(timer, 90, active=False, mowing=False)
    coordinator = SimpleNamespace(
        client=SimpleNamespace(descriptor=SimpleNamespace(unique_id="test")),
        observed_mowing_timer=timer,
    )
    sensor = DreameLawnMowerLastObservedRunSensor(coordinator)
    assert sensor.native_value == 1.5
    assert sensor.extra_state_attributes["measurement_state"] == "ended"
    assert sensor.extra_state_attributes["source"] == "observed_mowing_state"
    coordinator.data = SimpleNamespace(available=False)
    coordinator.last_update_success = False
    assert sensor.available
    current = DreameLawnMowerObservedMowingTimeSensor(coordinator)
    assert not current.available
    timer.last_run = None
    assert not sensor.available
    assert (
        "last_observed_at"
        in DreameLawnMowerObservedMowingTimeSensor._unrecorded_attributes
    )


def test_cancelled_write_is_drained_before_entry_removal(monkeypatch):
    async def scenario():
        checkpoint, coordinator, clock, store, saved = setup_checkpoint(monkeypatch)
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_save(record):
            entered.set()
            await release.wait()
            saved.append(record)

        store.async_save.side_effect = slow_save
        checkpoint.async_schedule_save()
        await clock.advance(2)
        await entered.wait()
        checkpoint._task.cancel()
        removing = asyncio.create_task(checkpoint.async_remove())
        await asyncio.sleep(0)
        assert store.async_remove.await_count == 0
        release.set()
        await removing
        assert store.async_remove.await_count == 1
        count = len(saved)
        checkpoint.async_schedule_save()
        await clock.advance(100)
        assert len(saved) == count

    asyncio.run(scenario())


def test_cancelled_load_never_overwrites_unread_evidence(monkeypatch):
    async def scenario():
        checkpoint, coordinator, clock, store, saved = setup_checkpoint(monkeypatch)
        store.async_load.side_effect = asyncio.CancelledError
        with pytest.raises(asyncio.CancelledError):
            await checkpoint.async_load()
        await checkpoint.async_close()
        assert not saved

    asyncio.run(scenario())


def test_rapid_transitions_are_rate_limited(monkeypatch):
    async def scenario():
        checkpoint, coordinator, clock, store, saved = setup_checkpoint(monkeypatch)
        for second in range(120):
            observe(
                coordinator.observed_mowing_timer, 60 + second, mowing=second % 2 == 0
            )
            checkpoint.async_schedule_save()
            await clock.advance(1)
            if checkpoint._task is not None:
                await checkpoint._task
        assert len(saved) <= 12
        await checkpoint.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", "different-device"),
        ("saved_at", 10**1000),
        ("saved_at", math.nan),
        ("saved_at", -1),
        ("timing", {"unexpected": "a" * 16384}),
    ],
)
def test_invalid_store_envelope_cannot_restore_a_run(monkeypatch, field, value):
    async def scenario():
        first, _, _, _, saved = setup_checkpoint(monkeypatch)
        await first.async_close()
        record = saved[-1]
        record[field] = value
        second, owner, _, _, _ = setup_checkpoint(monkeypatch, record=record)
        owner.observed_mowing_timer = ObservedMowingTimer()
        await second.async_load()
        observe(owner.observed_mowing_timer, 120)
        assert owner.observed_mowing_timer.minutes == 0
        assert owner.observed_mowing_timer.last_run is None
        await second.async_close()

    asyncio.run(scenario())


def test_stale_terminal_snapshot_cannot_replace_saved_mission_identity():
    timer = measured_timer()
    cache = DreameLawnMowerRuntimeTelemetryCache(
        _session_generation=1,
        _session_identity=71,
        _session_started_at=NOW.timestamp(),
        _session_active=True,
    )
    coordinator = SimpleNamespace(
        observed_mowing_timer=timer, runtime_telemetry_cache=cache
    )
    snapshot = SimpleNamespace(
        available=True,
        activity="mowing",
        task_status="finished",
        task_status_event_at=NOW.timestamp() - 1,
        mission_task_id=70,
    )
    observe_mowing_time(coordinator, snapshot, True)
    assert timer.checkpoint()["current"]["identity"] == 71


@pytest.mark.parametrize("uncertainty", ["disconnect", "unknown"])
def test_ended_run_is_not_resurrected_by_later_uncertainty(uncertainty):
    timer = measured_timer()
    observe(timer, 90, active=False, mowing=False)
    ended = timer.last_run
    if uncertainty == "disconnect":
        timer.interrupt()
    else:
        observe(timer, 100, active=None, mowing=False)
    record = timer.checkpoint()
    assert record["current"] is None
    observe(timer, 120, active=False, mowing=False)
    assert timer.last_run == ended
    observe(timer, 130, generation=2)
    assert timer.last_run == ended
    restarted = ObservedMowingTimer()
    restarted.restore_checkpoint(record, now=NOW + timedelta(seconds=300))
    observe(restarted, 300, identity=72)
    assert restarted.last_run == ended
