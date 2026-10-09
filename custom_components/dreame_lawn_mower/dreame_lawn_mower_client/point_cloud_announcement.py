"""Freshness decoding for the vendor point-cloud announcement property."""

from __future__ import annotations

from typing import Any

from .client_map_helpers import _app_object_extension
from .point_cloud_diagnostics import property_value_observation, value_shape
from .point_cloud_policy import (
    _POINT_CLOUD_ANNOUNCEMENT_CLOCK_SKEW_MS,
    _POINT_CLOUD_ANNOUNCEMENT_PROPERTY_KEY,
    _POINT_CLOUD_OBJECT_EXTENSIONS,
)
from .point_cloud_trace import record_point_cloud_stage


def announcement_result(
    entries: list[dict[str, Any]],
    *,
    payload_shape: str,
    requested_after_ms: int,
    baseline: tuple[str, int] | None,
    require_post_request: bool,
    observation: dict[str, Any],
) -> tuple[bool | None, str | None, tuple[str, int] | None]:
    record_point_cloud_stage(
        "announcement_result",
        {
            "value_shape": payload_shape,
            "property_entry_count": len(entries),
        },
    )
    observation["property_entry_count"] = min(len(entries), 1_000_000)
    observation["status"] = "property_missing"
    for entry in entries:
        if entry.get("key") != _POINT_CLOUD_ANNOUNCEMENT_PROPERTY_KEY:
            continue
        object_name = entry.get("value")
        updated_at = entry.get("updateDate")
        observation["value_shape"] = value_shape(object_name)
        observation.update(property_value_observation(object_name))
        observation["timestamp_shape"] = value_shape(updated_at)
        observation["status"] = "invalid_value"
        if not isinstance(object_name, str) or not object_name.strip():
            return True, None, None
        observation["status"] = "invalid_timestamp"
        if isinstance(updated_at, bool) or not isinstance(
            updated_at,
            int | float | str,
        ):
            return True, None, None
        try:
            updated_at_ms = int(updated_at)
        except (TypeError, ValueError, OverflowError):
            return True, None, None
        observation["timestamp_unit"] = (
            "milliseconds"
            if 1_000_000_000_000 <= updated_at_ms < 10_000_000_000_000
            else "seconds"
            if 1_000_000_000 <= updated_at_ms < 10_000_000_000
            else "unknown"
        )
        extension = _app_object_extension(object_name)
        observation["object_extension"] = (
            "missing"
            if extension is None
            else extension.casefold()
            if extension.casefold() in _POINT_CLOUD_OBJECT_EXTENSIONS
            else "unsupported"
        )
        if (
            extension is None
            or extension.casefold() not in _POINT_CLOUD_OBJECT_EXTENSIONS
        ):
            observation["status"] = "unsupported_extension"
            return True, None, None
        normalized_name = object_name.strip()
        observed = (normalized_name, updated_at_ms)
        observation["after_request"] = updated_at_ms > requested_after_ms
        if baseline is not None:
            observation["name_changed"] = normalized_name != baseline[0]
            observation["timestamp_changed"] = updated_at_ms != baseline[1]
        fresh = (
            (
                (normalized_name != baseline[0] or updated_at_ms > baseline[1])
                and updated_at_ms > requested_after_ms
            )
            if baseline is not None
            else (
                updated_at_ms > requested_after_ms
                if require_post_request
                else (
                    updated_at_ms
                    >= (requested_after_ms - _POINT_CLOUD_ANNOUNCEMENT_CLOCK_SKEW_MS)
                )
            )
        )
        observation["status"] = "fresh" if fresh else "stale"
        return True, normalized_name if fresh else None, observed
    return False, None, None
