"""Shared voice write validation and confirmation decoding."""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping, Sequence
from typing import Any

from .client_constants import (
    VOICE_LANGUAGE_INDEX_TO_CODE,
    VOICE_LANGUAGE_INDEX_TO_LABEL,
    VOICE_PROMPT_FIELDS,
)
from .client_settings_helpers import _as_optional_int, _normalize_voice_prompt_flags
from .client_shared_helpers import _ensure_app_write_succeeded
from .exceptions import DreameLawnMowerConnectionError
from .payload_utils import _json_safe


def run_voice_write(
    plan: Generator[Mapping[str, Any], Any, dict[str, Any]],
    dispatch: Callable[[Mapping[str, Any]], Any],
) -> dict[str, Any]:
    """Run the same one-command contract through the synchronous transport."""
    try:
        action = next(plan)
        response = dispatch(action)
        try:
            plan.send(response)
        except StopIteration as completed:
            result: dict[str, Any] = completed.value
            return result
        raise RuntimeError("Voice write emitted more than one command")
    finally:
        plan.close()


def write_voice_language(
    voice_language: int,
) -> Generator[Mapping[str, Any], Any, dict[str, Any]]:
    """Set the mower voice language and return the confirmed response."""
    request = {
        "m": "s",
        "t": "LANG",
        "d": {
            "type": "voice",
            "value": int(voice_language),
        },
    }
    response = yield request
    data = _ensure_app_write_succeeded(
        response,
        operation="Voice language write",
    )
    if not isinstance(data, Mapping):
        raise DreameLawnMowerConnectionError(
            f"LANG voice write returned invalid data: {response}"
        )
    confirmed_voice_language = _as_optional_int(data.get("voice"))
    confirmed_text_language = _as_optional_int(data.get("text"))
    return {
        "source": "app_action_voice_settings_write",
        "action": "set_voice_language",
        "request": _json_safe(request, max_depth=4),
        "response_data": _json_safe(response, max_depth=4),
        "text_language_index": confirmed_text_language,
        "voice_language_index": confirmed_voice_language,
        "voice_language_name": (
            VOICE_LANGUAGE_INDEX_TO_LABEL.get(confirmed_voice_language)
            if confirmed_voice_language is not None
            else None
        ),
        "voice_language_code": (
            VOICE_LANGUAGE_INDEX_TO_CODE.get(confirmed_voice_language)
            if confirmed_voice_language is not None
            else None
        ),
    }


def write_voice_volume(
    volume: int,
) -> Generator[Mapping[str, Any], Any, dict[str, Any]]:
    """Set the mower voice volume and return the confirmed response."""
    if volume < 0 or volume > 100:
        raise ValueError("volume must be between 0 and 100")
    request = {
        "m": "s",
        "t": "VOL",
        "d": {
            "value": int(volume),
        },
    }
    response = yield request
    data = _ensure_app_write_succeeded(
        response,
        operation="Voice volume write",
    )
    if not isinstance(data, Mapping):
        raise DreameLawnMowerConnectionError(
            f"VOL write returned invalid data: {response}"
        )
    return {
        "source": "app_action_voice_settings_write",
        "action": "set_voice_volume",
        "request": _json_safe(request, max_depth=4),
        "response_data": _json_safe(response, max_depth=4),
        "volume": _as_optional_int(data.get("value")),
    }


def write_voice_prompts(
    prompts: Sequence[int],
) -> Generator[Mapping[str, Any], Any, dict[str, Any]]:
    """Set the mower voice prompt flags and return the confirmed response."""
    normalized = _normalize_voice_prompt_flags(prompts)
    request = {
        "m": "s",
        "t": "VOICE",
        "d": {
            "value": normalized,
        },
    }
    response = yield request
    data = _ensure_app_write_succeeded(
        response,
        operation="Voice prompt write",
    )
    if not isinstance(data, Mapping):
        raise DreameLawnMowerConnectionError(
            f"VOICE write returned invalid data: {response}"
        )
    confirmed = _normalize_voice_prompt_flags(data.get("value"))
    result = {
        "source": "app_action_voice_settings_write",
        "action": "set_voice_prompts",
        "request": _json_safe(request, max_depth=4),
        "response_data": _json_safe(response, max_depth=4),
        "voice_prompts": confirmed,
    }
    for field_name, enabled in zip(VOICE_PROMPT_FIELDS, confirmed, strict=True):
        result[field_name] = bool(enabled)
    return result
