"""Helpers for read-only app task/status probe diagnostics."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .dreame_lawn_mower_client.app_protocol import (
    MOWER_BATTERY_PROPERTY_KEY,
    MOWER_BLUETOOTH_PROPERTY_KEY,
    MOWER_RUNTIME_STATUS_PROPERTY_KEY,
    MOWER_STATE_PROPERTY_KEY,
    MOWER_TASK_PROPERTY_KEY,
    MOWER_TIME_PROPERTY_KEY,
)

TASK_STATUS_PROBE_KEYS = (
    MOWER_RUNTIME_STATUS_PROPERTY_KEY,
    MOWER_BLUETOOTH_PROPERTY_KEY,
    MOWER_STATE_PROPERTY_KEY,
    "2.2",
    MOWER_TASK_PROPERTY_KEY,
    MOWER_TIME_PROPERTY_KEY,
    "2.56",
    "2.60",
    MOWER_BATTERY_PROPERTY_KEY,
    "3.2",
    "5.104",
    "5.105",
    "5.106",
    "5.107",
)
SERVICE_5_KEYS = tuple(key for key in TASK_STATUS_PROBE_KEYS if key.startswith("5."))


def task_status_probe_payload(
    scan: Mapping[str, Any],
    *,
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Return a compact HA/log payload from one task-status property scan."""
    entries = scan.get("entries", [])
    summary = task_status_probe_summary(scan)
    payload: dict[str, Any] = {
        "captured_at": captured_at,
        "source": "cloud_property_task_status",
        "available": bool(entries),
        "keys": list(TASK_STATUS_PROBE_KEYS),
        "entry_count": len(entries) if isinstance(entries, Sequence) else 0,
        "summary": summary,
        "errors": [],
    }
    if isinstance(entries, Sequence) and not isinstance(
        entries,
        str | bytes | bytearray,
    ):
        payload["entries"] = list(entries)
    return {
        key: value
        for key, value in payload.items()
        if value is not None
    }


def task_status_probe_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return compact app state/task/error evidence from a scan payload."""
    entries = payload.get("entries", [])
    scan_summary = payload.get("summary", {})
    if not isinstance(scan_summary, Mapping):
        scan_summary = {}

    state_entry = _entry_by_key(entries, MOWER_STATE_PROPERTY_KEY)
    error_entry = _entry_by_key(entries, "2.2")
    task_entry = _entry_by_key(entries, MOWER_TASK_PROPERTY_KEY)
    time_entry = _entry_by_key(entries, MOWER_TIME_PROPERTY_KEY)
    runtime_status_entry = _entry_by_key(entries, MOWER_RUNTIME_STATUS_PROPERTY_KEY)
    bluetooth_entry = _entry_by_key(entries, MOWER_BLUETOOTH_PROPERTY_KEY)
    status_matrix_entry = _entry_by_key(entries, "2.56")
    battery_entry = _entry_by_key(entries, MOWER_BATTERY_PROPERTY_KEY)
    service_5_latest = {
        key: _entry_value(entry)
        for key in SERVICE_5_KEYS
        if isinstance(entry := _entry_by_key(entries, key), Mapping)
    }
    auxiliary_live_properties = {
        key: _entry_value(entry)
        for key in ("2.60", "3.2")
        if isinstance(entry := _entry_by_key(entries, key), Mapping)
    }
    unknown_keys = scan_summary.get("unknown_non_empty_keys", [])

    return _drop_empty(
        {
            "state": _state_summary(state_entry),
            "runtime_status": _status_blob_summary(runtime_status_entry),
            "bluetooth_connected": _entry_value(bluetooth_entry),
            "task_status": task_entry.get("task_status")
            if isinstance(task_entry, Mapping)
            else None,
            "error": _error_summary(error_entry),
            "error_active": _error_active(error_entry),
            "battery_level": _entry_value(battery_entry),
            "device_time": _entry_json_value(time_entry),
            "status_matrix": _status_matrix_summary(status_matrix_entry),
            "auxiliary_live_properties": auxiliary_live_properties,
            "service_5_latest": service_5_latest,
            "unknown_non_empty_keys": unknown_keys,
        }
    )


def task_status_probe_state(result: Mapping[str, Any] | None) -> str:
    """Return a stable HA state for the latest task/status probe."""
    if not result:
        return "none"
    errors = result.get("errors")
    if isinstance(errors, list) and errors and not result.get("available"):
        return "error"
    summary = result.get("summary")
    if not isinstance(summary, Mapping):
        return "available" if result.get("available") else "unavailable"
    state = summary.get("state")
    if isinstance(state, Mapping) and state.get("state_key"):
        return str(state["state_key"])
    task_status = summary.get("task_status")
    if isinstance(task_status, Mapping) and task_status.get("executing"):
        return "task_executing"
    return "available" if result.get("available") else "unavailable"


def task_status_probe_result_attributes(
    result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return compact, non-secret attributes for a task/status probe."""
    if not result:
        return {}

    summary = result.get("summary")
    if not isinstance(summary, Mapping):
        summary = {}
    errors = result.get("errors")
    attributes: dict[str, Any] = {
        "captured_at": result.get("captured_at"),
        "source": result.get("source"),
        "available": result.get("available"),
        "keys": result.get("keys"),
        "entry_count": result.get("entry_count"),
        "state": summary.get("state"),
        "runtime_status": summary.get("runtime_status"),
        "bluetooth_connected": summary.get("bluetooth_connected"),
        "task_status": summary.get("task_status"),
        "error": summary.get("error"),
        "error_active": summary.get("error_active"),
        "battery_level": summary.get("battery_level"),
        "device_time": summary.get("device_time"),
        "status_matrix": summary.get("status_matrix"),
        "auxiliary_live_properties": summary.get("auxiliary_live_properties"),
        "service_5_latest": summary.get("service_5_latest"),
        "unknown_non_empty_keys": summary.get("unknown_non_empty_keys"),
    }
    if isinstance(errors, list):
        attributes["error_count"] = len(errors)
        attributes["errors"] = errors
    return {
        key: value
        for key, value in attributes.items()
        if value not in (None, [], {})
    }


