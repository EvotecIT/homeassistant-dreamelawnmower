"""Sparse mower state must remain readable before all properties arrive."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import Mock

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device import (
    DreameMowerDevice,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMapBackupStatus,
    DreameMapRecoveryStatus,
    DreameMowerAction,
    DreameMowerProperty,
    DreameMowerStreamStatus,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    InvalidActionException,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.map_types import (
    CleaningHistory,
)


def test_capability_refresh_keeps_sparse_status_readable() -> None:
    mower = DreameMowerDevice("Mower", None, None)
    mower.data[DreameMowerProperty.OFF_PEAK_CHARGING.value] = "{}"
    mower.capability.refresh({})

    assert mower.status.off_peak_charging is False
    assert mower.status.job["completed"] is False
    assert mower.status.attributes is not None
    assert mower.status.cleaning_route_name == "unknown"


def test_job_keeps_cleaning_mode_unknown_before_the_property_arrives() -> None:
    mower = DreameMowerDevice("Mower", None, None)
    mower.data[DreameMowerProperty.AUTO_SWITCH_SETTINGS.value] = "[]"
    mower.capability.refresh({})

    assert mower.capability.custom_cleaning_mode is True
    assert mower.status.job["cleaning_mode"] == "unknown"


@pytest.mark.parametrize("stream", ["cleaning", "cruising"])
def test_history_projection_omits_undated_entries_and_keeps_valid_ones(stream) -> None:
    mower = DreameMowerDevice("Mower", None, None)
    incomplete = CleaningHistory([], mower.property_mapping)
    valid = CleaningHistory([], mower.property_mapping)
    valid.date = datetime(2026, 10, 1, 10, 15)
    valid.cleaning_time = 12
    setattr(mower.status, f"_{stream}_history", [incomplete, valid])

    projected = getattr(mower.status, f"{stream}_history")

    assert len(projected) == 1
    assert projected["10-01 10:15"][f"{stream}_time"] == "12 min"


@pytest.mark.parametrize("operation,prop,status", [
    ("recovery", DreameMowerProperty.MAP_RECOVERY_STATUS, DreameMapRecoveryStatus),
    ("backup", DreameMowerProperty.MAP_BACKUP_STATUS, DreameMapBackupStatus),
])
def test_map_completion_refreshes_maps_and_status(operation, prop, status) -> None:
    mower = DreameMowerDevice("Mower", None, None)
    manager = mower._map_manager
    manager.request_next_map = Mock()
    manager.request_next_recovery_map_list = Mock()
    mower.data[prop.value] = status.SUCCESS.value
    mower._request_properties_plan = Mock(return_value=iter(()))

    list(getattr(mower, f"_map_{operation}_status_changed_plan")(status.RUNNING.value))

    mower._request_properties_plan.assert_called_once_with([prop])
    manager.request_next_recovery_map_list.assert_called_once_with()
    assert manager.request_next_map.call_count == (1 if operation == "recovery" else 0)


@pytest.mark.parametrize("value,expected", [
    (79, 79), ("79", 79), (None, None), ("unknown", None),
])
def test_numeric_status_preserves_unknown_instead_of_inventing_zero(
    value, expected,
) -> None:
    mower = DreameMowerDevice("Mower", None, None)
    mower.data[DreameMowerProperty.BATTERY_LEVEL.value] = value
    mower.data[DreameMowerProperty.BLADES_LEFT.value] = value

    assert mower.status.battery_level == expected
    assert mower.status.blades_life == expected


@pytest.mark.parametrize("value,expected", [
    ({"CMS": [120]}, 29880), ({"CMS": ["120"]}, 29880),
    (None, None), ({}, None), ({"CMS": []}, None), ({"CMS": ["unknown"]}, None),
])
def test_lensbrush_projection_tolerates_missing_consumable_data(
    value, expected,
) -> None:
    mower = DreameMowerDevice("Mower", None, None)
    mower.data[DreameMowerProperty.LENSBRUSH_LEFT.value] = value

    assert mower.status.lensbrush_life == expected


@pytest.mark.parametrize("operation,args", [
    ("go_to", (10, 20)), ("follow_path", ([],)), ("start_fast_mapping", ()),
])
def test_navigation_waits_for_battery_data_before_dispatch(operation, args) -> None:
    mower = DreameMowerDevice("Mower", None, None)
    mower.data[DreameMowerProperty.CRUISE_SCHEDULE.value] = "[]"
    mower.status.stream_status = DreameMowerStreamStatus.IDLE
    mower.call_action = Mock()
    mower.schedule_update = Mock()

    with pytest.raises(InvalidActionException, match="Battery level is unavailable"):
        getattr(mower, operation)(*args)

    mower.call_action.assert_not_called()
    mower.schedule_update.assert_not_called()


@pytest.mark.parametrize("action", [
    DreameMowerAction.RESET_BLADES, DreameMowerAction.RESET_SIDE_BRUSH,
    DreameMowerAction.RESET_FILTER, DreameMowerAction.RESET_SENSOR,
    DreameMowerAction.RESET_TANK_FILTER, DreameMowerAction.RESET_SILVER_ION,
    DreameMowerAction.RESET_LENSBRUSH, DreameMowerAction.RESET_SQUEEGEE,
])
def test_legacy_reset_rejects_unknown_life_without_dispatch(action) -> None:
    mower = DreameMowerDevice("Mower", None, None)
    mower._protocol.action = Mock()
    mower.schedule_update = Mock()

    with pytest.raises(InvalidActionException, match="unavailable"):
        mower.call_action(action)

    mower._protocol.action.assert_not_called()


def test_legacy_reset_dispatches_a_known_used_counter_and_updates_cache() -> None:
    mower = DreameMowerDevice("Mower", None, None)
    mower.data[DreameMowerProperty.BLADES_LEFT.value] = 50
    mower._protocol.action = Mock(return_value={"code": 0})
    mower.schedule_update = Mock()

    assert mower.call_action(DreameMowerAction.RESET_BLADES) == {"code": 0}

    mapping = mower.action_mapping[DreameMowerAction.RESET_BLADES]
    mower._protocol.action.assert_called_once_with(
        mapping["siid"], mapping["aiid"], None,
    )
    assert mower.status.blades_life == 100


def test_legacy_reset_rejects_an_unused_counter_without_dispatch() -> None:
    mower = DreameMowerDevice("Mower", None, None)
    mower.data[DreameMowerProperty.BLADES_LEFT.value] = 100
    mower._protocol.action = Mock()
    mower.schedule_update = Mock()

    with pytest.raises(InvalidActionException, match="unavailable"):
        mower.call_action(DreameMowerAction.RESET_BLADES)

    mower._protocol.action.assert_not_called()


@pytest.mark.parametrize("parameters", [None, {"custom": "retained"}])
def test_legacy_lensbrush_reset_prepares_default_parameters(parameters) -> None:
    mower = DreameMowerDevice("Mower", None, None)
    cms = {"CMS": [29950, 7, 9], "extra": "retained"}
    mower.data[DreameMowerProperty.LENSBRUSH_LEFT.value] = cms
    mower._protocol.action = Mock(return_value={"code": 0})
    mower.schedule_update = Mock()

    assert mower.call_action(
        DreameMowerAction.RESET_LENSBRUSH, parameters,
    ) == {"code": 0}

    sent = mower._protocol.action.call_args.args[2]
    assert sent["in"] == {"CMS": {"type": "set", "value": [1, 0, 1]}}
    if parameters is not None:
        assert sent["custom"] == "retained"
    assert mower.data[DreameMowerProperty.LENSBRUSH_LEFT.value] is cms
    assert mower.status.lensbrush_life == 50
    mower.schedule_update.assert_called_with(6, bool(mower._protocol.dreame_cloud))


def test_legacy_lensbrush_reset_rejects_other_parameter_shapes_before_dispatch(
) -> None:
    mower = DreameMowerDevice("Mower", None, None)
    mower.data[DreameMowerProperty.LENSBRUSH_LEFT.value] = {"CMS": [29950]}
    mower._protocol.action = Mock()
    mower.schedule_update = Mock()

    with pytest.raises(InvalidActionException, match="parameters must be a dictionary"):
        mower.call_action(DreameMowerAction.RESET_LENSBRUSH, [])

    mower._protocol.action.assert_not_called()
