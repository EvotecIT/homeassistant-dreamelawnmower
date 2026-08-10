"""Thin Home Assistant adapters for mower-native device settings."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import time
from typing import Any


def device_settings_section(value: Any) -> Mapping[str, Any] | None:
    """Return a usable cached CFG settings record."""
    if not isinstance(value, Mapping) or not value.get("available"):
        return None
    return value


def minutes_to_time(minutes: int | None) -> time | None:
    """Convert minutes since midnight to a Home Assistant time value."""
    if minutes is None:
        return None
    return time(hour=int(minutes) // 60, minute=int(minutes) % 60)


def time_to_minutes(value: time) -> int:
    """Convert a Home Assistant time value to minutes since midnight."""
    return value.hour * 60 + value.minute


def rain_delay_label(hours: int) -> str:
    """Return a stable, human-friendly rain-delay option."""
    if hours == 0:
        return "Until manually started"
    return f"{hours} hour" if hours == 1 else f"{hours} hours"
