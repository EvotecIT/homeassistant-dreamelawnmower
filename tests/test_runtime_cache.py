"""Runtime mission telemetry cache regressions."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from custom_components.dreame_lawn_mower.runtime_cache import (
    DreameLawnMowerRuntimeTelemetryCache,
    runtime_blob_has_session_metrics,
    runtime_mission_completion_confirmed,
    runtime_mission_completion_rejected,
    runtime_mission_new_session,
    runtime_mission_progress_percent,
    runtime_mission_session_active,
)


def test_empty_runtime_poll_does_not_erase_last_useful_session() -> None:
    """Post-mission empty payloads leave the latest coverage available."""
    captured_at = datetime(2026, 7, 13, 15, 5, tzinfo=UTC)
    useful = SimpleNamespace(candidate_runtime_current_area_sqm=412.53)
    cache = DreameLawnMowerRuntimeTelemetryCache()

    assert cache.update(useful, now=captured_at) is True
    assert cache.update(SimpleNamespace(), now=captured_at) is False
    assert cache.blob is useful
    assert cache.captured_at == captured_at


def test_unchanged_runtime_metrics_keep_original_capture_time() -> None:
    """Repeated docked payloads do not pretend the last mission just changed."""
    captured_at = datetime(2026, 7, 13, 15, 5, tzinfo=UTC)
    cache = DreameLawnMowerRuntimeTelemetryCache()

    assert cache.update(
        SimpleNamespace(candidate_runtime_current_area_sqm=412.53),
        now=captured_at,
    ) is True
    assert cache.update(
        SimpleNamespace(candidate_runtime_current_area_sqm=412.53),
        now=datetime(2026, 7, 13, 16, 5, tzinfo=UTC),
    ) is False
    assert cache.captured_at == captured_at


def test_active_unchanged_runtime_metrics_refresh_capture_time() -> None:
    """Repeated active metrics still belong to the current mowing session."""
    first_capture = datetime(2026, 7, 13, 15, 5, tzinfo=UTC)
    latest_capture = datetime(2026, 7, 13, 16, 5, tzinfo=UTC)
    cache = DreameLawnMowerRuntimeTelemetryCache()

    assert cache.update(
        SimpleNamespace(candidate_runtime_current_area_sqm=412.53),
        now=first_capture,
        active_session=True,
    ) is True
    assert cache.update(
        SimpleNamespace(candidate_runtime_current_area_sqm=412.53),
        now=latest_capture,
        active_session=True,
    ) is True
    assert cache.captured_at == latest_capture


def test_zero_values_are_valid_session_metrics() -> None:
    """The beginning of a new mission may legitimately report zero progress."""
    blob = SimpleNamespace(candidate_runtime_area_progress_percent=0.0)

    assert runtime_blob_has_session_metrics(blob) is True


def test_idle_zero_blob_does_not_replace_completed_session() -> None:
    """An empty idle task block must not erase final mission coverage."""
    completed = SimpleNamespace(
        candidate_runtime_area_progress_percent=100.0,
        candidate_runtime_current_area_sqm=412.53,
        candidate_runtime_total_area_sqm=412.53,
    )
    idle = SimpleNamespace(
        candidate_runtime_progress_percent=0.0,
        candidate_runtime_area_progress_percent=0.0,
        candidate_runtime_current_area_sqm=0.0,
        candidate_runtime_total_area_sqm=0.0,
    )
    cache = DreameLawnMowerRuntimeTelemetryCache()

    assert cache.update(completed) is True
    assert cache.update(idle, allow_zero=False) is False
    assert cache.blob is completed


def test_active_zero_blob_starts_a_new_session() -> None:
    """Zero remains valid while an active mission is just beginning."""
    idle = SimpleNamespace(candidate_runtime_area_progress_percent=0.0)
    cache = DreameLawnMowerRuntimeTelemetryCache()

    assert cache.update(idle, allow_zero=True) is True
    assert cache.blob is idle


def test_completed_mission_normalizes_rounded_area_progress() -> None:
    """Explicit completion wins over a slightly short measured area ratio."""
    snapshot = SimpleNamespace(
        task_status="idle",
        status_notice_name="mowing_task_completed",
    )
    blob = SimpleNamespace(candidate_runtime_area_progress_percent=99.7)

    completion_confirmed = runtime_mission_completion_confirmed(
        snapshot,
        tracking_active=False,
    )
    assert (
        runtime_mission_progress_percent(
            blob,
            completion_confirmed=completion_confirmed,
        )
        == 100.0
    )
    assert completion_confirmed is True


def test_finished_heartbeat_normalizes_rounded_area_progress() -> None:
    """The heartbeat's terminal task state is also authoritative completion."""
    snapshot = SimpleNamespace(task_status="finished", status_notice_name=None)
    blob = SimpleNamespace(candidate_runtime_area_progress_percent=99.7)

    assert (
        runtime_mission_progress_percent(
            blob,
            completion_confirmed=runtime_mission_completion_confirmed(
                snapshot,
                tracking_active=False,
            ),
        )
        == 100.0
    )


