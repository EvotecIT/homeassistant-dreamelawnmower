"""Pure-protocol regressions for the observed LiDAX 21-byte mowing frame.

Use a test-only namespace so these tests do not load the HA integration or any
cloud/network dependencies. All frame values are synthetic, without positions.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

PACKAGE = "_mova_heartbeat_protocol_test"
package = ModuleType(PACKAGE)
package.__path__ = [
    str(
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "dreame_lawn_mower"
        / "dreame_lawn_mower_client"
    )
]
sys.modules[PACKAGE] = package
protocol = importlib.import_module(f"{PACKAGE}.app_protocol")
property_read = importlib.import_module(f"{PACKAGE}.device_property_read")
types = importlib.import_module(f"{PACKAGE}.device_types")
errors = importlib.import_module(f"{PACKAGE}.exceptions")


def frame() -> list[int]:
    """Keep only the observed length and state bytes; synthesize everything else."""
    value = [0] * 21
    value[0] = value[-1] = 0xCE
    value[11] = 75  # Synthetic battery, not a captured field.
    value[12:15] = [5, 35, 135]
    return value


def decode(value: list[int], key: str = "1.1"):
    return protocol.decode_mower_status_blob(value, property_key=key)


def test_observed_mova_mowing_shape_decodes_task_and_docking():
    result = decode(frame())
    assert result.frame_valid
    assert result.task_status == "mowing"
    assert result.mowing_session_active is True
    assert result.task_resumable is False
    assert result.heartbeat_docked is False
    assert "unexpected_length" not in result.notes


def test_fresh_read_accepts_validated_native_frame_with_fresh_state():
    rows = [
        {"siid": 2, "piid": 1, "code": 0, "value": 1},
        {"siid": 1, "piid": 1, "code": 0, "value": frame()},
    ]
    result = property_read.require_fresh_task_properties(
        rows, types.DreameMowerPropertyMapping
    )
    assert result["heartbeat"] is rows[1]


@pytest.mark.parametrize("key", ["1.4", None])
def test_same_bytes_are_not_evidence_without_heartbeat_property(key):
    result = decode(frame(), key)
    assert result.mowing_session_active is None
    assert result.task_resumable is None


@pytest.mark.parametrize("index,value", [(0, 0), (20, 0), (12, 1), (13, 36), (14, 128)])
def test_unproven_or_invalid_shape_does_not_gain_task_evidence(index, value):
    raw = frame()
    raw[index] = value
    result = decode(raw)
    assert result.mowing_session_active is None
    assert result.task_resumable is None


@pytest.mark.parametrize("code", [-1, -4004])
def test_rejected_fresh_state_still_blocks_decision(code):
    rows = [
        {"siid": 2, "piid": 1, "code": code, "value": 1},
        {"siid": 1, "piid": 1, "code": 0, "value": frame()},
    ]
    with pytest.raises(errors.DeviceUpdateFailedException):
        property_read.require_fresh_task_properties(
            rows, types.DreameMowerPropertyMapping
        )


def test_missing_fresh_heartbeat_does_not_reuse_cached_mowing_state():
    with pytest.raises(errors.DeviceUpdateFailedException):
        property_read.require_fresh_task_properties(
            [{"siid": 2, "piid": 1, "code": 0, "value": 1}],
            types.DreameMowerPropertyMapping,
        )


def test_existing_standard_and_a3_station_shapes_keep_working():
    standard = frame()[:19] + [0xCE]
    assert decode(standard).task_status == "mowing"
    a3 = [0] * 22
    a3[0] = a3[-1] = 0xCE
    a3[12:15] = [1, 255, 0]
    assert decode(a3).task_status == "idle"
    assert decode(a3).heartbeat_docked is True


@pytest.mark.parametrize(
    "status_bytes,status,active,resumable",
    [([5, 36, 0], "paused", True, True), ([1, 255, 0], "idle", False, False)],
)
def test_observed_finish_transition_preserves_session_evidence(
    status_bytes, status, active, resumable
):
    raw = frame()
    raw[12:15] = status_bytes
    result = decode(raw)
    assert result.task_status == status
    assert result.mowing_session_active is active
    assert result.task_resumable is resumable
    assert result.heartbeat_docked is True


@pytest.mark.parametrize("status_bytes", [[5, 36, 0], [1, 255, 0]])
def test_station_frames_can_supply_fresh_task_evidence(status_bytes):
    raw = frame()
    raw[12:15] = status_bytes
    rows = [
        {"siid": 2, "piid": 1, "code": 0, "value": 13},
        {"siid": 1, "piid": 1, "code": 0, "value": raw},
    ]
    result = property_read.require_fresh_task_properties(
        rows, types.DreameMowerPropertyMapping
    )
    assert result["heartbeat"] is rows[1]


@pytest.mark.parametrize("status_bytes", [[5, 37, 0], [1, 255, 128], [5, 36, 4]])
def test_unobserved_station_shapes_remain_unknown(status_bytes):
    raw = frame()
    raw[12:15] = status_bytes
    assert decode(raw).task_status is None
