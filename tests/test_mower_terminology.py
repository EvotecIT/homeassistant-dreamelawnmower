"""Contracts for mower-native public terminology and legacy aliases."""

from __future__ import annotations

import pytest

from custom_components.dreame_lawn_mower.binary_sensor import BINARY_SENSORS
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.const import (
    ACTION_TO_NAME,
    CONSUMABLE_TO_LIFE_WARNING_DESCRIPTION,
    PROPERTY_TO_NAME,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
    DreameLawnMowerSnapshot,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.types import (
    DreameMowerAction,
    DreameMowerProperty,
)
from custom_components.dreame_lawn_mower.sensor import SENSORS


def _snapshot(**overrides: object) -> DreameLawnMowerSnapshot:
    values: dict[str, object] = {
        "descriptor": DreameLawnMowerDescriptor(
            did="mower-1",
            name="Garden Mower",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
        "available": True,
        "state": "spot_cleaning",
        "state_name": "spot_cleaning",
        "activity": "mowing",
        "task_status": "zone_cleaning_paused",
        "task_status_name": "zone_cleaning_paused",
        "scheduled_clean": True,
        "cleaning_mode": 0,
        "cleaning_mode_name": "sweeping",
        "cleaned_area": 42.5,
        "cleaning_time": 18,
    }
    values.update(overrides)
    return DreameLawnMowerSnapshot(**values)


def _sensor(key: str):
    return next(description for description in SENSORS if description.key == key)


def _binary_sensor(key: str):
    return next(description for description in BINARY_SENSORS if description.key == key)


def test_snapshot_exposes_mower_names_without_removing_legacy_fields() -> None:
    snapshot = _snapshot()

    assert snapshot.mower_state == "spot_mowing"
    assert snapshot.mower_state_name == "spot_mowing"
    assert snapshot.mowing_task_status == "zone_mowing_paused"
    assert snapshot.mowing_task_status_name == "zone_mowing_paused"
    assert snapshot.scheduled_mow is True
    assert snapshot.mowing_mode == 0
    assert snapshot.mowing_mode_name == "mowing"
    assert snapshot.mowed_area == 42.5
    assert snapshot.mowing_time == 18

    assert snapshot.state == "spot_cleaning"
    assert snapshot.task_status == "zone_cleaning_paused"
    assert snapshot.scheduled_clean is True
    assert snapshot.cleaning_mode == 0
    assert snapshot.cleaning_mode_name == "sweeping"
    assert snapshot.cleaned_area == 42.5
    assert snapshot.cleaning_time == 18


@pytest.mark.parametrize(
    ("legacy", "mower_native"),
    [
        ("cleaning", "mowing"),
        ("auto_cleaning", "mowing"),
        ("auto_cleaning_paused", "mowing_paused"),
        ("follow_wall_cleaning", "edge_mowing"),
        ("summon_clean_paused", "summon_mow_paused"),
        ("curising_point", "cruising_point"),
    ],
)
def test_snapshot_normalizes_state_and_task_vocabulary(
    legacy: str,
    mower_native: str,
) -> None:
    snapshot = _snapshot(
        state=legacy,
        state_name=legacy,
        task_status=legacy,
        task_status_name=legacy,
    )

    assert snapshot.mower_state == mower_native
    assert snapshot.mower_state_name == mower_native
    assert snapshot.mowing_task_status == mower_native
    assert snapshot.mowing_task_status_name == mower_native


def test_home_assistant_labels_change_without_changing_entity_keys() -> None:
    snapshot = _snapshot()

    mode = _sensor("cleaning_mode")
    area = _sensor("current_cleaned_area")
    duration = _sensor("current_cleaning_time")

    assert (mode.name, mode.value_fn(snapshot)) == ("Mowing Mode", "mowing")
    assert (area.name, area.value_fn(snapshot)) == ("Current Mowed Area", 42.5)
    assert (duration.name, duration.value_fn(snapshot)) == (
        "Current Mowing Time",
        18,
    )
    assert _sensor("state_name").value_fn(snapshot) == "spot_mowing"
    assert _sensor("task_status").value_fn(snapshot) == "zone_mowing_paused"
    assert _binary_sensor("scheduled_task").value_fn(snapshot) is True


def test_protocol_metadata_uses_mower_labels_for_confirmed_mower_parts() -> None:
    assert PROPERTY_TO_NAME[DreameMowerProperty.CLEANING_MODE.name][1] == "Mowing Mode"
    assert PROPERTY_TO_NAME[DreameMowerProperty.CLEANED_AREA.name][1] == "Mowed Area"
    assert PROPERTY_TO_NAME[DreameMowerProperty.CLEANING_TIME.name][1] == "Mowing Time"
    assert PROPERTY_TO_NAME[DreameMowerProperty.BLADES_LEFT.name][1] == "Blades Left"
    assert ACTION_TO_NAME[DreameMowerAction.RESET_BLADES][1] == "Reset Blades"
    warnings = CONSUMABLE_TO_LIFE_WARNING_DESCRIPTION[
        DreameMowerProperty.BLADES_LEFT
    ]
    assert warnings[0][0] == "Blades must be replaced"
    assert warnings[1][0] == "Blades need to be replaced soon"
