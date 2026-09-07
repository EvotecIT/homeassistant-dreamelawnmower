"""Real starting heartbeats must not split a paused or starting mission."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.coordinator import (
    DreameLawnMowerCoordinator,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    session_timing,
)
from custom_components.dreame_lawn_mower.runtime_cache import (
    DreameLawnMowerRuntimeTelemetryCache,
    runtime_mission_new_session_event_at,
    runtime_mission_new_session_evidence,
    runtime_mission_session_generation,
)


@pytest.mark.parametrize("task_id", [None, 71])
def test_starting_heartbeats_and_resume_preserve_one_observed_mission(
    monkeypatch, task_id
):
    """A2 reports starting repeatedly and again on resume, without a task id."""
    coordinator = object.__new__(DreameLawnMowerCoordinator)
    coordinator.runtime_telemetry_cache = DreameLawnMowerRuntimeTelemetryCache()
    coordinator.update_interval = timedelta(seconds=60)
    tick = [0.0]
    monkeypatch.setattr(session_timing.time, "monotonic", lambda: tick[0])

    def publish(second, activity, task_status, active=True):
        tick[0] = float(second)
        snapshot = SimpleNamespace(
            available=True,
            activity=activity,
            state=activity,
            state_event_at=float(second),
            task_status=task_status,
            task_status_event_at=float(second),
            task_resumable=activity == "paused",
            mowing_session_active=active,
            mission_task_id=task_id if active else None,
        )
        coordinator._observe_runtime_mission_boundary(snapshot)

    publish(0, "idle", "idle", False)
    publish(10, "mowing", "starting")
    generation = runtime_mission_session_generation(coordinator.runtime_telemetry_cache)
    started_at = coordinator.observed_mowing_timer.started_at
    publish(20, "mowing", "starting")
    publish(30, "mowing", "starting")
    publish(40, "mowing", "mowing")
    publish(50, "paused", "paused")
    assert coordinator.observed_mowing_timer.seconds == 40
    publish(80, "paused", "paused")
    assert coordinator.observed_mowing_timer.seconds == 40
    publish(90, "mowing", "starting")
    publish(100, "mowing", "starting")
    publish(110, "mowing", "mowing")
    assert coordinator.observed_mowing_timer.minutes == 1.0
    assert coordinator.observed_mowing_timer.started_at == started_at
    assert (
        runtime_mission_session_generation(coordinator.runtime_telemetry_cache)
        == generation
    )


def test_starting_echo_cannot_erase_metrics_after_command_or_late_identity():
    cache = DreameLawnMowerRuntimeTelemetryCache()
    cache.begin_new_session(session_started_at=10.0)
    cache.observe_session_state(
        active_session=True,
        new_session=True,
        new_session_evidence=("task_status", "starting", 11.0),
        new_session_event_at=11.0,
    )
    metrics = SimpleNamespace(candidate_runtime_area_progress_percent=12.0)
    cache.update(metrics, active_session=True)
    generation = runtime_mission_session_generation(cache)
    for event_at, identity in ((12.0, None), (13.0, 71), (14.0, 71)):
        cache.observe_session_state(
            active_session=True,
            new_session=True,
            new_session_evidence=("task", identity)
            if identity is not None
            else ("task_status", "starting", event_at),
            new_session_event_at=event_at,
            session_identity=identity,
        )
        assert cache.blob is metrics
        assert runtime_mission_session_generation(cache) == generation

    cache.observe_session_state(
        active_session=True,
        new_session=True,
        new_session_evidence=("task", 72),
        new_session_event_at=20.0,
        session_identity=72,
    )
    assert cache.blob is None
    assert runtime_mission_session_generation(cache) == generation + 1


def test_explicit_replacement_notice_wins_over_an_ambiguous_starting_heartbeat():
    cache = DreameLawnMowerRuntimeTelemetryCache()
    metrics = SimpleNamespace(candidate_runtime_area_progress_percent=12.0)
    cache.update(metrics, active_session=True)
    generation = runtime_mission_session_generation(cache)
    snapshot = SimpleNamespace(
        mowing_session_active=True,
        task_status="starting",
        task_status_event_at=31.0,
        status_notice_name="mowing_task_started",
        status_notice_event_at=30.0,
        mission_task_id=None,
    )
    evidence = runtime_mission_new_session_evidence(snapshot)
    event_at = runtime_mission_new_session_event_at(snapshot)
    assert evidence == ("notice", "mowing_task_started", 30.0)
    assert event_at == 30.0
    cache.observe_session_state(
        active_session=True,
        new_session=True,
        new_session_evidence=evidence,
        new_session_event_at=event_at,
    )
    assert cache.blob is None
    assert runtime_mission_session_generation(cache) == generation + 1


@pytest.mark.parametrize("notice_at", [10.0, 30.0])
def test_retained_notice_before_terminal_boundary_cannot_backdate_next_start(notice_at):
    snapshot = SimpleNamespace(
        state="idle",
        state_event_at=30.0,
        mowing_session_active=True,
        task_status="starting",
        task_status_event_at=40.0,
        status_notice_name="mowing_task_started",
        status_notice_event_at=notice_at,
        mission_task_id=None,
    )
    assert runtime_mission_new_session_event_at(snapshot) == 40.0
    assert runtime_mission_new_session_evidence(snapshot) == (
        "task_status",
        "starting",
        40.0,
    )
