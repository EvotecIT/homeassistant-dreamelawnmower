"""Bounded, value-free observations of the point-cloud discovery protocol.

These summaries describe what was received, not inferred mower capabilities.
Never include raw property values, object names, URLs, or exception messages.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def value_shape(value: Any) -> str:
    """Classify a vendor value without copying any of its contents."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "nonempty_string" if value.strip() else "empty_string"
    if isinstance(value, Mapping):
        return "mapping"
    if isinstance(value, Sequence):
        return "sequence"
    if isinstance(value, int | float):
        return "number"
    return "other"


def indexed_object_observation(data: Any, map_index: int) -> dict[str, Any]:
    """Distinguish an empty slot from a missing index or unexpected OBJ shape."""
    names = data.get("name") if isinstance(data, Mapping) else None
    result: dict[str, Any] = {"data_shape": value_shape(data)}
    if not isinstance(data, Mapping) or "name" not in data:
        result["names_shape"] = "missing"
        return result
    result["names_shape"] = value_shape(names)
    if not isinstance(names, Sequence) or isinstance(names, str | bytes | bytearray):
        return result
    result["slot_count"] = min(len(names), 1_000_000)
    result["selected_shape"] = (
        value_shape(names[map_index]) if 0 <= map_index < len(names) else "missing"
    )
    return result


def action_reply_observation(response: Any) -> dict[str, Any]:
    """Describe a rejected/malformed action reply without copying vendor values."""
    result: dict[str, Any] = {"value_shape": value_shape(response)}
    if isinstance(response, Mapping):
        result["result_shape"] = value_shape(response.get("r"))
        result["result_status"] = (
            "accepted"
            if type(response.get("r")) is int and response["r"] == 0
            else "rejected"
            if type(response.get("r")) is int
            else "invalid_value"
        )
        data = response.get("d")
        result["data_shape"] = value_shape(data)
        result["names_shape"] = (
            value_shape(data["name"])
            if isinstance(data, Mapping) and "name" in data
            else "missing"
        )
    return result


_COUNTERS = frozenset(
    {
        "announcement_polls",
        "announcement_fresh_observations",
        "announcement_stale_observations",
        "announcement_empty_observations",
        "announcement_unavailable_observations",
        "announcement_inconclusive_observations",
        "indexed_verification_attempts",
        "download_attempts",
        "map_index",
        "slot_count",
        "property_entry_count",
        "download_http_status",
        "download_bytes",
    }
)
_ENUM_FIELDS = frozenset(
    {
        "announcement_capability_initial",
        "announcement_capability",
        "announcement_baseline",
        "indexed_verification_result",
        "last_download_result",
        "status",
        "data_shape",
        "names_shape",
        "selected_shape",
        "value_shape",
        "timestamp_shape",
        "timestamp_unit",
        "object_extension",
        "generation_result",
        "last_download_step",
        "fixed_baseline",
        "stable_baseline",
        "signer_shape",
        "result_shape",
        "result_status",
    }
)
_VALUES = frozenset(
    {
        "available",
        "unavailable",
        "inconclusive",
        "observed",
        "not_observed",
        "not_attempted",
        "matched_announcement",
        "accepted_acknowledged_fixed_object",
        "object_not_observed",
        "object_mismatch",
        "validated",
        "accepted",
        "rejected",
        "null",
        "boolean",
        "nonempty_string",
        "empty_string",
        "mapping",
        "sequence",
        "number",
        "other",
        "missing",
        "pcd",
        "bin",
        "unsupported",
        "budget_exhausted",
        "transport_error",
        "no_response",
        "property_missing",
        "invalid_value",
        "invalid_timestamp",
        "unsupported_extension",
        "fresh",
        "stale",
        "milliseconds",
        "seconds",
        "unknown",
        "signer",
        "download",
        "validation",
        "absent",
        "present",
        "error:device",
        "rejected:fixed_baseline_inconclusive",
        "rejected:stale_object",
    }
)
_ERROR_CODES = frozenset(
    {
        "point_cloud_failed",
        "point_cloud_timeout",
        "point_cloud_download_invalid",
        "point_cloud_not_published",
        "point_cloud_download_unsupported",
        "point_cloud_invalid_request",
        "point_cloud_mower_request_failed",
        "point_cloud_mower_request_rejected",
        "point_cloud_mower_response_invalid",
    }
)
_SNAPSHOTS = frozenset(
    {
        "initial_announcement",
        "latest_announcement",
        "baseline_indexed",
        "latest_indexed",
        "action_reply",
        "stored_attempt",
    }
)


def safe_attempt_diagnostics(value: Mapping[str, Any]) -> dict[str, Any]:
    """Allowlist both keys and values at the public diagnostics boundary.

    An exception's context is extensible and may originate in another transport.
    Do not expose arbitrary strings even when they use a recognized field name.
    """
    result: dict[str, Any] = {}
    for key in _COUNTERS | _ENUM_FIELDS | _SNAPSHOTS:
        item = value.get(key)
        if key in _COUNTERS and type(item) is int and 0 <= item <= 1_000_000_000:
            result[key] = item
        elif key in _ENUM_FIELDS and isinstance(item, str):
            if item in _VALUES or item in {f"error:{code}" for code in _ERROR_CODES}:
                result[key] = item
        elif key in _SNAPSHOTS and isinstance(item, Mapping):
            # Snapshots contain only scalar fields; nested input cannot recurse.
            result[key] = safe_attempt_diagnostics(
                {name: item[name] for name in _COUNTERS | _ENUM_FIELDS if name in item}
            )
    return result


def safe_failure_diagnostics(error: Any) -> dict[str, Any]:
    """Share the same privacy contract in HTTP, diagnostics, and the public probe."""
    result: dict[str, Any] = {
        "attempt": safe_attempt_diagnostics(error.diagnostic_context)
    }
    if error.diagnostic_reason in {
        "fixed_baseline_inconclusive",
        "published_object_invalid",
        "unchanged_object",
        "object_not_observed",
    }:
        result["reason"] = error.diagnostic_reason
    if error.discovery_route in {"announcement_property", "legacy_obj"}:
        result["discovery_route"] = error.discovery_route
    if type(error.generation_acknowledged) is bool:
        result["generation_acknowledged"] = error.generation_acknowledged
    return result
