"""Canonical decoding of byte-array property values from cloud and realtime reads."""

from __future__ import annotations

import json
from collections.abc import Sequence


def normalize_byte_array(value: object) -> tuple[int, ...] | None:
    """Accept arrays or their JSON encoding without coercing invalid bytes."""
    raw = value
    if isinstance(raw, str):
        text = raw.strip()
        if not (text.startswith("[") and text.endswith("]")):
            return None
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return None

    if isinstance(raw, bytes | bytearray):
        return tuple(raw)

    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes | bytearray):
        return None
    if not raw:
        return None
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in raw):
        return None
    if not all(0 <= item <= 255 for item in raw):
        return None
    return tuple(raw)
