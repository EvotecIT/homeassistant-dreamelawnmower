"""Persisted inputs for the optional, UI-configured mowing automation."""

from __future__ import annotations

import math
from typing import Any

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

CONF_MOWING_AUTOMATION = "mowing_automation"

AUTOMATION_SCHEMA = vol.Schema(
    {
        vol.Optional("enabled", default=False): cv.boolean,
        vol.Optional("schedule_entity", default=""): vol.Any(
            "", cv.entity_domain("schedule")
        ),
        vol.Optional("soil_entity", default=""): vol.Any(
            "", cv.entity_domain("sensor")
        ),
        vol.Optional("wet_threshold", default=65): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional("dry_threshold", default=55): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
        vol.Optional("drying_minutes", default=30): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=1440)
        ),
        vol.Optional("source_max_age", default=1800): vol.All(
            vol.Coerce(int), vol.Range(min=60, max=86400)
        ),
        vol.Optional("irrigation_entity", default=""): vol.Any("", cv.entity_id),
        vol.Optional("settling_minutes", default=15): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=1440)
        ),
        vol.Optional("permission_entity", default=""): vol.Any("", cv.entity_id),
        vol.Optional("minimum_battery", default=30): vol.All(
            vol.Coerce(int), vol.Range(min=20, max=100)
        ),
        vol.Optional("defer_minutes", default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=1440)
        ),
        vol.Optional("interruption", default="none"): vol.In(["none", "pause", "dock"]),
        vol.Optional("resume_when_allowed", default=False): cv.boolean,
        vol.Optional("recovery_minutes", default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=1440)
        ),
        vol.Optional("notify_outcome", default=True): cv.boolean,
        vol.Optional("native_plans_disabled", default=False): cv.boolean,
    }
)


def automation_config(value: Any) -> dict[str, Any]:
    """Validate stored inputs and fail closed on invalid or incompatible state."""
    if not isinstance(value, dict):
        return {}
    try:
        result: dict[str, Any] = AUTOMATION_SCHEMA(value)
        if not all(
            math.isfinite(result[key]) for key in ("dry_threshold", "wet_threshold")
        ):
            return {}
        if result["dry_threshold"] >= result["wet_threshold"]:
            return {}
        if result["resume_when_allowed"] and (
            result["interruption"] != "pause" or not result["recovery_minutes"]
        ):
            return {}
        if result["enabled"] and (
            not result["schedule_entity"] or not result["native_plans_disabled"]
        ):
            return {}
        for key in ("irrigation_entity", "permission_entity"):
            if result[key] and result[key].split(".", 1)[0] not in {
                "binary_sensor",
                "switch",
                "input_boolean",
            }:
                return {}
        return result
    except (vol.Invalid, TypeError, ValueError, OverflowError):
        return {}
