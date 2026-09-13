"""Bounded, value-free observations of the point-cloud discovery protocol.

These summaries describe what was received, not inferred mower capabilities.
Never include raw property values, object names, URLs, or exception messages.
"""

from __future__ import annotations

import json
import re
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


_MEMBER_NAMES = frozenset(
    {
        "name",
        "objectName",
        "fileName",
        "object_name",
        "file_name",
        "url",
        "value",
        "data",
        "result",
        "idx",
        "index",
        "timestamp",
        "updateDate",
        "key",
    }
)
_SHAPES = frozenset(
    {
        "null",
        "boolean",
        "empty_string",
        "nonempty_string",
        "mapping",
        "sequence",
        "number",
        "other",
    }
)


def property_value_observation(value: Any) -> dict[str, Any]:
    """Describe known members of an unfamiliar value, never arbitrary keys/values."""
    result: dict[str, Any] = {"value_shape": value_shape(value)}
    structured = value
    if isinstance(value, str):
        text = value.strip()
        result["string_form"] = (
            "json_object"
            if text.startswith("{")
            else "json_array"
            if text.startswith("[")
            else "other"
        )
        if text.startswith(("{", "[")):
            if len(text) > 4096:
                result["structure_truncated"] = True
            else:
                try:
                    structured = json.loads(text)
                except (ValueError, RecursionError):
                    pass
    if isinstance(structured, Mapping):
        result["member_shapes"] = {
            key: value_shape(structured[key])
            for key in _MEMBER_NAMES
            if key in structured
        }
        result["unknown_member_count"] = len(structured) - len(result["member_shapes"])
    return result


def indexed_object_observation(data: Any, map_index: int) -> dict[str, Any]:
    """Distinguish an empty slot from a missing index or unexpected OBJ shape."""
    names = data.get("name") if isinstance(data, Mapping) else None
    result: dict[str, Any] = {"data_shape": value_shape(data)}
    if not isinstance(data, Mapping) or "name" not in data:
        result["names_shape"] = "missing"
        return result
    result["names_shape"] = value_shape(names)
    if isinstance(names, Mapping):
        result.update(property_value_observation(names))
        result["numeric_member_count"] = sum(
            isinstance(key, int) or isinstance(key, str) and key.isdecimal()
            for key in names
        )
        selected = str(map_index) if str(map_index) in names else map_index
        result["selected_shape"] = (
            value_shape(names[selected]) if selected in names else "missing"
        )
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
        "pcd_fields",
        "pcd_points",
        "pcd_payload_bytes",
        "unknown_member_count",
        "numeric_member_count",
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
        "validation_reason",
        "pcd_encoding",
        "download_reason",
        "string_form",
        "exception_kind",
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
        "ascii",
        "binary",
        "binary_compressed",
        "json_object",
        "json_array",
        "KeyError",
        "TypeError",
        "ValueError",
        "AttributeError",
        "RuntimeError",
        "OSError",
        "ConnectionError",
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
        "first_failure",
    }
)
_BOOL_FIELDS = frozenset(
    {
        "name_changed",
        "timestamp_changed",
        "after_request",
        "structure_truncated",
    }
)

_TRACE_STAGES = frozenset(
    {
        "queue",
        "cloud_setup",
        "announcement_read",
        "announcement_result",
        "baseline_signer",
        "baseline_download",
        "stored_download",
        "generation_request",
        "generation_reply",
        "indexed_read",
        "indexed_reply",
        "signer",
        "signer_reply",
        "download",
        "download_result",
        "validation",
        "validation_result",
        "poll_result",
        "failed",
        "outer_timeout",
    }
)

