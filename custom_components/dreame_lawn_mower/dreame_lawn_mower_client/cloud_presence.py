"""Decode explicit vendor presence without treating missing data as offline."""

from collections.abc import Mapping
from typing import Any


def cloud_device_online(info: Mapping[str, Any]) -> bool | None:
    """Return only a recognized online flag from authenticated device metadata."""
    value = info.get("online")
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "on", "yes", "1"}:
            return True
        if normalized in {"false", "off", "no", "0"}:
            return False
    return None
