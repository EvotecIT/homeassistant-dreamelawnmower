"""Regression checks for Home Assistant service helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError

from custom_components.dreame_lawn_mower.services import (
    ATTR_PREFERENCE_MODE,
    PLAN_MAINTENANCE_RESET_SCHEMA,
    PLAN_MOWING_PREFERENCE_UPDATE_SCHEMA,
    PLAN_SCHEDULE_UPLOAD_SCHEMA,
    REMOTE_CONTROL_STEP_SCHEMA,
    SET_SCHEDULE_PLAN_ENABLED_SCHEMA,
    _guard_maintenance_reset_request,
    _guard_preference_write_request,
    _guard_remote_control_step,
    _guard_schedule_write_request,
    _maintenance_reset_notification,
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
        "execute": False,
        "confirm_preference_write": False,
        "mowing_height_cm": 4.5,
        "obstacle_avoidance_ai_classes": ["people", "animals"],
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("mowing_direction_mode", 3, "mowing_direction_mode must be at most 2"),
        (
            "mowing_direction_degrees",
            181,
            "mowing_direction_degrees must be at most 180",
        ),
        ("edge_mowing_walk_mode", 2, "edge_mowing_walk_mode must be at most 1"),
    ],
)
def test_plan_mowing_preference_update_schema_rejects_unknown_modes(
    field: str,
    value: int,
    message: str,
) -> None:
    with pytest.raises(vol.Invalid, match=message):
        PLAN_MOWING_PREFERENCE_UPDATE_SCHEMA(
            {
                "map_index": 0,
                field: value,
            }
        )


def test_plan_mowing_preference_update_schema_accepts_mode_only_request() -> None:
    parsed = PLAN_MOWING_PREFERENCE_UPDATE_SCHEMA(
        {
            "map_index": "1",
            "preference_mode": "custom",
        }
    )

    assert parsed == {
        "map_index": 1,
        "preference_mode": 1,
        "execute": False,
        "confirm_preference_write": False,
    }


def test_plan_maintenance_reset_schema_normalizes_aliases() -> None:
    parsed = PLAN_MAINTENANCE_RESET_SCHEMA(
        {
            "item": "cleaning brush",
        }
    )

    assert parsed == {
        "item": "brush",
        "execute": False,
        "confirm_maintenance_reset": False,
    }


def test_plan_schedule_upload_schema_accepts_readable_plans() -> None:
    parsed = PLAN_SCHEDULE_UPLOAD_SCHEMA(
        {
            "map_index": "0",
            "plans": [
                {
                    "plan_id": 0,
                    "enabled": True,
                    "name": "",
                    "weeks": [
                        {
                            "week_day": 0,
                            "tasks": [
                                {
                                    "type": 0,
                                    "start": 658,
                                    "end": 1257,
                                    "real_end": 802,
                                    "regions": [],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    assert parsed == {
        "map_index": 0,
        "plans": [
            {
                "plan_id": 0,
                "enabled": True,
                "name": "",
                "weeks": [
                    {
                        "week_day": 0,
                        "week_day_name": "sun",
                        "tasks": [
                            {
                                "type": 0,
                                "type_name": "all_area_mowing",
                                "cyclic": False,
                                "start": 658,
                                "start_time": "10:58",
                                "end": 1257,
                                "end_time": "20:57",
                                "real_end": 802,
                                "real_end_time": "13:22",
                                "regions": [],
                            }
                        ],
                    }
                ],
            }
        ],
        "chunk_size": 100,
        "execute": False,
        "confirm_schedule_write": False,
    }


def test_plan_schedule_upload_schema_rejects_invalid_plans() -> None:
    with pytest.raises(vol.Invalid, match="plan_id must be an integer"):
        PLAN_SCHEDULE_UPLOAD_SCHEMA(
            {
                "map_index": 0,
                "plans": [{"enabled": True}],
            }
        )


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


def test_preference_write_guard_blocks_execute_without_confirmation() -> None:
    call = SimpleNamespace(
        data={
            "execute": True,
            "confirm_preference_write": False,
        }
    )

    with pytest.raises(HomeAssistantError, match="confirm_preference_write"):
        _guard_preference_write_request(call)


def test_preference_write_guard_allows_dry_run_without_confirmation() -> None:
    call = SimpleNamespace(
        data={
            "execute": False,
            "confirm_preference_write": False,
        }
    )

    _guard_preference_write_request(call)


def test_maintenance_reset_guard_blocks_execute_without_confirmation() -> None:
    call = SimpleNamespace(
        data={
            "execute": True,
            "confirm_maintenance_reset": False,
        }
    )

    with pytest.raises(HomeAssistantError, match="confirm_maintenance_reset"):
        _guard_maintenance_reset_request(call)


def test_maintenance_reset_guard_allows_dry_run_without_confirmation() -> None:
    call = SimpleNamespace(
        data={
            "execute": False,
            "confirm_maintenance_reset": False,
        }
    )

    _guard_maintenance_reset_request(call)


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
            "mowing_direction_degrees": 35,
            "edge_mowing_auto": False,
            "edge_cutting_attachment": True,
        }
    )

    assert _preference_change_request(call) == {
        "mowing_height_cm": 5.0,
        "mowing_direction_degrees": 35,
        "edge_mowing_auto": False,
        "edge_cutting_attachment": True,
    }


def test_preference_change_request_extracts_preference_mode() -> None:
    call = SimpleNamespace(
        data={
            "map_index": 0,
            ATTR_PREFERENCE_MODE: 0,
        }
    )

    assert _preference_change_request(call) == {
        ATTR_PREFERENCE_MODE: 0,
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


def test_schedule_write_notification_summarizes_upload_plan() -> None:
    title, message = _schedule_write_notification(
        {
            "action": "upload_schedule_plans",
            "executed": False,
            "changed": True,
            "map_index": 0,
            "version": 19383,
            "schedule": {"label": "map_0"},
            "target_schedule": {"plan_count": 2, "enabled_plan_count": 1},
            "payload_size": 70,
            "chunk_count": 1,
            "request": {
                "sequence": [
                    {"m": "s", "t": "SCHDIV2", "d": {"i": 0, "l": 70, "v": 19383}},
                    {"m": "s", "t": "SCHDDV2", "d": {"s": 0, "l": 70, "v": 19383}},
                ]
            },
        }
    )

    assert title == "Dreame Lawn Mower Schedule Dry Run"
    assert "Built dry-run schedule upload for map_0" in message
    assert "plans=2, enabled=1 (will change)" in message
    assert "payload_size=70, chunk_count=1" in message
    assert '"t": "SCHDIV2"' in message


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


def test_maintenance_reset_notification_summarizes_candidate_request() -> None:
    title, message = _maintenance_reset_notification(
        {
            "executed": False,
            "changed": True,
            "item": "blade",
            "item_name": "Blade",
            "previous_item": {"used_minutes": 4896},
            "updated_item": {"used_minutes": 0},
            "request": {
                "m": "s",
                "t": "CMS",
                "d": {"value": [0, 16752, 6849, -1]},
            },
        }
    )

    assert title == "Dreame Lawn Mower Maintenance Reset Dry Run"
    assert "Built dry-run maintenance reset for Blade" in message
    assert "counter 4896 -> 0 (will change)" in message
    assert '"t": "CMS"' in message


def test_maintenance_reset_notification_summarizes_executed_request() -> None:
    title, message = _maintenance_reset_notification(
        {
            "executed": True,
            "changed": False,
            "item": "robot",
            "item_name": "Robot Maintenance",
            "previous_item": {"used_minutes": 0},
            "updated_item": {"used_minutes": 0},
            "request": {
                "m": "s",
                "t": "CMS",
                "d": {"value": [4896, 16752, 0, -1]},
            },
            "response_data": {"ok": True},
        }
    )

    assert title == "Dreame Lawn Mower Maintenance Reset"
    assert "Sent maintenance reset for Robot Maintenance" in message
    assert "counter 0 -> 0 (was already reset)" in message
    assert 'Response: `{"ok": true}`' in message


def test_mowing_preference_notification_summarizes_mode_only_request() -> None:
    title, message = _mowing_preference_notification(
        {
            "map_index": 1,
            "area_id": None,
            "mode_name": "global",
            "target_mode_name": "custom",
            "changed_fields": ["preference_mode"],
            "changed": True,
            "request_candidate": {
                "m": "s",
                "t": "PREP",
                "d": {"idx": 1, "value": 1},
            },
        }
    )

    assert title == "Dreame Lawn Mower Preference Dry Run"
    assert "Built dry-run mowing preference update for map 1" in message
    assert "mode global -> custom" in message
    assert "changed_fields=preference_mode" in message
    assert '"t": "PREP"' in message


def test_mowing_preference_notification_summarizes_executed_request() -> None:
    title, message = _mowing_preference_notification(
        {
            "executed": True,
            "changed": False,
            "map_index": 0,
            "area_id": 11,
            "mode_name": "custom",
            "changed_fields": [],
            "previous_preference": {"mowing_height_cm": 4.0},
            "updated_preference": {"mowing_height_cm": 4.0},
            "request_candidate": {
                "m": "s",
                "t": "PRE",
                "d": [8, 0, 11],
            },
            "response_data": {"r": 0, "ok": True},
        }
    )

    assert title == "Dreame Lawn Mower Preference Updated"
    assert "Sent mowing preference update for map 0 area 11" in message
    assert "height 4.0 -> 4.0 (was already matched)" in message
    assert 'Response: `{"ok": true, "r": 0}`' in message


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


def test_remote_control_guard_blocks_retained_connectivity_state() -> None:
    coordinator = _coordinator(_snapshot())
    coordinator.connection_degraded = True

    with pytest.raises(HomeAssistantError, match="connection is recovering"):
        _guard_remote_control_step(coordinator)


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
