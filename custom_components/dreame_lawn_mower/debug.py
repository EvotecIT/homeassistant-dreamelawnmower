"""Debug payload helpers for Dreame lawn mower."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .const import CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME

REDACT_KEYS = {
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
    "bindDomain",
    "did",
    "host",
    "localip",
    "mac",
    "masterName",
    "masterUid",
    "masterUid2UUID",
    "sn",
    "token",
    "uid",
    "username",
}

STATUS_FIELDS = (
    "state_name",
    "task_status_name",
    "battery_level",
    "charging",
    "started",
    "paused",
    "running",
    "returning",
    "docked",
    "scheduled_clean",
    "shortcut_task",
    "cleaning_mode_name",
    "child_lock",
)

CAPABILITY_FIELDS = (
    "lidar_navigation",
    "map",
    "custom_cleaning_mode",
    "shortcuts",
    "camera_streaming",
    "camera_light",
    "obstacles",
    "ai_detection",
    "disable_sensor_cleaning",
)

DOCKED_STATES = {
    "idle",
    "charging",
    "charging_completed",
    "building",
    "upgrading",
    "station_reset",
    "smart_charging",
    "waiting_for_task",
}


def _normalize_debug_value(value: Any) -> Any:
    """Convert a debug value into JSON-friendly data."""
    if is_dataclass(value):
        return _normalize_debug_value(asdict(value))
    if isinstance(value, Enum):
        return value.name.lower()
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_debug_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_normalize_debug_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    name = value.name if hasattr(value, "name") else None
    if isinstance(name, str):
        return name.lower()
    enum_value = value.value if hasattr(value, "value") else None
    if isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    return str(value)


def _is_no_error_value(value: Any) -> bool:
    """Return whether a debug value explicitly means no active error."""
    if value is None:
        return True
    text = str(value).strip().replace("_", " ").casefold()
    return text in {"", "none", "no error", "no error."}


def _active_error_from_snapshot(snapshot: Any) -> bool:
    """Return whether the normalized snapshot contains an active error signal."""
    error_code = getattr(snapshot, "error_code", None)
    return bool(
        error_code not in (None, -1, 0)
        or not _is_no_error_value(getattr(snapshot, "error_name", None))
        or not _is_no_error_value(getattr(snapshot, "error_text", None))
        or not _is_no_error_value(getattr(snapshot, "error_display", None))
    )


def _redact_debug_data(value: Any) -> Any:
    """Recursively redact sensitive fields from debug data."""
    if isinstance(value, Mapping):
        return {
            str(key): (
                "**REDACTED**"
                if str(key) in REDACT_KEYS
                else _redact_debug_data(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_debug_data(item) for item in value]
    return value


def _collect_status_values(device: Any) -> dict[str, Any]:
    """Return a compact status summary from the mower device."""
    status = getattr(device, "status", None)
    if status is None:
        return {}
    return {
        field: _normalize_debug_value(getattr(status, field, None))
        for field in STATUS_FIELDS
    }


def _collect_capability_values(device: Any) -> dict[str, Any]:
    """Return a compact capability summary from the mower device."""
    capability = getattr(device, "capability", None)
    if capability is None:
        return {}

    payload = {
        "list": _normalize_debug_value(getattr(capability, "list", [])),
    }
    for field in CAPABILITY_FIELDS:
        payload[field] = _normalize_debug_value(getattr(capability, field, None))
    return payload


def _collect_state_reconciliation(snapshot: Any, device: Any) -> dict[str, Any]:
    """Return source fields and warnings used to debug state disagreements."""
    raw_attributes = getattr(snapshot, "raw_attributes", {}) or {}
    status = getattr(device, "status", None) if device is not None else None
    status_values = _collect_status_values(device)
    activity = getattr(snapshot, "activity", None)
    state = getattr(snapshot, "state", None)
    state_name = getattr(snapshot, "state_name", None)
    raw_mower_state = raw_attributes.get("mower_state")
    snapshot_docked = bool(getattr(snapshot, "docked", False))
    snapshot_raw_docked = getattr(snapshot, "raw_docked", None)
    snapshot_charging = bool(getattr(snapshot, "charging", False))
    snapshot_raw_started = getattr(snapshot, "raw_started", None)
    snapshot_raw_returning = getattr(snapshot, "raw_returning", None)
    active_error = _active_error_from_snapshot(snapshot)
    warnings: list[str] = []

    if activity == "error" and not active_error:
        warnings.append("activity_error_without_active_error")
    if active_error and _is_no_error_value(getattr(snapshot, "error_display", None)):
        warnings.append("active_error_code_but_display_says_no_error")
    if state in DOCKED_STATES and snapshot_raw_docked is False:
        warnings.append("state_looks_docked_but_raw_docked_false")
    if raw_mower_state in DOCKED_STATES and snapshot_raw_docked is False:
        warnings.append("raw_mower_state_looks_docked_but_raw_docked_false")
    if snapshot_charging and not snapshot_docked:
        warnings.append("charging_true_but_docked_false")
    if (
        raw_mower_state is not None
        and state_name is not None
        and str(raw_mower_state).casefold() != str(state_name).casefold()
    ):
        warnings.append("raw_mower_state_differs_from_state_name")

    return {
        "activity": _normalize_debug_value(activity),
        "state": _normalize_debug_value(state),
        "state_name": _normalize_debug_value(state_name),
        "raw_mower_state": _normalize_debug_value(raw_mower_state),
        "status_state_name": _normalize_debug_value(
            getattr(status, "state_name", None) if status is not None else None
        ),
        "status_status": _normalize_debug_value(raw_attributes.get("status")),
        "error": {
            "active": active_error,
            "code": _normalize_debug_value(getattr(snapshot, "error_code", None)),
            "name": _normalize_debug_value(getattr(snapshot, "error_name", None)),
            "text": _normalize_debug_value(getattr(snapshot, "error_text", None)),
            "display": _normalize_debug_value(
                getattr(snapshot, "error_display", None)
            ),
            "raw_attribute": _normalize_debug_value(raw_attributes.get("error")),
        },
        "flags": {
            "charging": snapshot_charging,
            "docked": snapshot_docked,
            "raw_docked": snapshot_raw_docked,
            "mowing": bool(getattr(snapshot, "mowing", False)),
            "paused": bool(getattr(snapshot, "paused", False)),
            "returning": bool(getattr(snapshot, "returning", False)),
            "raw_returning": snapshot_raw_returning,
            "started": bool(getattr(snapshot, "started", False)),
            "raw_started": snapshot_raw_started,
        },
        "status_values": status_values,
        "warnings": warnings,
    }


def build_debug_payload(
    *,
    entry_data: Mapping[str, Any] | None,
    snapshot: Any,
    device: Any,
) -> dict[str, Any]:
    """Build a sanitized structured debug payload for diagnostics or logs."""
    descriptor = getattr(snapshot, "descriptor", None)
    info = getattr(device, "info", None) if device is not None else None
    status = getattr(device, "status", None) if device is not None else None

    payload = {
        "captured_at": datetime.now(UTC).isoformat(),
        "entry": _normalize_debug_value(dict(entry_data or {})),
        "descriptor": _normalize_debug_value(descriptor),
        "snapshot": _normalize_debug_value(snapshot),
        "state_reconciliation": _collect_state_reconciliation(snapshot, device),
        "cloud_record": _normalize_debug_value(getattr(descriptor, "raw", {})),
        "device": {
            "name": _normalize_debug_value(getattr(device, "name", None)),
            "available": _normalize_debug_value(getattr(device, "available", None)),
            "host": _normalize_debug_value(getattr(device, "host", None)),
            "token_present": bool(getattr(device, "token", None)),
            "unknown_property_count": len(
                getattr(device, "unknown_properties", {}) or {}
            ),
            "unknown_properties": _normalize_debug_value(
                getattr(device, "unknown_properties", {}) or {}
            ),
            "realtime_property_count": len(
                getattr(device, "realtime_properties", {}) or {}
            ),
            "realtime_properties": _normalize_debug_value(
                getattr(device, "realtime_properties", {}) or {}
            ),
            "last_realtime_message": _normalize_debug_value(
                getattr(device, "last_realtime_message", None)
            ),
            "status_values": _collect_status_values(device),
            "status_attributes": _normalize_debug_value(
                getattr(status, "attributes", {}) if status is not None else {}
            ),
            "capabilities": _collect_capability_values(device),
            "info_raw": _normalize_debug_value(
                getattr(info, "raw", {}) if info is not None else {}
            ),
        },
    }
    return _redact_debug_data(payload)