def _entry_by_key(entries: Any, key: str) -> Mapping[str, Any] | None:
    if not isinstance(entries, Sequence) or isinstance(
        entries,
        str | bytes | bytearray,
    ):
        return None
    for entry in entries:
        if isinstance(entry, Mapping) and str(entry.get("key")) == key:
            return entry
    return None


def _entry_value(entry: Mapping[str, Any] | None) -> Any:
    if not isinstance(entry, Mapping):
        return None
    return entry.get("value", entry.get("value_preview"))


def _entry_json_value(entry: Mapping[str, Any] | None) -> Any:
    value = _entry_value(entry)
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    if not ((text.startswith("{") and text.endswith("}")) or text.startswith("[")):
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _state_summary(entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(entry, Mapping):
        return None
    return _drop_empty(
        {
            "value": _entry_value(entry),
            "label": entry.get("decoded_label"),
            "state_key": entry.get("state_key"),
        }
    )


def _error_summary(entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(entry, Mapping):
        return None
    return _drop_empty(
        {
            "value": _entry_value(entry),
            "label": entry.get("decoded_label"),
            "label_source": entry.get("decoded_label_source"),
            "active": _error_active(entry),
        }
    )


def _error_active(entry: Mapping[str, Any] | None) -> bool | None:
    if not isinstance(entry, Mapping):
        return None
    value = _entry_value(entry)
    try:
        return int(str(value)) not in (-1, 0)
    except (TypeError, ValueError):
        return None


def _status_blob_summary(entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(entry, Mapping):
        return None
    blob = entry.get("status_blob")
    if not isinstance(blob, Mapping):
        return None
    notes = blob.get("notes")
    return _drop_empty(
        {
            "length": blob.get("length"),
            "hex": blob.get("hex"),
            "frame_valid": blob.get("frame_valid"),
            "candidate_battery_level": blob.get("candidate_battery_level"),
            "candidate_runtime_progress_percent": blob.get(
                "candidate_runtime_progress_percent"
            ),
            "candidate_runtime_area_progress_percent": blob.get(
                "candidate_runtime_area_progress_percent"
            ),
            "candidate_runtime_current_area_sqm": blob.get(
                "candidate_runtime_current_area_sqm"
            ),
            "candidate_runtime_total_area_sqm": blob.get(
                "candidate_runtime_total_area_sqm"
            ),
            "candidate_runtime_region_id": blob.get("candidate_runtime_region_id"),
            "candidate_runtime_task_id": blob.get("candidate_runtime_task_id"),
            "candidate_runtime_pose_x": blob.get("candidate_runtime_pose_x"),
            "candidate_runtime_pose_y": blob.get("candidate_runtime_pose_y"),
            "candidate_runtime_heading_deg": blob.get("candidate_runtime_heading_deg"),
            "notes": list(notes)
            if isinstance(notes, Sequence)
            and not isinstance(notes, str | bytes | bytearray)
            else None,
        }
    )


def _status_matrix_summary(entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
    value = _entry_json_value(entry)
    if isinstance(value, Mapping):
        status_pairs = _status_pairs(value.get("status"))
        return _drop_empty(
            {
                "keys": sorted(str(key) for key in value.keys()),
                "status_pairs": status_pairs,
                "status_count": len(status_pairs),
            }
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        status_pairs = _status_pairs(value)
        return _drop_empty(
            {
                "status_pairs": status_pairs,
                "status_count": len(status_pairs),
            }
        )
    return None


def _status_pairs(value: Any) -> list[list[Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    pairs: list[list[Any]] = []
    for item in value:
        if not isinstance(item, Sequence) or isinstance(item, str | bytes | bytearray):
            continue
        pairs.append(list(item[:2]))
    return pairs


def _drop_empty(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: cleaned
            for key, item in value.items()
            if (cleaned := _drop_empty(item)) not in (None, [], {})
        }
    if isinstance(value, list):
        return [_drop_empty(item) for item in value]
    return value
