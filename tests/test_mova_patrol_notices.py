"""Reported MOVA patrol completion reaches current and retained HA notices."""

from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.mower_condition_history import (
    MowerConditionHistory,
)
from custom_components.dreame_lawn_mower.sensor import SENSORS
from custom_components.dreame_lawn_mower.sensor_conditions import (
    DreameLawnMowerConditionSensor,
)
from tests.test_mower_error_semantics import _snapshot


@pytest.mark.parametrize("source", ["status", "realtime"])
def test_patrol_completion_reaches_current_and_retained_mova_notices(source):
    snapshot = _snapshot(
        74 if source == "status" else None,
        "unknown",
        realtime_error_code=74 if source == "realtime" else None,
        state="RETURNING",
        model="mova.mower.g2529c",
        account_type="mova",
    )
    assert snapshot.activity == "returning"
    assert snapshot.error_code is None
    assert snapshot.status_notice_code == 74
    assert snapshot.status_notice_tier == "attention"
    notice = next(item for item in SENSORS if item.key == "status_notice")
    assert notice.value_fn(snapshot) == "Patrol task completed"

    history = MowerConditionHistory()
    assert history.observe(snapshot)
    retained = object.__new__(DreameLawnMowerConditionSensor)
    retained._kind = "notification"
    retained.coordinator = SimpleNamespace(
        mower_condition_history=history, data=snapshot,
    )
    retained._descriptor = snapshot.descriptor
    assert retained.native_value == "Patrol task completed"
    assert retained.extra_state_attributes["code"] == 74
