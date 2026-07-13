"""Runtime mission telemetry cache regressions."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from custom_components.dreame_lawn_mower.runtime_cache import (
    DreameLawnMowerRuntimeTelemetryCache,
    runtime_blob_has_session_metrics,
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
