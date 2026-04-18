"""Debug payload helpers for Dreame lawn mower."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from .const import CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME
from .dreame_client.app_protocol import (
    MOWER_RAW_STATUS_PROPERTY_KEY,
    decode_mower_status_blob,
)
from .dreame_client.map_probe import MAP_CANDIDATE_TERMS
from .dreame_client.models import (
    MODEL_NAME_MAP,
    SUPPORTED_MODEL_MARKER,
    remote_control_block_reason,
    remote_control_state_safe,
)

DIAGNOSTIC_SCHEMA_VERSION = 5
UNKNOWN_REALTIME_PREFIX = "UNKNOWN_REALTIME_"

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
    "serial_number",
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


def _value_type(value: Any) -> str:
    """Return a stable coarse type label for debug summaries."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list | tuple | set):
        return "array"
    return type(value).__name__


def _short_preview(value: Any, *, max_length: int = 120) -> Any:
    """Return a compact JSON-safe preview for report triage."""
    normalized = _normalize_debug_value(value)
    if isinstance(normalized, str):
        return (
            normalized
            if len(normalized) <= max_length
            else f"{normalized[: max_length - 3]}..."
        )
    if isinstance(normalized, list):
        preview = normalized[:10]
        if len(normalized) > 10:
            preview.append(f"... +{len(normalized) - 10} items")
        return preview
    if isinstance(normalized, Mapping):
        return {
            key: normalized[key]
            for key in list(normalized.keys())[:10]
        }
    return normalized


def _map_candidate_reason(key: object, value: Any) -> str | None:
    """Return why a value looks useful for future map decoding, if it does."""
    haystacks = [str(key).casefold()]
    if isinstance(value, str):
        haystacks.append(value[:2000].casefold())
    elif isinstance(value, Mapping):
        haystacks.extend(str(item).casefold() for item in value.keys())

    for term in MAP_CANDIDATE_TERMS:
        folded = term.casefold()
        if any(folded in haystack for haystack in haystacks):
            return f"contains_{term}"
    if isinstance(value, Mapping):
        return "object_payload"
    return None


def _count_value_type(counter: dict[str, int], value: Any) -> None:
    value_type = _value_type(value)
    counter[value_type] = counter.get(value_type, 0) + 1


def _status_blob_summary(key: object, value: Any) -> dict[str, Any] | None:
    if str(key) != MOWER_RAW_STATUS_PROPERTY_KEY:
        return None
    decoded = decode_mower_status_blob(value, source="debug")
    if decoded is None:
        return None
    return {
        "length": decoded.length,
        "frame_valid": decoded.frame_valid,
        "hex": decoded.hex,
        "notes": _normalize_debug_value(decoded.notes),
        "bytes_by_index": _normalize_debug_value(decoded.bytes_by_index),
    }


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


def sanitize_debug_data(value: Any) -> Any:
    """Return JSON-friendly debug data with sensitive fields redacted."""
    return _redact_debug_data(_normalize_debug_value(value))


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
    snapshot_raw_charging = getattr(snapshot, "raw_charging", None)
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
            "raw_charging": snapshot_raw_charging,
            "docked": snapshot_docked,
            "raw_docked": snapshot_raw_docked,
            "mowing": bool(getattr(snapshot, "mowing", False)),
            "paused": bool(getattr(snapshot, "paused", False)),
            "returning": bool(getattr(snapshot, "returning", False)),
            "raw_returning": snapshot_raw_returning,
            "started": bool(getattr(snapshot, "started", False)),
            "raw_started": snapshot_raw_started,
        },
        "manual_drive": {
            "safe": remote_control_state_safe(snapshot),
            "block_reason": _normalize_debug_value(
                remote_control_block_reason(snapshot)
            ),
        },
        "status_values": status_values,
        "warnings": warnings,
    }