def test_docked_active_mission_remains_the_same_session() -> None:
    """Charging pauses tracking without ending a resumable mission."""
    snapshot = SimpleNamespace(
        mowing_session_active=None,
        docked=True,
        state="charging",
        task_status="paused",
        task_resumable=True,
        status_notice_name="mowing_task_completed",
    )

    assert (
        runtime_mission_session_active(snapshot, tracking_active=False) is True
    )
    assert (
        runtime_mission_completion_confirmed(snapshot, tracking_active=False)
        is False
    )


def test_missing_charging_heartbeat_preserves_active_session_boundary() -> None:
    """A failed heartbeat cannot turn a charging pause into a session end."""
    current = SimpleNamespace(candidate_runtime_area_progress_percent=42.0)
    missing_heartbeat = SimpleNamespace(
        mowing_session_active=None,
        docked=True,
        state="charging",
        task_status=None,
        task_resumable=None,
        status_notice_name="mowing_task_completed",
    )
    paused_heartbeat = SimpleNamespace(
        mowing_session_active=None,
        docked=True,
        state="charging",
        task_status="paused",
        task_resumable=True,
        status_notice_name=None,
    )
    cache = DreameLawnMowerRuntimeTelemetryCache()
    assert cache.update(current, active_session=True) is True

    missing_active = runtime_mission_session_active(
        missing_heartbeat,
        tracking_active=False,
    )
    assert missing_active is None
    cache.observe_session_state(
        active_session=missing_active,
        completion_confirmed=runtime_mission_completion_confirmed(
            missing_heartbeat,
            tracking_active=missing_active,
        ),
    )
    cache.observe_session_state(
        active_session=runtime_mission_session_active(
            paused_heartbeat,
            tracking_active=False,
        ),
    )

    assert cache.blob is current
    assert cache.completion_confirmed is False


def test_missing_idle_heartbeat_ends_active_session_boundary() -> None:
    """A docked idle snapshot cannot join the next mission to the prior one."""
    previous = SimpleNamespace(candidate_runtime_area_progress_percent=42.0)
    idle = SimpleNamespace(
        mowing_session_active=None,
        docked=True,
        state="idle",
        task_status=None,
        task_resumable=None,
        status_notice_name=None,
    )
    cache = DreameLawnMowerRuntimeTelemetryCache()
    assert cache.update(previous, active_session=True) is True

    idle_active = runtime_mission_session_active(idle, tracking_active=False)
    assert idle_active is False
    cache.observe_session_state(active_session=idle_active)
    cache.observe_session_state(active_session=True)

    assert cache.blob is None


def test_starting_signal_resets_an_already_active_prior_session_once() -> None:
    """Coalesced stop/start callbacks cannot reuse the prior mission blob."""
    previous = SimpleNamespace(candidate_runtime_area_progress_percent=42.0)
    current = SimpleNamespace(candidate_runtime_area_progress_percent=1.0)
    starting = SimpleNamespace(
        mowing_session_active=None,
        docked=True,
        state="charging",
        task_status="starting",
        status_notice_name=None,
    )
    cache = DreameLawnMowerRuntimeTelemetryCache()
    assert cache.update(previous, active_session=True) is True

    mission_active = runtime_mission_session_active(
        starting,
        tracking_active=False,
    )
    assert mission_active is True
    cache.observe_session_state(
        active_session=mission_active,
        new_session=runtime_mission_new_session(starting),
    )
    assert cache.blob is None
    assert cache.update(
        current,
        active_session=mission_active,
        new_session=runtime_mission_new_session(starting),
    ) is True
    cache.observe_session_state(active_session=True, new_session=False)

    assert cache.blob is current


def test_command_start_deduplicates_noncontiguous_start_notice() -> None:
    """A delayed start notice cannot erase telemetry from the same mission."""
    previous = SimpleNamespace(candidate_runtime_area_progress_percent=100.0)
    current = SimpleNamespace(candidate_runtime_area_progress_percent=12.5)
    cache = DreameLawnMowerRuntimeTelemetryCache()
    assert cache.update(previous, completion_confirmed=True) is True

    cache.begin_new_session()
    cache.observe_session_state(active_session=False, new_session=False)
    assert cache.update(current, active_session=True, new_session=False) is True
    cache.observe_session_state(active_session=True, new_session=False)
    cache.observe_session_state(active_session=True, new_session=True)

    assert cache.blob is current
    assert cache.completion_confirmed is False

    cache.observe_session_state(active_session=False)
    cache.observe_session_state(active_session=True, new_session=True)

    assert cache.blob is None


