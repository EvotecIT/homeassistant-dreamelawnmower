"""Pure regressions for an observed LiDAX automatic-return heartbeat.

Use a test-only namespace and synthetic, position-free frames.
"""

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

PACKAGE = "_mova_return_heartbeat_protocol_test"
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


def decode(value, key="1.1"):
    return protocol.decode_mower_status_blob(value, property_key=key)


def returning_frame():
    value = [0] * 21
    value[0] = value[-1] = 0xCE
    value[11] = 75
    value[12:15] = [5, 40, 140]
    return value


def test_observed_return_remains_active_outside_station():
    result = decode(returning_frame())
    assert result.task_status == "returning_to_dock"
    assert result.mowing_session_active is True
    assert result.task_resumable is False
    assert result.heartbeat_docked is False
    assert "unexpected_length" not in result.notes


@pytest.mark.parametrize("state_code", [0, -1, -4004, None])
def test_return_frame_requires_successful_fresh_state(state_code):
    rows = [{"siid": 1, "piid": 1, "code": 0, "value": returning_frame()}]
    if state_code is not None:
        rows.append({"siid": 2, "piid": 1, "code": state_code, "value": 5})
    if state_code == 0:
        assert property_read.require_fresh_task_properties(
            rows, types.DreameMowerPropertyMapping
        )["heartbeat"] is rows[0]
    else:
        with pytest.raises(errors.DeviceUpdateFailedException):
            property_read.require_fresh_task_properties(
                rows, types.DreameMowerPropertyMapping
            )


@pytest.mark.parametrize("key", ["1.4", None])
def test_runtime_property_does_not_authorize_return_task_state(key):
    assert decode(returning_frame(), key).mowing_session_active is None


@pytest.mark.parametrize(
    "index,value", [(0, 0), (20, 0), (12, 4), (13, 39), (14, 136)]
)
def test_other_return_shapes_remain_unclassified(index, value):
    raw = returning_frame()
    raw[index] = value
    assert decode(raw).mowing_session_active is None
