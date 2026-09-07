"""Observed duration never claims device time or unseen mowing intervals."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import session_timing
from custom_components.dreame_lawn_mower.sensor_session import (
    DreameLawnMowerObservedMowingTimeSensor,
)

NOW = datetime(2026, 9, 7, 8, tzinfo=UTC)
ObservedMowingTimer = session_timing.ObservedMowingTimer


def observe(timer, second, *, generation=1, active=True, mowing=True):
    timer.observe(
        generation=generation,
        session_active=active,
        mowing=mowing,
        now=NOW + timedelta(seconds=second),
        monotonic=float(second),
    )


def test_mid_session_start_is_partial_and_does_not_backfill():
    timer = ObservedMowingTimer()
    observe(timer, 1000)
    assert timer.minutes == 0
    assert timer.partial
    observe(timer, 1060)
    assert timer.minutes == 1
    assert timer.attributes()["source"] == "observed_mowing_state"


def test_pause_and_docking_freeze_time_and_same_session_resume_continues():
    timer = ObservedMowingTimer()
    observe(timer, 0, generation=0, active=False, mowing=False)
    observe(timer, 10)
    observe(timer, 70, mowing=False)
    observe(timer, 100, mowing=False)
    assert timer.minutes == 1
    observe(timer, 160)
    observe(timer, 220, active=False, mowing=False)
    assert timer.minutes == 2
    assert timer.state == "ended"
    assert not timer.partial
    observe(timer, 280, active=False, mowing=False)
    assert timer.minutes == 2
    observe(timer, 290, generation=2)
    assert timer.minutes == 0


def test_docked_start_with_unknown_or_resumable_task_does_not_claim_zero_minutes():
    for active in (True, False, None):
        timer = ObservedMowingTimer()
        observe(timer, 0, active=active, mowing=False)
        observe(timer, 60, active=active, mowing=False)
        assert timer.minutes is None


@pytest.mark.parametrize("interrupt", [True, False])
def test_disconnect_or_long_observation_gap_is_not_counted(interrupt):
    timer = ObservedMowingTimer()
    observe(timer, 0)
    observe(timer, 60)
    if interrupt:
        timer.interrupt()
    observe(timer, 1000)
    assert timer.minutes == 1
    assert timer.partial
    observe(timer, 1060)
    assert timer.minutes == 2


def test_new_process_does_not_restore_a_complete_session_timer():
    first = ObservedMowingTimer()
    observe(first, 0)
    observe(first, 120)
    restarted = ObservedMowingTimer()
    observe(restarted, 150)
    assert first.minutes == 2
    assert restarted.minutes == 0
    assert restarted.partial


def test_sensor_reports_the_separate_observation_and_its_limits():
    timer = ObservedMowingTimer()
    observe(timer, 0)
    observe(timer, 60)
    coordinator = SimpleNamespace(
        client=SimpleNamespace(descriptor=SimpleNamespace(unique_id="test")),
        data=SimpleNamespace(available=True),
        last_update_success=True,
        observed_mowing_timer=timer,
    )
    sensor = DreameLawnMowerObservedMowingTimeSensor(coordinator)
    assert sensor.native_value == 1
    assert sensor.unique_id == "test_observed_mowing_time"
    assert sensor.extra_state_attributes["partial"] is True
    assert sensor.extra_state_attributes["includes_pauses"] is False


def test_unavailable_snapshot_interrupts_coordinator_observation():
    from custom_components.dreame_lawn_mower.session_timing import observe_mowing_time

    timer = ObservedMowingTimer()
    observe(timer, 0)
    observe(timer, 60)
    coordinator = SimpleNamespace(observed_mowing_timer=timer)
    observe_mowing_time(
        coordinator, SimpleNamespace(available=False, activity="mowing"), True
    )
    observe(timer, 120)
    assert timer.minutes == 1
    assert timer.partial


@pytest.mark.parametrize("uncertain", [False, True])
def test_new_session_after_gap_or_unknown_state_is_partial(uncertain):
    timer = ObservedMowingTimer()
    observe(timer, 0, generation=0, active=False, mowing=False)
    if uncertain:
        observe(timer, 10, generation=0, active=None, mowing=False)
    observe(timer, 20 if uncertain else 1000, generation=1)
    assert timer.minutes == 0
    assert timer.partial