def test_all_area_start_notice_announces_a_new_session() -> None:
    """The emitted base-model start notice resets coalesced prior telemetry."""
    snapshot = SimpleNamespace(
        task_status="mowing",
        status_notice_name="mowing_task_started",
    )

    assert runtime_mission_new_session(snapshot) is True


def test_active_or_incomplete_mission_keeps_measured_progress() -> None:
    """Returns, pauses, and new sessions must not be presented as complete."""
    blob = SimpleNamespace(candidate_runtime_area_progress_percent=73.3)
    returning = SimpleNamespace(
        task_status="returning_to_dock",
        status_notice_name="low_battery_returning",
    )
    stale_notice = SimpleNamespace(
        task_status="mowing",
        status_notice_name="mowing_task_completed",
    )
    manually_stopped = SimpleNamespace(
        task_status="completed",
        status_notice_name=None,
    )

    assert (
        runtime_mission_progress_percent(
            blob,
            completion_confirmed=runtime_mission_completion_confirmed(
                returning,
                tracking_active=False,
            ),
        )
        == 73.3
    )
    for unsuccessful_status in ("failed", "exit"):
        assert (
            runtime_mission_progress_percent(
                blob,
                completion_confirmed=runtime_mission_completion_confirmed(
                    SimpleNamespace(
                        task_status=unsuccessful_status,
                        status_notice_name="mowing_task_completed",
                    ),
                    tracking_active=False,
                ),
            )
            == 73.3
        )
    assert (
        runtime_mission_progress_percent(
            blob,
            completion_confirmed=runtime_mission_completion_confirmed(
                manually_stopped,
                tracking_active=False,
            ),
        )
        == 73.3
    )
    assert (
        runtime_mission_progress_percent(
            blob,
            completion_confirmed=runtime_mission_completion_confirmed(
                stale_notice,
                tracking_active=True,
            ),
        )
        == 73.3
    )


def test_completion_without_runtime_measurement_remains_unavailable() -> None:
    """A completion event alone must not invent runtime telemetry."""
    snapshot = SimpleNamespace(
        task_status="finished",
        status_notice_name="mowing_task_completed",
    )

    assert (
        runtime_mission_progress_percent(
            SimpleNamespace(),
            completion_confirmed=runtime_mission_completion_confirmed(
                snapshot,
                tracking_active=False,
            ),
        )
        is None
    )


def test_explicit_failure_clears_cached_completion_latch() -> None:
    """A later failed heartbeat cannot leave stale success attached."""
    blob = SimpleNamespace(candidate_runtime_area_progress_percent=99.7)
    failed = SimpleNamespace(
        task_status="failed",
        status_notice_name="mowing_task_completed",
    )
    cache = DreameLawnMowerRuntimeTelemetryCache()
    assert cache.update(blob, completion_confirmed=True) is True

    cache.observe_session_state(
        active_session=False,
        completion_confirmed=runtime_mission_completion_confirmed(
            failed,
            tracking_active=False,
            cached_completion_confirmed=cache.completion_confirmed,
        ),
        completion_rejected=runtime_mission_completion_rejected(failed),
    )

    assert cache.completion_confirmed is False


def test_cache_preserves_completion_until_the_next_active_session() -> None:
    """Transient completion evidence remains attached to the cached mission."""
    blob = SimpleNamespace(candidate_runtime_area_progress_percent=99.7)
    cache = DreameLawnMowerRuntimeTelemetryCache()

    assert cache.update(blob, completion_confirmed=True) is True
    assert cache.completion_confirmed is True
    assert cache.update(SimpleNamespace()) is False
    assert cache.completion_confirmed is True
    assert cache.update(SimpleNamespace(), active_session=True) is False
    assert cache.completion_confirmed is False
    assert cache.blob is None
    assert cache.captured_at is None


def test_cache_only_invalidates_once_during_an_active_session() -> None:
    """Fresh telemetry remains cached across later polls in the same mission."""
    previous = SimpleNamespace(candidate_runtime_area_progress_percent=99.7)
    current = SimpleNamespace(candidate_runtime_area_progress_percent=12.5)
    cache = DreameLawnMowerRuntimeTelemetryCache()

    assert cache.update(previous, completion_confirmed=True) is True
    assert cache.update(current, active_session=True) is True
    cache.observe_session_state(active_session=True)

    assert cache.blob is current
    assert cache.completion_confirmed is False
