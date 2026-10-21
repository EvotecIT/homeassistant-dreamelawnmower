"""Fast-changing attributes must stay out of the recorder database."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.dreame_lawn_mower.lawn_mower import DreameLawnMower
from custom_components.dreame_lawn_mower.sensor_runtime import (
    MOWING_PROGRESS_UNRECORDED_ATTRIBUTES,
    RUNTIME_TELEMETRY_UNRECORDED_ATTRIBUTES,
    DreameLawnMowerMowingProgressSensor,
    DreameLawnMowerRuntimeCurrentAreaSensor,
    DreameLawnMowerRuntimeHeadingSensor,
    DreameLawnMowerRuntimeMissionProgressSensor,
    DreameLawnMowerRuntimePositionXSensor,
    DreameLawnMowerRuntimePositionYSensor,
    DreameLawnMowerRuntimeTotalAreaSensor,
    _runtime_status_blob_summary,
)

RUNTIME_SENSORS = (
    DreameLawnMowerRuntimeMissionProgressSensor,
    DreameLawnMowerRuntimeCurrentAreaSensor,
    DreameLawnMowerRuntimeTotalAreaSensor,
    DreameLawnMowerRuntimePositionXSensor,
    DreameLawnMowerRuntimePositionYSensor,
    DreameLawnMowerRuntimeHeadingSensor,
)


def _live_blob() -> SimpleNamespace:
    return SimpleNamespace(
        source="runtime",
        length=33,
        frame_valid=True,
        candidate_runtime_progress_percent=42,
        candidate_runtime_area_progress_percent=40,
        candidate_runtime_current_area_sqm=120.5,
        candidate_runtime_total_area_sqm=300.0,
        candidate_runtime_region_id=1,
        candidate_runtime_task_id=7,
        candidate_runtime_pose_x=1234,
        candidate_runtime_pose_y=-567,
        candidate_runtime_heading_deg=91.0,
        candidate_runtime_track_segments=[[(0, 0), (100, 0)]],
        notes=("decoded",),
    )


def test_runtime_sensors_do_not_record_live_telemetry():
    summary = _runtime_status_blob_summary(_live_blob())
    recorded = set(summary) - RUNTIME_TELEMETRY_UNRECORDED_ATTRIBUTES
    # Only slow, mission-level identifiers stay in history.
    assert recorded == {"source", "total_area_sqm", "region_id", "task_id"}
    for sensor in RUNTIME_SENSORS:
        assert sensor._unrecorded_attributes == RUNTIME_TELEMETRY_UNRECORDED_ATTRIBUTES


def test_mowing_progress_does_not_record_runtime_blob_or_areas():
    assert (
        DreameLawnMowerMowingProgressSensor._unrecorded_attributes
        == MOWING_PROGRESS_UNRECORDED_ATTRIBUTES
    )
    assert "runtime_status_blob" in MOWING_PROGRESS_UNRECORDED_ATTRIBUTES


def test_lawn_mower_does_not_record_per_refresh_attributes():
    unrecorded = DreameLawnMower._unrecorded_attributes
    assert {"connection_last_success_at", "observed_mowing_time"} <= unrecorded
