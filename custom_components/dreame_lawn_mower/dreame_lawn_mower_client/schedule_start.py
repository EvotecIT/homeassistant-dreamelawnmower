"""Eligibility policy for unattended starts with known, current evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def scheduled_start_block_reason(
    snapshot: Any,
    weather: Mapping[str, Any],
    native: Mapping[str, Any],
    *,
    minimum_battery: int = 30,
    map_index: int | None = None,
) -> str | None:
    """Return a stable skip reason, retaining unknown as a distinct state."""
    if not isinstance(minimum_battery, int) or not 20 <= minimum_battery <= 100:
        raise ValueError("minimum_battery must be an integer between 20 and 100.")
    if getattr(snapshot, "online", None) is not True:
        return "connectivity_unknown_or_offline"
    if getattr(snapshot, "docked", None) is not True:
        return "not_docked"
    if getattr(snapshot, "task_resumable", None) is not False:
        return "resumable_or_unknown_task"
    if getattr(snapshot, "mowing_session_active", None) is not False:
        return "active_or_unknown_task"
    if getattr(snapshot, "error_code", None) != 0:
        return "fault_or_unknown_fault_state"
    if getattr(snapshot, "child_lock", None) is True:
        return "child_lock_enabled"
    battery = getattr(snapshot, "battery_level", None)
    if not isinstance(battery, (int, float)) or isinstance(battery, bool):
        return "battery_unknown"
    if not minimum_battery <= battery <= 100:
        return "battery_below_threshold_or_invalid"
    if (
        weather.get("rain_protect_end_time_present") is not True
        or weather.get("rain_protection_active") is None
        or weather.get("errors")
        or weather.get("warnings")
    ):
        return "rain_delay_unknown"
    if weather["rain_protection_active"] is not False:
        return "rain_delay_active"
    indices = native.get("map_indices")
    if native.get("map_inventory_valid") is not True or not indices:
        return "native_schedule_inventory_unknown"
    current = native.get("current_map_index")
    if current not in indices:
        return "current_map_unknown"
    if map_index is not None and map_index != current:
        return "requested_map_not_current"
    schedules = {
        entry.get("idx"): entry
        for entry in native.get("schedules", [])
        if isinstance(entry, Mapping)
    }
    required = list(indices)
    if not all(
        schedules.get(index, {}).get("protocol") == "tables" for index in indices
    ):
        required.append(-1)
    for index in required:
        entry = schedules.get(index, {})
        plans = entry.get("plans")
        if (
            entry.get("error")
            or entry.get("read_status") != "complete"
            or not isinstance(plans, list)
        ):
            return "native_schedule_state_unknown"
        if any(plan.get("enabled") is not False for plan in plans):
            return "native_schedule_enabled_or_unknown"
    return None
