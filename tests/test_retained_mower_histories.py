"""Reload must retain outcomes without inventing current mower state."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    scheduled_run_history,
)
from custom_components.dreame_lawn_mower.mower_condition_history import (
    MowerConditionHistory,
)
from custom_components.dreame_lawn_mower.observation_checkpoint import checkpoint_fits

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
ScheduledRunHistory = scheduled_run_history.ScheduledRunHistory


def snapshot(**changes):
    return SimpleNamespace(**{"available": True, **changes})


def test_restored_fault_reconciles_without_duplicate_or_false_clear():
    first = MowerConditionHistory()
    fault = snapshot(
        activity="error",
        error_code=23,
        error_display="Emergency stop",
        error_source="realtime_property_2.3",
    )
    first.observe(fault, observed_at=NOW)
    saved = first.checkpoint()
    restored = MowerConditionHistory()
    restored.restore_checkpoint(saved, now=NOW + timedelta(minutes=1))
    assert restored.latest()["active"] is None
    restored.observe(
        snapshot(activity=None, available=False), observed_at=NOW + timedelta(minutes=2)
    )
    assert restored.latest()["active"] is None
    restored.observe(fault, observed_at=NOW + timedelta(minutes=3))
    assert len(restored.recent()) == 1
    assert restored.latest()["active"] is True
    assert restored.latest()["observed_at"] == NOW.isoformat()
    restored.observe(snapshot(activity="idle"), observed_at=NOW + timedelta(minutes=4))
    assert restored.latest()["active"] is False
    assert restored.latest()["cleared_at"] == (NOW + timedelta(minutes=4)).isoformat()
    assert saved == first.checkpoint()


def test_fresh_clear_after_restart_and_evicted_last_error_are_retained():
    first = MowerConditionHistory()
    first.observe(
        snapshot(activity="error", error_code=23, error_display="Stop"), observed_at=NOW
    )
    second = MowerConditionHistory()
    second.restore_checkpoint(first.checkpoint(), now=NOW)
    second.observe(snapshot(activity="idle"), observed_at=NOW + timedelta(seconds=1))
    assert second.latest()["active"] is False
    for code in range(5):
        second.observe(
            snapshot(
                activity="idle",
                status_notice_code=code,
                status_notice_display=f"Warning {code}",
            ),
            observed_at=NOW + timedelta(seconds=2),
        )
    third = MowerConditionHistory()
    third.restore_checkpoint(second.checkpoint(), now=NOW + timedelta(seconds=3))
    assert third.latest(severity="error")["active"] is False
    assert third.latest(severity="error")["message"] == "Stop"
    assert len(third.recent()) == 5


def test_bad_and_expired_history_entries_do_not_discard_valid_recent_records():
    first = MowerConditionHistory()
    first.observe(
        snapshot(activity="error", error_code=23, error_display="Stop"), observed_at=NOW
    )
    saved = first.checkpoint()
    expired = deepcopy(saved["events"][0])
    expired["observed_at"] = (NOW - timedelta(days=31)).isoformat()
    bad = deepcopy(saved["events"][0])
    bad["source"] = {"token": "unsafe"}
    saved["events"] += [expired, bad]
    saved["last_error"] = expired
    restored = MowerConditionHistory()
    restored.restore_checkpoint(saved, now=NOW)
    assert len(restored.recent()) == 1
    assert restored.latest()["active"] is None
    assert restored.latest(severity="error")["message"] == "Stop"


def test_scheduled_scope_isolated_bounded_and_restored_with_timestamp():
    first = ScheduledRunHistory()
    targets = [[i, 0] for i in range(20)]
    result = first.record(
        "edge",
        "skipped",
        "rain_delay_active",
        map_index=2,
        targets=targets,
        observed_at=NOW,
    )
    targets[0][0] = 99
    result["target_ids"][0][0] = 99
    recent = first.recent()
    assert recent[0]["target_ids"][0] == [0, 0]
    assert recent[0]["target_count"] == 20
    assert recent[0]["targets_truncated"] is True
    assert len(recent[0]["target_ids"]) == 16
    second = ScheduledRunHistory()
    second.restore_checkpoint(recent, now=NOW + timedelta(seconds=1))
    assert second.latest() == first.latest()
    assert second.latest()["observed_at"] == NOW.isoformat()
    for _ in range(6):
        second.record("all", "submitted", "start_request_submitted", observed_at=NOW)
    assert len(second.recent()) == 5


def test_malformed_scheduled_outcomes_cannot_become_restored_results():
    first = ScheduledRunHistory()
    valid = first.record(
        "zone",
        "started",
        "mowing_start_confirmed",
        map_index=2,
        targets=[7],
        observed_at=NOW,
    )
    malformed = deepcopy(valid)
    malformed["status"] = []
    future = deepcopy(valid)
    future["observed_at"] = (NOW + timedelta(seconds=1)).isoformat()
    second = ScheduledRunHistory()
    second.restore_checkpoint([malformed, future, valid], now=NOW)
    assert second.recent() == [valid]


def test_full_retained_histories_fit_the_existing_storage_envelope():
    conditions = MowerConditionHistory()
    runs = ScheduledRunHistory()
    for code in range(5):
        conditions.observe(
            snapshot(
                activity="error",
                error_code=code,
                error_display="X" * 255,
                status_notice_code=code,
                status_notice_display="Y" * 255,
            ),
            observed_at=NOW,
        )
        runs.record(
            "edge",
            "failed",
            "command_not_confirmed",
            map_index=65535,
            targets=[[65535, 65535]] * 20,
            detail="Z" * 255,
            observed_at=NOW,
        )
    assert checkpoint_fits(
        {
            "scope": "f" * 64,
            "saved_at": NOW.timestamp(),
            "timing": {"current": None, "last_run": None},
            "position": {},
            "conditions": conditions.checkpoint(),
            "scheduled_runs": runs.recent(),
        },
        "dreame_lawn_mower.test-entry.observation_checkpoint",
    )
