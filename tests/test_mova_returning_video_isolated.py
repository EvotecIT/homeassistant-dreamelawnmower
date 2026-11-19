"""Pure state-gate regressions for owner-observed LiDAX video during return."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

PACKAGE = "_mova_returning_video_test"
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
models = importlib.import_module(f"{PACKAGE}.models")


def snapshot(**changes):
    values = {
        "descriptor": SimpleNamespace(model="mova.mower.g2529c"),
        "state": "returning",
        "activity": "returning",
        "returning": True,
        "raw_attributes": {},
        "available": True,
        "battery_level": 80,
    }
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("activity", ["returning", ""])
def test_observed_model_can_open_video_during_explicit_return(running, activity):
    assert (
        models.camera_stream_block_reason(
            snapshot(
                activity=activity,
                raw_attributes={"running": running, "returning": True},
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "model", ["mova.mower.g2529b", "mova.mower.g2529f", "dreame.mower.g2408", None]
)
def test_other_or_missing_model_keeps_return_gate(model):
    assert (
        models.camera_stream_block_reason(
            snapshot(descriptor=SimpleNamespace(model=model))
        )
        is not None
    )


@pytest.mark.parametrize("state", ["docked", "charging", "charging_completed"])
def test_station_state_overrides_stale_return_flag(state):
    assert models.camera_stream_block_reason(snapshot(state=state)) is not None


def test_physical_dock_flag_overrides_return_state():
    assert models.camera_stream_block_reason(snapshot(docked=True)) is not None


@pytest.mark.parametrize("flag", ["mapping", "fast_mapping"])
def test_mapping_gate_remains(flag):
    assert (
        models.camera_stream_block_reason(snapshot(raw_attributes={flag: True}))
        is not None
    )


@pytest.mark.parametrize("state", ["idle", "error", "unknown"])
def test_return_flag_does_not_authorize_conflicting_state(state):
    assert models.camera_stream_block_reason(snapshot(state=state)) is not None


def test_raw_return_flag_alone_is_not_explicit_return():
    assert (
        models.camera_stream_block_reason(
            snapshot(
                state="idle",
                activity="idle",
                returning=False,
                raw_attributes={"returning": True},
            )
        )
        is not None
    )


def test_unknown_running_state_still_blocks():
    assert (
        models.camera_stream_block_reason(
            snapshot(
                state="unknown",
                activity="unknown",
                returning=False,
                raw_attributes={"running": True},
            )
        )
        is not None
    )


def test_stale_raw_return_flag_does_not_block_known_mowing():
    assert (
        models.camera_stream_block_reason(
            snapshot(
                state="mowing",
                activity="mowing",
                returning=False,
                raw_attributes={"running": True, "returning": True},
            )
        )
        is None
    )


def test_remote_control_remains_blocked_during_return():
    assert models.remote_control_block_reason(snapshot()) is not None
