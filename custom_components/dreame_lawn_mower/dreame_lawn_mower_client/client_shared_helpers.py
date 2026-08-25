"""Small primitives shared by reusable client domains."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .exceptions import (
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
)
from .exceptions import (
    DreameLawnMowerError as DreameLawnMowerError,
)


def _setting_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value != 0
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return bool(value)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _epoch_to_iso(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    timestamp = parsed / 1000 if parsed > 10_000_000_000 else parsed
    try:
        return datetime.fromtimestamp(timestamp, UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _property_entry_received_at(entry: Mapping[str, Any]) -> str | None:
    """Return a vendor or local reception timestamp without inventing freshness."""
    for key in ("last_seen", "timestamp", "time", "update_time", "updateTime"):
        received_at = _epoch_to_iso(entry.get(key))
        if received_at is not None:
            return received_at
    return None


def _app_action_data(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return None
    if value.get("r") not in (None, 0):
        raise DreameLawnMowerConnectionError(f"App action failed: {value}")
    return value.get("d")


def _ensure_app_write_succeeded(
    value: Any,
    *,
    operation: str,
    allow_missing_data: bool = False,
) -> Any:
    """Require an explicit mower acknowledgement for a state-changing request."""
    if not isinstance(value, Mapping) or value.get("r") is None:
        raise DreameLawnMowerConnectionError(
            f"{operation} failed: the mower did not acknowledge the request."
        )
    if value.get("r") != 0:
        raise DreameLawnMowerCommandRejectedError(
            f"{operation} failed: the mower rejected the request."
        )
    if "d" not in value and not allow_missing_data:
        raise DreameLawnMowerConnectionError(
            f"{operation} failed: the mower did not acknowledge the request."
        )
    data = value.get("d")
    if isinstance(data, Mapping) and data.get("r") not in (None, 0):
        raise DreameLawnMowerCommandRejectedError(
            f"{operation} failed: the mower rejected the request."
        )
    return data


def _operation_value_type(value: Any) -> str:
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
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return "array"
    return type(value).__name__
