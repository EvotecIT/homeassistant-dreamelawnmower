"""Interpret the cloud's reported AI privacy-policy acceptance."""

from __future__ import annotations

import json
from collections.abc import Mapping

AI_POLICY_PROPERTY = "prop.s_ai_config"


def decode_ai_policy_acceptance(response: object) -> bool | None:
    """Return explicit acceptance or refusal; missing/malformed data is unknown."""
    if not isinstance(response, Mapping):
        return None
    encoded = response.get(AI_POLICY_PROPERTY)
    if not isinstance(encoded, str):
        return None
    try:
        data: object = json.loads(encoded)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, Mapping):
        return None
    value = data.get("privacyAuthed") if "privacyAuthed" in data else data.get(
        "aiPrivacyAuthed"
    )
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return None
