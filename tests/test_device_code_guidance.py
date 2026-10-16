"""Recovery guidance exposes catalog uncertainty without clearing faults."""

from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device_code_semantics,
)
from custom_components.dreame_lawn_mower.mower_condition_history import (
    MowerConditionHistory,
)
from custom_components.dreame_lawn_mower.sensor_conditions import (
    DreameLawnMowerConditionSensor,
)
from dreame_lawn_mower_client import mower_device_code_context

mower_fault_code = device_code_semantics.mower_fault_code


@pytest.mark.parametrize(
    "model",
    [
        "dreame.mower.g2408",
        "dreame.mower.g2568e",
        "dreame.mower.q2501c",
        "dreame.mower.g2541g",
    ],
)
def test_startup_map_conflict_stays_ambiguous_and_keeps_hard_fault(model):
    result = mower_device_code_context(25, model=model, firmware="4.3.6_0625")
    assert result["possible_meanings"] == [
        "map_file_damaged",
        "robot_startup_abnormality",
    ]
    assert result["meaning_ambiguous"] is True
    assert result["firmware_transition_known"] is False
    assert result["tier"] == "error"
    assert result["automatic_recovery"] is False
    assert mower_fault_code(25, model=model) == 25
    assert any("Preserve existing maps" in check for check in result["recovery_checks"])


@pytest.mark.parametrize(
    ("code", "field_name", "app_name"),
    [
        (19, "emergency_stop_pressed", "location_lost"),
        (73, "robot_lifted", "top_cover_open"),
        (53, "mowing_started", "scheduled_mowing_started"),
        (70, "mowing_resumed_after_charging", "resuming_unfinished_task"),
    ],
)
def test_a1_field_meaning_is_retained_with_explicit_app_alternative(
    code, field_name, app_name
):
    result = mower_device_code_context(code, model="dreame.mower.p2255")
    assert result["name"] == field_name
    assert result["possible_meanings"] == [field_name, app_name]
    assert result["firmware_transition_known"] is False


def test_unknown_codes_offer_diagnostics_without_promoting_to_hard_fault():
    result = mower_device_code_context(4321, model="dreame.mower.g3255")
    assert result["tier"] == "unknown"
    assert result["automatic_recovery"] is False
    assert mower_fault_code(4321, model="dreame.mower.g3255") is None
    assert any("diagnostics" in check for check in result["recovery_checks"])
    assert mower_device_code_context(-1) is None


def test_new_map_notice_does_not_inherit_a3_edge_cutter_guidance():
    notice = mower_device_code_context(47, model="dreame.mower.g2568e")
    fault = mower_device_code_context(47, model="dreame.mower.q2501c")
    assert notice["tier"] == "info" and notice["recovery_checks"] == []
    assert fault["tier"] == "error"
    assert any("power-off" in check for check in fault["recovery_checks"])


def test_condition_sensor_only_attaches_guidance_to_confirmed_current_condition():
    snapshot = SimpleNamespace(
        available=True,
        activity="error",
        error_code=25,
        error_display="Startup or map data error",
        error_source="realtime",
        firmware_version="4.3.6_0625",
    )
    history = MowerConditionHistory()
    history.observe(snapshot)
    entity = object.__new__(DreameLawnMowerConditionSensor)
    entity._kind = "error"
    entity._descriptor = SimpleNamespace(model="dreame.mower.g2568e")
    entity.coordinator = SimpleNamespace(data=snapshot, mower_condition_history=history)
    assert entity.extra_state_attributes["guidance"]["meaning_ambiguous"] is True
    assert entity.extra_state_attributes["guidance"]["firmware"] == "4.3.6_0625"
    snapshot.available = False
    assert "guidance" not in entity.extra_state_attributes
    snapshot.available = True
    snapshot.error_code = 23
    assert "guidance" not in entity.extra_state_attributes
