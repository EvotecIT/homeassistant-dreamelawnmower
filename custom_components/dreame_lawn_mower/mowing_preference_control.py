"""Shared Home Assistant control path for map and zone mowing preferences."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .control_options import current_map_index, current_zone_entries, map_entries
from .coordinator import DreameLawnMowerCoordinator
from .dreame_lawn_mower_client.mowing_preferences import (
    MOWING_PREFERENCE_MODE_FIELD,
)
from .sensor_map_data import (
    _selected_map_preference_summary,
    _selected_zone_preference_summary,
)

MOWING_HEIGHT_MIN_CM = 3.5
MOWING_HEIGHT_MAX_CM = 6.0
MOWING_HEIGHT_STEP_CM = 0.5

PREFERENCE_MODE_GLOBAL = "Global"
PREFERENCE_MODE_CUSTOM = "Custom"
PREFERENCE_MODE_OPTIONS = [PREFERENCE_MODE_GLOBAL, PREFERENCE_MODE_CUSTOM]


def selected_map_preference_mode(coordinator: Any) -> str | None:
    """Return the selected map preference mode as a Home Assistant option."""
    mode_name = _selected_map_preference_summary(coordinator).get("mode_name")
    if not isinstance(mode_name, str):
        return None
    normalized = mode_name.strip().casefold()
    if normalized == "global":
        return PREFERENCE_MODE_GLOBAL
    if normalized == "custom":
        return PREFERENCE_MODE_CUSTOM
    return None


def selected_zone_mowing_height(coordinator: Any) -> float | None:
    """Return the selected zone mowing height in centimeters."""
    value = _selected_zone_preference_summary(coordinator).get("mowing_height_cm")
    return float(value) if isinstance(value, int | float) else None


def selected_zone_preference_attributes(coordinator: Any) -> dict[str, Any]:
    """Return the selected zone preference summary for control state attributes."""
    return _selected_zone_preference_summary(coordinator)


async def async_update_selected_mowing_preference(
    coordinator: DreameLawnMowerCoordinator,
    *,
    changes: Mapping[str, Any],
    zone_id: int | None = None,
    execute: bool = True,
    confirm_write: bool = True,
) -> dict[str, Any]:
    """Build or execute one preference update for the selected map and zone."""
    if execute and not confirm_write:
        raise HomeAssistantError(
            "Preference writes require explicit confirmation when execution is enabled."
        )
    if not changes:
        raise HomeAssistantError("At least one mowing preference change is required.")

    maps = map_entries(coordinator.app_maps, coordinator.batch_device_data)
    if not maps:
        raise HomeAssistantError(
            "No selected or current map is available for mowing preference updates."
        )
    selected_map_index = current_map_index(
        coordinator.app_maps,
        coordinator.batch_device_data,
        selected_map_index=coordinator.selected_map_index,
    )

    zone_scoped_change = any(key != MOWING_PREFERENCE_MODE_FIELD for key in changes)
    zone_entries: list[dict[str, Any]] = []
    target_zone_id: int | None = None
    if zone_scoped_change:
        zone_entries = current_zone_entries(
            coordinator.batch_device_data,
            coordinator.app_maps,
            getattr(coordinator, "vector_map_details", None),
            selected_map_index=coordinator.selected_map_index,
        )
        target_zone_id = _resolve_zone_id(
            zone_entries,
            selected_zone_id=coordinator.selected_zone_id,
            requested_zone_id=zone_id,
        )

    result = await coordinator.client.async_plan_app_mowing_preference_update(
        map_index=selected_map_index,
        area_id=target_zone_id,
        changes=dict(changes),
        execute=execute,
        confirm_write=confirm_write,
    )
    selection_scope: dict[str, Any] = {
        "selected_map_index": selected_map_index,
        "selected_map_label": _map_label(maps, selected_map_index),
    }
    if target_zone_id is not None:
        selection_scope["selected_zone_id"] = target_zone_id
        selection_scope["selected_zone_label"] = _zone_label(
            zone_entries,
            target_zone_id,
        )
    result["selection_scope"] = selection_scope
    coordinator.last_preference_write_result = result
    if execute:
        await coordinator.async_refresh_batch_device_data(
            force=True,
            source="mowing_preference_write",
        )
        await coordinator.async_request_refresh()
    else:
        coordinator.async_update_listeners()
    return result


def _resolve_zone_id(
    zone_entries: list[dict[str, Any]],
    *,
    selected_zone_id: int | None,
    requested_zone_id: int | None,
) -> int:
    target = requested_zone_id if requested_zone_id is not None else selected_zone_id
    available = [
        int(entry["area_id"])
        for entry in zone_entries
        if isinstance(entry.get("area_id"), int)
    ]
    if target is None and available:
        target = available[0]
    if target not in available:
        raise HomeAssistantError(
            f"Zone #{target} is not available on the selected map. "
            f"Available zone ids: {available or 'none'}."
        )
    return int(target)


def _map_label(maps: list[dict[str, Any]], map_index: int) -> str | None:
    entry = next((item for item in maps if item.get("map_index") == map_index), None)
    label = entry.get("label") if isinstance(entry, dict) else None
    return str(label) if label is not None else None


def _zone_label(zone_entries: list[dict[str, Any]], zone_id: int) -> str:
    entry = next(
        (item for item in zone_entries if item.get("area_id") == zone_id),
        None,
    )
    label = entry.get("label") if isinstance(entry, dict) else None
    return str(label) if label is not None else f"Zone #{zone_id}"
