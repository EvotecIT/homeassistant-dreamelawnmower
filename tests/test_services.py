"""Regression checks for Home Assistant service helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError

from custom_components.dreame_lawn_mower.services import (
    PLAN_MOWING_PREFERENCE_UPDATE_SCHEMA,
    REMOTE_CONTROL_STEP_SCHEMA,
    SET_SCHEDULE_PLAN_ENABLED_SCHEMA,
    _guard_remote_control_step,
    _guard_schedule_write_request,
    _mowing_preference_notification,
    _preference_change_request,
    _schedule_write_notification,
)


def _coordinator(snapshot: object) -> object:
    return SimpleNamespace(data=snapshot)


def _snapshot(**overrides: object) -> SimpleNamespace:
    values = {
        "activity": "docked",
        "battery_level": 80,
        "mowing": False,
        "raw_attributes": {},
        "returning": False,
        "state": "charging",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_remote_control_step_schema_rejects_bool_values() -> None:
    with pytest.raises(vol.Invalid, match="rotation must be an integer"):
        REMOTE_CONTROL_STEP_SCHEMA(
            {
                "rotation": True,
                "velocity": 0,
            }
        )


def test_remote_control_step_schema_rejects_out_of_range_values() -> None:
    with pytest.raises(vol.Invalid, match="velocity must be between"):
        REMOTE_CONTROL_STEP_SCHEMA(
            {
                "rotation": 0,
                "velocity": 1001,
            }
        )


def test_set_schedule_plan_enabled_schema_defaults_to_dry_run() -> None:
    parsed = SET_SCHEDULE_PLAN_ENABLED_SCHEMA(
        {
            "map_index": "0",
            "plan_id": "1",
            "enabled": "false",
        }
    )

    assert parsed == {
        "map_index": 0,
        "plan_id": 1,
        "enabled": False,
        "execute": False,
        "confirm_schedule_write": False,
    }


def test_set_schedule_plan_enabled_schema_rejects_bool_indices() -> None:
    with pytest.raises(vol.Invalid, match="map_index must be an integer"):
        SET_SCHEDULE_PLAN_ENABLED_SCHEMA(
            {
                "map_index": True,
                "plan_id": 1,
                "enabled": False,
            }
        )


def test_set_schedule_plan_enabled_schema_rejects_negative_plan_id() -> None:
    with pytest.raises(vol.Invalid, match="plan_id must be at least 0"):
        SET_SCHEDULE_PLAN_ENABLED_SCHEMA(
            {
                "map_index": 0,
                "plan_id": -1,
                "enabled": False,
            }
        )


def test_plan_mowing_preference_update_schema_parses_numeric_values() -> None:
    parsed = PLAN_MOWING_PREFERENCE_UPDATE_SCHEMA(
        {
            "map_index": "0",
            "area_id": "11",
            "mowing_height_cm": "4.5",
            "obstacle_avoidance_ai_classes": ["people", "animals"],
        }
    )

    assert parsed == {
        "map_index": 0,
        "area_id": 11,
        "mowing_height_cm": 4.5,
        "obstacle_avoidance_ai_classes": ["people", "animals"],
    }


def test_schedule_write_guard_blocks_execute_without_confirmation() -> None:
    call = SimpleNamespace(
        data={
            "execute": True,
            "confirm_schedule_write": False,
        }
    )

    with pytest.raises(HomeAssistantError, match="confirm_schedule_write"):
        _guard_schedule_write_request(call)


def test_schedule_write_guard_allows_dry_run_without_confirmation() -> None:
    call = SimpleNamespace(
        data={
            "execute": False,
            "confirm_schedule_write": False,
        }
    )

    _guard_schedule_write_request(call)


def test_preference_change_request_requires_at_least_one_field() -> None:
    call = SimpleNamespace(data={"map_index": 0, "area_id": 11})

    with pytest.raises(
        HomeAssistantError,
        match="At least one mowing preference field",
    ):
        _preference_change_request(call)


def test_preference_change_request_extracts_supported_fields() -> None:
    call = SimpleNamespace(
        data={
            "map_index": 0,
            "area_id": 11,
            "mowing_height_cm": 5.0,
            "edge_mowing_auto": False,
        }
    )

    assert _preference_change_request(call) == {
        "mowing_height_cm": 5.0,
        "edge_mowing_auto": False,
    }


def test_schedule_write_notification_summarizes_dry_run_change() -> None:
    title, message = _schedule_write_notification(
        {
            "executed": False,
            "changed": True,
            "map_index": 0,
            "plan_id": 1,
            "previous_enabled": False,
            "enabled": True,
            "version": 19383,
            "schedule": {"label": "map_0"},
            "target_plan": {"name": "Evening trim"},
            "request": {
                "m": "s",
                "t": "SCHDSV2",
                "d": {"i": 0, "v": 19383, "s": [1, 1]},
            },
        }
    )

    assert title == "Dreame Lawn Mower Schedule Dry Run"
    assert "Built dry-run schedule enable request for map_0 Evening trim" in message
    assert "previous=False, target=True (will change), version=19383" in message
    assert '"t": "SCHDSV2"' in message


def test_mowing_preference_notification_summarizes_candidate_request() -> None:
    title, message = _mowing_preference_notification(
        {
            "map_index": 0,
            "area_id": 11,
            "mode_name": "custom",
            "changed_fields": ["mowing_height_cm", "edge_mowing_auto"],
            "previous_preference": {"mowing_height_cm": 4.0},
            "updated_preference": {"mowing_height_cm": 5.0},
            "request_candidate": {
                "m": "s",
                "t": "PRE",
                "d": [8, 0, 11],
            },
        }
    )

    assert title == "Dreame Lawn Mower Preference Dry Run"
    assert "Built dry-run mowing preference update for map 0 area 11" in message
    assert "changed_fields=mowing_height_cm, edge_mowing_auto" in message
    assert "height 4.0 -> 5.0" in message
    assert '"t": "PRE"' in message


def test_schedule_write_notification_summarizes_executed_noop() -> None:
    title, message = _schedule_write_notification(
        {
            "executed": True,
            "changed": False,
            "map_index": 0,
            "plan_id": 1,
            "previous_enabled": False,
            "enabled": False,
            "version": 19383,
            "request": {
                "m": "s",
                "t": "SCHDSV2",
                "d": {"i": 0, "v": 19383, "s": [1, 0]},
            },
            "response_data": {"r": 0, "v": 19383},
        }
    )

    assert title == "Dreame Lawn Mower Schedule Updated"
    assert "Sent schedule enable request for map 0 plan 1" in message
    assert "previous=False, target=False (was already matched)" in message
    assert 'Response: `{"r": 0, "v": 19383}`' in message


def test_remote_control_guard_blocks_active_mower() -> None:
    with pytest.raises(HomeAssistantError, match="mower is active"):
        _guard_remote_control_step(
            _coordinator(
                _snapshot(
                    activity="mowing",
                    mowing=True,
                    raw_attributes={"running": True},
                    state="mowing",
                )
            )
        )


def test_remote_control_guard_allows_existing_remote_control_session() -> None:
    _guard_remote_control_step(
        _coordinator(
            _snapshot(
                activity="mowing",
                mowing=True,
                raw_attributes={"running": True},
                state="remote_control",
            )
        )
    )


def test_remote_control_guard_blocks_mapping() -> None:
    with pytest.raises(HomeAssistantError, match="blocked while mapping"):
        _guard_remote_control_step(
            _coordinator(
                _snapshot(
                    raw_attributes={"mapping": True},
                )
            )
        )


def test_remote_control_guard_blocks_low_battery() -> None:
    with pytest.raises(HomeAssistantError, match="battery is low"):
        _guard_remote_control_step(
            _coordinator(
                _snapshot(
                    battery_level=19,
                )
            )
        )


def test_remote_control_guard_blocks_active_error() -> None:
    with pytest.raises(HomeAssistantError, match="error is active"):
        _guard_remote_control_step(
            _coordinator(
                _snapshot(
                    activity="error",
                )
            )
        )


def test_remote_control_guard_allows_unknown_battery_level() -> None:
    _guard_remote_control_step(
        _coordinator(
            _snapshot(
                battery_level=None,
            )
        )
    )