def _collect_unknown_property_summary(device: Any) -> dict[str, Any]:
    """Return compact unknown-property details for issue triage."""
    unknown_properties = getattr(device, "unknown_properties", {}) or {}
    entries = []
    value_type_counts: dict[str, int] = {}
    candidate_map_properties: list[dict[str, Any]] = []
    for key, value in unknown_properties.items():
        payload = value if isinstance(value, Mapping) else {}
        property_value = payload.get("value")
        _count_value_type(value_type_counts, property_value)
        candidate_reason = _map_candidate_reason(
            f"{payload.get('siid')}.{payload.get('piid')}",
            property_value,
        )
        if candidate_reason:
            candidate_map_properties.append(
                {
                    "key": str(key),
                    "siid": _normalize_debug_value(payload.get("siid")),
                    "piid": _normalize_debug_value(payload.get("piid")),
                    "reason": candidate_reason,
                    "value_type": _value_type(property_value),
                    "value_preview": _short_preview(property_value),
                }
            )
        entries.append(
            {
                "key": str(key),
                "siid": _normalize_debug_value(payload.get("siid")),
                "piid": _normalize_debug_value(payload.get("piid")),
                "code": _normalize_debug_value(payload.get("code")),
                "value_type": _value_type(property_value),
                "value_preview": _short_preview(property_value),
                "map_candidate_reason": candidate_reason,
            }
        )

    entries.sort(key=lambda item: item["key"])
    candidate_map_properties.sort(key=lambda item: item["key"])
    return {
        "count": len(entries),
        "keys": [entry["key"] for entry in entries],
        "value_type_counts": value_type_counts,
        "candidate_map_property_count": len(candidate_map_properties),
        "candidate_map_properties": candidate_map_properties[:20],
        "entries": entries,
    }


def _collect_realtime_summary(device: Any) -> dict[str, Any]:
    """Return compact realtime-property details for issue triage."""
    realtime_properties = getattr(device, "realtime_properties", {}) or {}
    entries = []
    known_keys: list[str] = []
    unknown_keys: list[str] = []
    value_type_counts: dict[str, int] = {}
    candidate_map_properties: list[dict[str, Any]] = []
    status_blob_keys: list[str] = []

    for key, value in realtime_properties.items():
        payload = value if isinstance(value, Mapping) else {}
        property_name = str(payload.get("property_name") or "")
        key_text = str(key)
        if property_name.startswith(UNKNOWN_REALTIME_PREFIX):
            unknown_keys.append(key_text)
        else:
            known_keys.append(key_text)
        property_value = payload.get("value")
        _count_value_type(value_type_counts, property_value)
        candidate_reason = _map_candidate_reason(key_text, property_value)
        if candidate_reason:
            candidate_map_properties.append(
                {
                    "key": key_text,
                    "reason": candidate_reason,
                    "value_type": _value_type(property_value),
                    "value_preview": _short_preview(property_value),
                }
            )
        status_blob = _status_blob_summary(key_text, property_value)
        if status_blob is not None:
            status_blob_keys.append(key_text)
        entries.append(
            {
                "key": key_text,
                "property_name": property_name or None,
                "siid": _normalize_debug_value(payload.get("siid")),
                "piid": _normalize_debug_value(payload.get("piid")),
                "code": _normalize_debug_value(payload.get("code")),
                "value_type": _value_type(property_value),
                "value_preview": _short_preview(property_value),
                "map_candidate_reason": candidate_reason,
                "status_blob": status_blob,
            }
        )

    entries.sort(key=lambda item: item["key"])
    known_keys.sort()
    unknown_keys.sort()
    candidate_map_properties.sort(key=lambda item: item["key"])
    status_blob_keys.sort()
    return {
        "count": len(entries),
        "known_keys": known_keys,
        "unknown_keys": unknown_keys,
        "value_type_counts": value_type_counts,
        "candidate_map_property_count": len(candidate_map_properties),
        "candidate_map_properties": candidate_map_properties[:20],
        "status_blob_keys": status_blob_keys,
        "entries": entries,
    }


