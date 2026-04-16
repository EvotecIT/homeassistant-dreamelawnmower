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
        "cloud_record": _normalize_debug_value(getattr(descriptor, "raw", {})),
        "device": {
            "name": _normalize_debug_value(getattr(device, "name", None)),
            "available": _normalize_debug_value(getattr(device, "available", None)),
            "host": _normalize_debug_value(getattr(device, "host", None)),
            "token_present": bool(getattr(device, "token", None)),
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
