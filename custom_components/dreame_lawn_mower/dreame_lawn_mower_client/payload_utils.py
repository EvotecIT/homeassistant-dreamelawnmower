"""Shared normalization helpers for reusable client payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _lower_enum_name(value: Any) -> str | None:
    name = getattr(value, "name", None)
    if name:
        return str(name).lower()
    if value is None:
        return None
    text = str(value).strip()
    return text.lower() or None


def _as_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _find_text_by_key(value: Any, keys: Sequence[str]) -> str | None:
    wanted = {key.casefold() for key in keys}
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in wanted:
                text = _as_optional_text(item)
                if text:
                    return text
        for item in value.values():
            text = _find_text_by_key(item, keys)
            if text:
                return text
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for item in value:
            text = _find_text_by_key(item, keys)
            if text:
                return text
    return None


def _json_safe(value: Any, *, max_depth: int = 4) -> Any:
    if max_depth < 0:
        return str(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, max_depth=max_depth - 1)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item, max_depth=max_depth - 1) for item in value]
    name = getattr(value, "name", None)
    if name is not None:
        return str(name).lower()
    return str(value)