def _collect_triage_summary(
    *,
    snapshot: Any,
    device: Any,
    state_reconciliation: Mapping[str, Any],
    unknown_property_summary: Mapping[str, Any],
    realtime_summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a compact first-read summary for user bug reports."""
    descriptor = getattr(snapshot, "descriptor", None)
    model = getattr(descriptor, "model", None)
    capabilities = tuple(getattr(snapshot, "capabilities", ()) or ())
    issues: list[str] = []
    suggested_next_capture: list[str] = []
    state_warnings = list(state_reconciliation.get("warnings", []) or [])

    if model not in MODEL_NAME_MAP:
        issues.append("model_not_in_known_model_map")
    if model and SUPPORTED_MODEL_MARKER not in model:
        issues.append("model_does_not_match_mower_marker")
    if not bool(getattr(snapshot, "available", False)):
        issues.append("snapshot_unavailable")
    if state_warnings:
        issues.extend(f"state:{warning}" for warning in state_warnings)
    if unknown_property_summary.get("count", 0):
        issues.append("unknown_device_properties_present")
    if realtime_summary.get("unknown_keys"):
        issues.append("unknown_realtime_properties_present")
    mapping_available = bool(getattr(snapshot, "mapping_available", False))
    if "map" in capabilities and not mapping_available:
        suggested_next_capture.append("capture_map_probe")
    if unknown_property_summary.get("count", 0) or realtime_summary.get("unknown_keys"):
        suggested_next_capture.append("download_diagnostics_after_state_change")
    if not suggested_next_capture:
        suggested_next_capture.append("download_diagnostics")

    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "known_model": model in MODEL_NAME_MAP,
        "model": _normalize_debug_value(model),
        "display_model": _normalize_debug_value(
            getattr(descriptor, "display_model", None)
        ),
        "activity": _normalize_debug_value(getattr(snapshot, "activity", None)),
        "state": _normalize_debug_value(getattr(snapshot, "state", None)),
        "error": {
            "active": bool(state_reconciliation.get("error", {}).get("active")),
            "code": _normalize_debug_value(getattr(snapshot, "error_code", None)),
            "display": _normalize_debug_value(
                getattr(snapshot, "error_display", None)
            ),
        },
        "manual_drive": _normalize_debug_value(
            state_reconciliation.get("manual_drive", {})
        ),
        "available": _normalize_debug_value(getattr(snapshot, "available", None)),
        "capabilities": _normalize_debug_value(capabilities),
        "unknown_property_count": unknown_property_summary.get("count", 0),
        "unknown_realtime_count": len(realtime_summary.get("unknown_keys", []) or []),
        "state_warning_count": len(state_warnings),
        "issues": issues,
        "suggested_next_capture": suggested_next_capture,
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
    state_reconciliation = _collect_state_reconciliation(snapshot, device)
    unknown_property_summary = _collect_unknown_property_summary(device)
    realtime_summary = _collect_realtime_summary(device)

    payload = {
        "diagnostic_schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "captured_at": datetime.now(UTC).isoformat(),
        "entry": _normalize_debug_value(dict(entry_data or {})),
        "descriptor": _normalize_debug_value(descriptor),
        "snapshot": _normalize_debug_value(snapshot),
        "triage": _collect_triage_summary(
            snapshot=snapshot,
            device=device,
            state_reconciliation=state_reconciliation,
            unknown_property_summary=unknown_property_summary,
            realtime_summary=realtime_summary,
        ),
        "state_reconciliation": state_reconciliation,
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
            "unknown_property_summary": unknown_property_summary,
            "realtime_property_count": len(
                getattr(device, "realtime_properties", {}) or {}
            ),
            "realtime_properties": _normalize_debug_value(
                getattr(device, "realtime_properties", {}) or {}
            ),
            "realtime_summary": realtime_summary,
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
