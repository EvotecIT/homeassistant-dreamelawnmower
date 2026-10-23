"""Bounded private state for one explicitly requested managed mowing run."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

MANAGED_FIELDS: dict[Any, Any] = {
    vol.Optional("permission_entity", default=""): vol.Any("", cv.entity_id),
    vol.Optional("permission_max_age", default=1800): vol.All(
        vol.Coerce(int),
        vol.Range(min=0, max=86400),
    ),
    vol.Optional("defer_minutes", default=0): vol.All(
        vol.Coerce(int),
        vol.Range(min=0, max=1440),
    ),
    vol.Optional("interruption", default="none"): vol.In(["none", "pause", "dock"]),
    vol.Optional("resume_when_allowed", default=False): cv.boolean,
    vol.Optional("recovery_minutes", default=0): vol.All(
        vol.Coerce(int),
        vol.Range(min=0, max=1440),
    ),
    vol.Optional("automation_entity", default=""): vol.Any(
        "", cv.entity_domain("automation"), cv.entity_domain("switch")
    ),
    vol.Optional("run_id", default=""): vol.All(str, vol.Length(max=128)),
}

TASK_SCHEMA = vol.Schema(
    {
        vol.Required("task_type"): vol.In(["all", "zone", "edge", "spot"]),
        vol.Required("minimum_battery"): vol.All(int, vol.Range(min=20, max=100)),
        vol.Optional("map_index"): vol.All(int, vol.Range(min=0)),
        vol.Optional("zone_ids"): [vol.All(int, vol.Range(min=0, max=65535))],
        vol.Optional("spot_ids"): [vol.All(int, vol.Range(min=0, max=65535))],
        vol.Optional("contour_ids"): [[vol.All(int, vol.Range(min=0, max=65535))]],
        **MANAGED_FIELDS,
    }
)

PHASES = {
    "waiting",
    "dispatching",
    "observing",
    "running",
    "pausing",
    "paused",
    "resuming",
    "confirming_resume",
}


def decode_managed_record(value: Any, now: datetime) -> dict[str, Any] | None:
    """Restore only known scalar state; dispatched commands are never replayed."""
    if not isinstance(value, dict) or set(value) != {
        "params",
        "run_id",
        "map_index",
        "baseline_task_id",
        "task_id",
        "phase",
        "created_at",
        "start_deadline",
        "recovery_deadline",
        "command_at",
    }:
        return None
    try:
        params = TASK_SCHEMA(value["params"])
        if params != value["params"] or value["phase"] not in PHASES:
            return None
        if not isinstance(value["run_id"], str) or not 1 <= len(value["run_id"]) <= 128:
            return None
        if type(value["map_index"]) is not int or not 0 <= value["map_index"] <= 65535:
            return None
        for key in ("task_id", "baseline_task_id"):
            if value[key] is not None and (
                type(value[key]) is not int or not 0 <= value[key] < 2**64
            ):
                return None
        created = datetime.fromisoformat(value["created_at"])
        if created.tzinfo is None or not 0 <= (now - created).total_seconds() <= 86400:
            return None
        for key in ("start_deadline", "recovery_deadline", "command_at"):
            if value[key] is None:
                continue
            instant = datetime.fromisoformat(value[key])
            if (
                instant.tzinfo is None
                or not 0 <= (instant - created).total_seconds() <= 86400
            ):
                return None
        return dict(value)
    except (vol.Invalid, TypeError, ValueError, OverflowError):
        return None
