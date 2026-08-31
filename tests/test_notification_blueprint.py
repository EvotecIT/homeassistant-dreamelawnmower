"""Validation for the importable mower notification blueprint."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.automation.config import (
    AUTOMATION_BLUEPRINT_SCHEMA,
)
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.util.yaml.loader import load_yaml

BLUEPRINT_PATH = (
    Path(__file__).parents[1]
    / "blueprints"
    / "automation"
    / "dreame_lawn_mower"
    / "mower_condition_notifications.yaml"
)
EXAMPLE_INPUTS = {
    "mower_entity": "lawn_mower.garden",
    "fault_entity": "binary_sensor.garden_error_active",
    "error_entity": "sensor.garden_error",
    "status_notice_entity": "sensor.garden_status_notice",
    "online_entity": "binary_sensor.garden_online",
    "maintenance_warning_entity": "binary_sensor.garden_maintenance_warning",
    "maintenance_due_entity": "binary_sensor.garden_maintenance_due",
}


def _blueprint() -> Blueprint:
    return Blueprint(
        load_yaml(BLUEPRINT_PATH),
        path=str(BLUEPRINT_PATH),
        expected_domain="automation",
        schema=AUTOMATION_BLUEPRINT_SCHEMA,
    )


def _substituted_automation(overrides: dict | None = None) -> dict:
    selected_inputs = dict(EXAMPLE_INPUTS)
    if overrides:
        selected_inputs.update(overrides)
    inputs = BlueprintInputs(
        _blueprint(),
        {
            "use_blueprint": {
                "path": str(BLUEPRINT_PATH),
                "input": selected_inputs,
            }
        },
    )
    return inputs.async_substitute()


def test_blueprint_schema_and_defaults_are_importable() -> None:
    blueprint = _blueprint()

    assert blueprint.domain == "automation"
    assert set(EXAMPLE_INPUTS) <= set(blueprint.inputs)
    assert _substituted_automation()["mode"] == "parallel"


def test_blueprint_never_issues_automatic_mower_controls() -> None:
    source = BLUEPRINT_PATH.read_text(encoding="utf-8")

    assert "action: lawn_mower." not in source
