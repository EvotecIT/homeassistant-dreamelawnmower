"""Fixture-driven checks for the paused A2 wheel-speed error capture."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.dreame_lawn_mower.binary_sensor import BINARY_SENSORS
from custom_components.dreame_lawn_mower.sensor import SENSORS

from .fixture_data import load_json_fixture


def _snapshot() -> SimpleNamespace:
    payload = load_json_fixture("a2_paused_left_wheel_error_diagnostics.json")
    return SimpleNamespace(**payload["data"]["snapshot"])


def test_a2_error_fixture_preserves_paused_state_context() -> None:
    snapshot = _snapshot()

    assert snapshot.state == "paused"
    assert snapshot.activity == "error"
    assert snapshot.battery_level == 74
    assert snapshot.error_code == 31
    assert snapshot.error_name == "left_wheell_speed"
    assert snapshot.error_text == "Left wheell speed"
    assert snapshot.error_display == "Left wheel speed"
    assert snapshot.raw_attributes["mower_state"] == "paused"
    assert snapshot.raw_attributes["running"] is False
    assert snapshot.started is True


def test_a2_error_fixture_exposes_expected_sensor_set() -> None:
    snapshot = _snapshot()

    visible = {
        description.name
        for description in SENSORS
        if description.exists_fn(snapshot)
    }

    assert visible == {
        "Battery",
        "Cloud Update Time",
        "Error",
        "Error Code",
        "Firmware Version",
        "Hardware Version",
        "Last Realtime Method",
        "Mower State",
        "Raw Error",
        "Realtime Property Count",
        "Serial Number",
        "Unknown Property Count",
    }


def test_a2_error_fixture_exposes_expected_binary_sensor_set() -> None:
    snapshot = _snapshot()

    visible = {
        description.name
        for description in BINARY_SENSORS
        if description.exists_fn(snapshot)
    }

    assert visible == {
        "Charging",
        "Mapping Available",
        "Online",
        "Raw Paused Flag",
        "Raw Returning Flag",
        "Raw Running Flag",
        "Scheduled Task",
        "Task Active",
    }
