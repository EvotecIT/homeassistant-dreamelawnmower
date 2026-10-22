"""Fast-changing attributes must stay out of the recorder database."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from homeassistant.components.recorder.db_schema import StateAttributes
from homeassistant.core import Event, State

from custom_components.dreame_lawn_mower.lawn_mower import DreameLawnMower
from custom_components.dreame_lawn_mower.sensor_runtime import (
    DreameLawnMowerMowingProgressSensor,
    DreameLawnMowerRuntimeCurrentAreaSensor,
    DreameLawnMowerRuntimeHeadingSensor,
    DreameLawnMowerRuntimeMissionProgressSensor,
    DreameLawnMowerRuntimePositionXSensor,
    DreameLawnMowerRuntimePositionYSensor,
    DreameLawnMowerRuntimeTotalAreaSensor,
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
    """Return a decoded frame containing both live and mission-level data."""
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


def _recorded_attributes(entity_type, attributes):
    """Apply HA's recorder serializer without a database or external service."""
    state = State(
        "sensor.mower_recorder_test",
        "42",
        attributes,
        state_info={"unrecorded_attributes": entity_type._unrecorded_attributes},
    )
    event = Event("state_changed", {"new_state": state})
    recorded = json.loads(StateAttributes.shared_attrs_bytes_from_event(event, None))
    assert dict(state.attributes) == attributes
    return recorded


@pytest.mark.parametrize("sensor_type", RUNTIME_SENSORS)
def test_runtime_sensors_do_not_record_live_telemetry(sensor_type):
    """Live frame changes retain only mission metadata in recorded attributes."""
    blob = _live_blob()
    entity = object.__new__(sensor_type)
    entity.coordinator = SimpleNamespace(
        data=SimpleNamespace(activity="mowing"),
        runtime_status_blob=blob,
    )
    expected = {
        "source": "runtime", "total_area_sqm": 300.0, "region_id": 1, "task_id": 7,
    }
    assert entity.native_value is not None
    assert entity.extra_state_attributes["pose_x"] == 1234
    assert _recorded_attributes(sensor_type, entity.extra_state_attributes) == expected

    blob.candidate_runtime_pose_x = 2000
    blob.candidate_runtime_current_area_sqm = 125.0
    blob.candidate_runtime_track_segments.append([(100, 0), (200, 0)])
    assert entity.extra_state_attributes["pose_x"] == 2000
    assert entity.extra_state_attributes["current_area_sqm"] == 125.0
    assert _recorded_attributes(sensor_type, entity.extra_state_attributes) == expected


def test_mowing_progress_does_not_record_runtime_blob_or_areas():
    """Progress stays live while area, time and nested telemetry leave history."""
    snapshot = SimpleNamespace(
        mowed_area=120.5, mowing_time=20, cleaning_time=20,
        current_zone_name="Front Lawn", active_segment_count=1,
    )
    entity = object.__new__(DreameLawnMowerMowingProgressSensor)
    entity.coordinator = SimpleNamespace(
        data=snapshot, app_maps=None, batch_device_data=None,
        runtime_status_blob=_live_blob(),
    )
    attributes = entity.extra_state_attributes
    assert attributes["mowed_area"] == attributes["cleaned_area"] == 120.5
    assert attributes["mowing_time"] == attributes["cleaning_time"] == 20
    assert attributes["runtime_status_blob"]["pose_x"] == 1234
    expected = {"current_zone": "Front Lawn", "active_segment_count": 1}
    assert _recorded_attributes(type(entity), attributes) == expected

    snapshot.mowed_area = 125.0
    snapshot.mowing_time = snapshot.cleaning_time = 21
    assert entity.extra_state_attributes["mowed_area"] == 125.0
    assert _recorded_attributes(type(entity), entity.extra_state_attributes) == expected


def test_lawn_mower_does_not_record_per_refresh_attributes():
    """Both public and legacy area/time names must be excluded from history."""
    attributes = {
        "connection_last_success_at": "2026-10-22T06:00:00+00:00",
        "observed_mowing_time": 20,
        "observed_mowing_time_details": {"minutes": 20},
        "recent_scheduled_runs": [{"duration": 20}],
        "mowed_area": 120.5, "cleaned_area": 120.5,
        "mowing_time": 20, "cleaning_time": 20,
        "model": "dreame.mower.p2255", "selected_map_index": 1,
    }
    assert _recorded_attributes(DreameLawnMower, attributes) == {
        "model": "dreame.mower.p2255", "selected_map_index": 1,
    }
