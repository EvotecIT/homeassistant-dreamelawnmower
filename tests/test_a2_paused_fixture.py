"""Fixture-driven checks for the paused A2 diagnostics capture."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.dreame_lawn_mower.binary_sensor import BINARY_SENSORS
from custom_components.dreame_lawn_mower.sensor import SENSORS

from .fixture_data import load_json_fixture


def _snapshot() -> SimpleNamespace:
    payload = load_json_fixture("a2_paused_diagnostics.json")
    return SimpleNamespace(**payload["data"]["snapshot"])


def test_a2_paused_fixture_has_expected_snapshot_values() -> None:
    snapshot = _snapshot()

    assert snapshot.state == "paused"
    assert snapshot.activity == "paused"
    assert snapshot.battery_level == 76
    assert snapshot.firmware_version == "4.3.6_0320"
    assert snapshot.hardware_version == "Linux"
    assert snapshot.cleaning_mode_name == "unknown"
    assert snapshot.state_name == "paused"
    assert snapshot.task_status_name == "unknown"
    assert snapshot.child_lock is None
    assert snapshot.capabilities == [
        "lidar_navigation",
        "disable_sensor_cleaning",
        "map",
    ]
    assert snapshot.raw_attributes["mower_state"] == "paused"
    assert snapshot.raw_attributes["running"] is False


def test_a2_paused_fixture_exposes_expected_sensor_set() -> None:
    snapshot = _snapshot()

    visible = {
        description.name
        for description in SENSORS
        if description.exists_fn(snapshot)
    }

    assert visible == {
        "Battery",
        "Error",
        "Error Code",
        "Firmware Version",
        "Hardware Version",
        "State Name",
        "Task Status",
        "Serial Number",
        "Cloud Update Time",
        "Unknown Property Count",
        "Realtime Property Count",
        "Last Realtime Method",
        "Mower State",
        "Raw Error",
    }


def test_a2_paused_fixture_reports_expected_normalized_sensor_values() -> None:
    snapshot = _snapshot()
    sensors = {description.name: description for description in SENSORS}

    assert sensors["State Name"].value_fn(snapshot) == "paused"
    assert sensors["Task Status"].value_fn(snapshot) == "unknown"
    assert sensors["Error Code"].value_fn(snapshot) == "none"
    assert sensors["Raw Error"].value_fn(snapshot) == "none"


def test_a2_paused_fixture_keeps_optional_entities_opt_in() -> None:
    sensors = {description.name: description for description in SENSORS}
    binary_sensors = {description.name: description for description in BINARY_SENSORS}

    assert sensors["Cleaning Mode"].entity_registry_enabled_default is False
    assert binary_sensors["Child Lock"].entity_registry_enabled_default is False
    assert binary_sensors["Shortcut Task"].entity_registry_enabled_default is False


def test_a2_paused_fixture_exposes_expected_binary_sensor_set() -> None:
    snapshot = _snapshot()

    visible = {
        description.name
        for description in BINARY_SENSORS
        if description.exists_fn(snapshot)
    }

    assert visible == {
        "Docked",
        "Error Active",
        "Online",
        "Mowing",
        "Paused",
        "Returning",
        "Charging",
        "Task Active",
        "Mapping Available",
        "Scheduled Task",
        "Raw Paused Flag",
        "Raw Running Flag",
        "Raw Returning Flag",
    }


def test_a2_paused_fixture_reports_expected_normalized_state_flags() -> None:
    snapshot = _snapshot()
    sensors = {description.name: description for description in BINARY_SENSORS}

    assert sensors["Error Active"].value_fn(snapshot) is False
    assert sensors["Docked"].value_fn(snapshot) is False
    assert sensors["Paused"].value_fn(snapshot) is True
    assert sensors["Mowing"].value_fn(snapshot) is False
    assert sensors["Returning"].value_fn(snapshot) is False
