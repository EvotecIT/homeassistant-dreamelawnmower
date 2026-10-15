"""Recognize map-switch announcements without guessing the destination map."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any


def is_map_switch_announcement(key: str, value: Any) -> bool:
    """Accept the session marker or a TASK echo of the map-switch opcode."""
    if key == "1.50":
        return isinstance(value, Mapping) and not value
    if key != "2.50":
        return False
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return False
    if not isinstance(value, Mapping) or value.get("t") != "TASK":
        return False
    data = value.get("d")
    return isinstance(data, Mapping) and type(data.get("o")) is int and data["o"] == 200


def map_switch_event_at(properties: Mapping[str, Any]) -> float | None:
    """Return the latest successful switch announcement's receipt time."""
    latest: float | None = None
    for key in ("1.50", "2.50"):
        entry = properties.get(key)
        if not isinstance(entry, Mapping) or entry.get("code") not in (None, 0):
            continue
        received = entry.get("last_seen")
        if (
            is_map_switch_announcement(key, entry.get("value"))
            and isinstance(received, int | float)
            and not isinstance(received, bool)
            and math.isfinite(received)
            and received > 0
        ):
            latest = max(latest or 0, float(received))
    return latest
