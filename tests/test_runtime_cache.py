"""Runtime mission telemetry cache regressions."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from custom_components.dreame_lawn_mower.runtime_cache import (
    DreameLawnMowerRuntimeTelemetryCache,
    runtime_blob_has_session_metrics,
    runtime_mission_cached_session_identity,
    runtime_mission_completion_confirmed,
    runtime_mission_completion_rejected,
    runtime_mission_new_session,
    runtime_mission_new_session_event_at,
    runtime_mission_new_session_evidence,
    runtime_mission_progress_percent,
    runtime_mission_session_active,
    runtime_mission_session_event_at,
    runtime_mission_session_generation,
    runtime_mission_session_identity,
    runtime_mission_session_started_at,
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


def test_stale_terminal_heartbeat_cannot_end_the_current_mission() -> None:
    """A retained prior heartbeat cannot complete or reject the new mission."""
    stale_finished = SimpleNamespace(
        mowing_session_active=False,
        task_status="finished",
        task_status_event_at=10.0,
        mission_task_id=100,
        status_notice_name="mowing_task_completed",
    )
    stale_failed = SimpleNamespace(
        mowing_session_active=False,
        task_status="failed",
        task_status_event_at=10.0,
        mission_task_id=100,
        status_notice_name=None,
    )
    cache = DreameLawnMowerRuntimeTelemetryCache()
    cache.observe_session_state(
        active_session=True,
        session_identity=101,
        new_session_event_at=20.0,
    )
    current = SimpleNamespace(candidate_runtime_area_progress_percent=42.0)
    assert cache.update(current, active_session=True, session_identity=101) is True
    session_started_at = runtime_mission_session_started_at(cache)
    session_identity = runtime_mission_cached_session_identity(cache)
    generation = runtime_mission_session_generation(cache)

    for stale_terminal in (stale_finished, stale_failed):
        assert (
            runtime_mission_session_active(
                stale_terminal,
                tracking_active=False,
                session_started_at=session_started_at,
                session_identity=session_identity,
            )
            is True
        )
    assert (
        runtime_mission_completion_confirmed(
            stale_finished,
            tracking_active=False,
            session_started_at=session_started_at,
            session_identity=session_identity,
        )
        is False
    )
    assert (
        runtime_mission_completion_rejected(
            stale_failed,
            session_started_at=session_started_at,
            session_identity=session_identity,
        )
        is False
    )
    cache.observe_session_state(
        active_session=True,
        session_identity=runtime_mission_session_identity(
            stale_finished,
            session_started_at=session_started_at,
            cached_session_identity=session_identity,
        ),
    )
    assert runtime_mission_session_generation(cache) == generation
    assert cache.blob is current


def test_current_terminal_heartbeat_can_end_the_current_mission() -> None:
    """Matching identity or newer timing accepts terminal heartbeat evidence."""
    matching = SimpleNamespace(
        mowing_session_active=False,
        task_status="finished",
        task_status_event_at=10.0,
        mission_task_id=101,
        status_notice_name=None,
    )
    newer = SimpleNamespace(
        mowing_session_active=False,
        task_status="finished",
        task_status_event_at=30.0,
        mission_task_id=None,
        status_notice_name=None,
    )

    assert (
        runtime_mission_completion_confirmed(
            matching,
            tracking_active=False,
            session_started_at=20.0,
            session_identity=101,
        )
        is True
    )
    assert (
        runtime_mission_completion_confirmed(
            newer,
            tracking_active=False,
            session_started_at=20.0,
        )
        is True
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


def test_stale_resume_notice_cannot_override_newer_idle_state() -> None:
    """A retained property 2.2 notice cannot keep an ended mission active."""
    idle = SimpleNamespace(
        mowing_session_active=None,
        docked=True,
        state="idle",
        state_event_at=2.0,
        task_status=None,
        task_resumable=None,
        status_notice_name="mowing_resumed_after_charging",
        status_notice_event_at=1.0,
    )

    assert runtime_mission_session_active(idle, tracking_active=False) is False


def test_newer_resume_notice_can_precede_lagging_physical_state() -> None:
    """Ordered realtime evidence can retain a resumed charging mission."""
    resuming = SimpleNamespace(
        mowing_session_active=None,
        docked=True,
        state="charging",
        state_event_at=1.0,
        task_status=None,
        task_resumable=None,
        status_notice_name="mowing_resumed_after_charging",
        status_notice_event_at=2.0,
    )

    assert runtime_mission_session_active(resuming, tracking_active=False) is True


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


def test_coalesced_replacement_start_advances_mission_identity() -> None:
    """A replacement task id resets cache without an inactive snapshot."""
    prior = SimpleNamespace(candidate_runtime_area_progress_percent=42.0)
    cache = DreameLawnMowerRuntimeTelemetryCache()
    cache.observe_session_state(
        active_session=True,
        new_session=True,
        new_session_evidence=("task", 100),
        session_identity=100,
    )
    assert cache.update(
        prior,
        active_session=True,
        session_identity=100,
    ) is True

    cache.observe_session_state(
        active_session=True,
        new_session=True,
        new_session_evidence=("task", 101),
        session_identity=101,
    )

    assert cache.blob is None


def test_coalesced_replacement_notice_uses_realtime_event_identity() -> None:
    """A newer same-code notice resets cache after the command notice is consumed."""
    current = SimpleNamespace(candidate_runtime_area_progress_percent=12.5)
    cache = DreameLawnMowerRuntimeTelemetryCache()
    cache.begin_new_session()
    assert cache.update(current, active_session=True) is True
    cache.observe_session_state(
        active_session=True,
        new_session=True,
        new_session_evidence=("notice", "mowing_task_started", 1.0),
    )
    assert cache.blob is current
    cache.observe_session_state(
        active_session=True,
        new_session=True,
        new_session_evidence=("notice", "mowing_task_started", 1.0),
    )
    assert cache.blob is current

    cache.observe_session_state(
        active_session=True,
        new_session=True,
        new_session_evidence=("notice", "mowing_task_started", 2.0),
    )

    assert cache.blob is None


def test_command_acceptance_preserves_callback_telemetry_from_new_generation() -> None:
    """A callback-observed mission is not invalidated again on command return."""
    previous = SimpleNamespace(candidate_runtime_area_progress_percent=100.0)
    current = SimpleNamespace(candidate_runtime_area_progress_percent=4.0)
    cache = DreameLawnMowerRuntimeTelemetryCache()
    assert cache.update(previous, completion_confirmed=True) is True
    observed_generation = runtime_mission_session_generation(cache)

    cache.observe_session_state(active_session=True)
    assert cache.update(current, active_session=True) is True
    cache.begin_new_session(observed_generation=observed_generation)

    assert cache.blob is current
    assert cache.completion_confirmed is False


def test_command_acceptance_still_resets_unidentified_active_telemetry() -> None:
    """An active update without a new boundary cannot mask a fresh command."""
    previous = SimpleNamespace(candidate_runtime_area_progress_percent=42.0)
    current = SimpleNamespace(candidate_runtime_area_progress_percent=43.0)
    cache = DreameLawnMowerRuntimeTelemetryCache()
    assert cache.update(previous, active_session=True) is True
    observed_generation = runtime_mission_session_generation(cache)

    assert cache.update(current, active_session=True) is True
    cache.begin_new_session(observed_generation=observed_generation)

    assert cache.blob is None
    assert cache.completion_confirmed is False


def test_command_owned_start_rejects_retained_prior_completion_notice() -> None:
    """A missed start callback still establishes an ordered mission boundary."""
    current = SimpleNamespace(candidate_runtime_area_progress_percent=42.0)
    stopped = SimpleNamespace(
        mowing_session_active=False,
        task_status="idle",
        status_notice_name="mowing_task_completed",
        status_notice_event_at=10.0,
    )
    cache = DreameLawnMowerRuntimeTelemetryCache()
    cache.begin_new_session(session_started_at=20.0)
    assert cache.update(current, active_session=True) is True

    completion_confirmed = runtime_mission_completion_confirmed(
        stopped,
        tracking_active=False,
        session_started_at=runtime_mission_session_started_at(cache),
    )
    cache.observe_session_state(
        active_session=False,
        completion_confirmed=completion_confirmed,
    )

    assert completion_confirmed is False
    assert cache.completion_confirmed is False
    assert cache.blob is current
    assert (
        runtime_mission_completion_confirmed(
            SimpleNamespace(
                mowing_session_active=False,
                task_status="idle",
                status_notice_name="mowing_task_completed",
                status_notice_event_at=30.0,
            ),
            tracking_active=False,
            session_started_at=runtime_mission_session_started_at(cache),
        )
        is True
    )


def test_active_transition_uses_physical_event_as_external_start_boundary() -> None:
    """An app-owned start remains ordered when its explicit notice is missed."""
    snapshot = SimpleNamespace(
        mowing_session_active=True,
        state_event_at=20.0,
        task_status="mowing",
        task_status_event_at=21.0,
        status_notice_name="mowing_task_completed",
        status_notice_event_at=10.0,
    )
    cache = DreameLawnMowerRuntimeTelemetryCache()
    mission_active = runtime_mission_session_active(snapshot, tracking_active=True)
    cache.observe_session_state(
        active_session=mission_active,
        new_session=False,
        new_session_event_at=runtime_mission_session_event_at(
            snapshot,
            active_session=mission_active,
        ),
    )

    assert runtime_mission_session_started_at(cache) == 21.0


def test_all_area_start_notice_announces_a_new_session() -> None:
    """The emitted base-model start notice resets coalesced prior telemetry."""
    snapshot = SimpleNamespace(
        task_status="mowing",
        status_notice_name="mowing_task_started",
    )

    assert runtime_mission_new_session(snapshot) is True


def test_retained_start_notice_cannot_override_explicit_inactive_heartbeat() -> None:
    """An older start property cannot keep a manually stopped mission active."""
    snapshot = SimpleNamespace(
        mowing_session_active=False,
        state="idle",
        state_event_at=30.0,
        task_status="idle",
        task_status_event_at=30.0,
        status_notice_name="mowing_task_started",
        status_notice_event_at=10.0,
    )

    assert runtime_mission_new_session(snapshot) is False
    assert runtime_mission_new_session_event_at(snapshot) is None
    assert runtime_mission_session_active(snapshot, tracking_active=False) is False


def test_newer_start_notice_can_precede_lagging_inactive_heartbeat() -> None:
    """A newly emitted start notice wins over older physical-state evidence."""
    snapshot = SimpleNamespace(
        mowing_session_active=False,
        state="idle",
        state_event_at=30.0,
        task_status="idle",
        task_status_event_at=30.0,
        status_notice_name="mowing_task_started",
        status_notice_event_at=40.0,
    )

    assert runtime_mission_new_session(snapshot) is True
    assert runtime_mission_new_session_event_at(snapshot) == 40.0
    assert runtime_mission_session_active(snapshot, tracking_active=False) is False
    cache = DreameLawnMowerRuntimeTelemetryCache()
    cache.observe_session_state(
        active_session=False,
        new_session=True,
        new_session_event_at=runtime_mission_new_session_event_at(snapshot),
    )
    assert runtime_mission_session_generation(cache) == 1


def test_completion_notice_older_than_current_mission_is_rejected() -> None:
    """A retained prior success cannot complete a later manually stopped mission."""
    current = SimpleNamespace(candidate_runtime_area_progress_percent=42.0)
    starting = SimpleNamespace(
        mowing_session_active=True,
        task_status="starting",
        task_status_event_at=20.0,
        status_notice_name="mowing_task_completed",
        status_notice_event_at=10.0,
    )
    stopped = SimpleNamespace(
        mowing_session_active=False,
        task_status="idle",
        task_status_event_at=30.0,
        status_notice_name="mowing_task_completed",
        status_notice_event_at=10.0,
    )
    cache = DreameLawnMowerRuntimeTelemetryCache()
    cache.observe_session_state(
        active_session=True,
        new_session=runtime_mission_new_session(starting),
        new_session_evidence=runtime_mission_new_session_evidence(starting),
        new_session_event_at=runtime_mission_new_session_event_at(starting),
    )
    assert cache.update(current, active_session=True) is True

    completion_confirmed = runtime_mission_completion_confirmed(
        stopped,
        tracking_active=False,
        session_started_at=runtime_mission_session_started_at(cache),
    )
    cache.observe_session_state(
        active_session=False,
        completion_confirmed=completion_confirmed,
        new_session=runtime_mission_new_session(stopped),
        new_session_event_at=runtime_mission_new_session_event_at(stopped),
    )

    assert completion_confirmed is False
    assert cache.completion_confirmed is False
    assert cache.blob is current


def test_new_session_evidence_prefers_stable_heartbeat_task_identity() -> None:
    """Task identity deduplicates repeated snapshots and separates missions."""
    snapshot = SimpleNamespace(
        task_status="mowing",
        status_notice_name="mowing_task_started",
        status_notice_event_at=20.0,
        mission_task_id=101,
    )

    assert runtime_mission_session_identity(snapshot) == 101
    assert runtime_mission_new_session_evidence(snapshot) == ("task", 101)


def test_new_session_evidence_uses_starting_property_event_without_task_id() -> None:
    snapshot = SimpleNamespace(
        task_status="starting",
        task_status_event_at=21.0,
        status_notice_name=None,
        mission_task_id=None,
    )

    assert runtime_mission_new_session_evidence(snapshot) == (
        "task_status",
        "starting",
        21.0,
    )


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