_VALIDATION_REASONS = frozenset(
    {
        "empty_content",
        "byte_limit",
        "header_non_ascii",
        "duplicate_header",
        "missing_data_declaration",
        "missing_header_value",
        "header_value_count",
        "header_integer",
        "header_positive_integer",
        "header_nonnegative_integer",
        "version",
        "coordinate_fields",
        "duplicate_fields",
        "field_lengths",
        "field_size",
        "field_type",
        "float_size",
        "coordinate_type",
        "point_dimensions",
        "point_limit",
        "encoding",
        "ascii_scalar_limit",
        "binary_payload_length",
        "ascii_row_count",
        "ascii_row_size",
        "ascii_non_ascii",
        "ascii_column_count",
        "nonfinite_coordinates",
        "validation_timeout",
        "ascii_scalar",
    }
)
_DOWNLOAD_REASONS = frozenset(
    {
        "signer_empty",
        "signer_invalid_url",
        "https_redirect",
        "invalid_content_length",
        "byte_limit",
        "download_timeout",
        "truncated_content",
        "http_error",
        "transport_error",
        "deadline_unavailable",
        "signer_invalid_response",
    }
)


def safe_attempt_diagnostics(
    value: Mapping[str, Any],
    *,
    include_trace: bool = True,
) -> dict[str, Any]:
    """Allowlist both keys and values at the public diagnostics boundary.

    An exception's context is extensible and may originate in another transport.
    Do not expose arbitrary strings even when they use a recognized field name.
    """
    result: dict[str, Any] = {}
    for key in _COUNTERS | _ENUM_FIELDS | _SNAPSHOTS | _BOOL_FIELDS:
        item = value.get(key)
        if key in _BOOL_FIELDS and type(item) is bool:
            result[key] = item
        elif key in _COUNTERS and type(item) is int and 0 <= item <= 1_000_000_000:
            result[key] = item
        elif key in _ENUM_FIELDS and isinstance(item, str):
            if item in _VALUES or item in {f"error:{code}" for code in _ERROR_CODES}:
                result[key] = item
        elif key in _SNAPSHOTS and isinstance(item, Mapping):
            # Snapshots contain only scalar fields; nested input cannot recurse.
            result[key] = safe_attempt_diagnostics(
                {
                    name: item[name]
                    for name in _COUNTERS
                    | _ENUM_FIELDS
                    | _BOOL_FIELDS
                    | {"member_shapes"}
                    if name in item
                },
                include_trace=False,
            )
    members = value.get("member_shapes")
    if isinstance(members, Mapping):
        result["member_shapes"] = {
            key: members[key]
            for key in _MEMBER_NAMES
            if isinstance(members.get(key), str) and members[key] in _SHAPES
        }
    reason = value.get("validation_reason")
    if isinstance(reason, str) and reason in _VALIDATION_REASONS:
        result["validation_reason"] = reason
    reason = value.get("download_reason")
    if isinstance(reason, str) and reason in _DOWNLOAD_REASONS:
        result["download_reason"] = reason
    if not include_trace:
        return result
    attempt_id = value.get("attempt_id")
    if isinstance(attempt_id, str) and re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        result["attempt_id"] = attempt_id
    for key in ("trace_schema_version", "trace_event_count", "trace_dropped_events"):
        item = value.get(key)
        if type(item) is int and 0 <= item <= 1_000_000_000:
            result[key] = item
    if type(value.get("worker_finished")) is bool:
        result["worker_finished"] = value["worker_finished"]
    timeline = value.get("timeline")
    if isinstance(timeline, list):
        events = []
        for event in timeline[:32]:
            if not isinstance(event, Mapping):
                continue
            stage, elapsed = event.get("trace_stage"), event.get("elapsed_ms")
            if not isinstance(stage, str) or stage not in _TRACE_STAGES:
                continue
            if type(elapsed) is not int or not 0 <= elapsed <= 1_000_000_000:
                continue
            observation = event.get("observation")
            events.append(
                {
                    "trace_stage": stage,
                    "elapsed_ms": elapsed,
                    "observation": safe_attempt_diagnostics(
                        observation if isinstance(observation, Mapping) else {},
                        include_trace=False,
                    ),
                }
            )
        result["timeline"] = events
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
