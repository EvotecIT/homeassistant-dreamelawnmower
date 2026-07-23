"""Classify privacy-safe Dreame XP2P provisioning diagnostics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

XP2P_PROVISIONING_DEVICE_TRIPLE_MISSING = "device_triple_missing"

_DEVICE_TRIPLE_REQUIRED_FIELDS = frozenset(
    {"product_id", "device_name", "p2p_info"}
)
_DEVICE_TRIPLE_MISSING_CODE = 10000
_DEVICE_TRIPLE_MISSING_MESSAGES = frozenset(
    {
        "设备三元组不存在",
        "the device triple does not exist",
        "device triple does not exist",
    }
)
_DEVICE_TRIPLE_STAGES = frozenset(
    {"cloud_device_identity", "cloud_p2p_info"}
)


def classify_xp2p_provisioning_issue(
    diagnostics: Mapping[str, Any],
    *,
    missing_required: Sequence[str],
) -> str | None:
    """Return a stable provisioning issue from sanitized cloud-stage evidence."""
    if not _DEVICE_TRIPLE_REQUIRED_FIELDS.issubset(missing_required):
        return None

    matching_stages: set[str] = set()
    stages = diagnostics.get("stages")
    if not _is_sequence(stages):
        return None

    for stage in stages:
        if not isinstance(stage, Mapping):
            continue
        stage_name = stage.get("stage")
        if stage_name not in _DEVICE_TRIPLE_STAGES:
            continue
        response = stage.get("response")
        if not isinstance(response, Mapping):
            continue
        if _has_device_triple_missing_response(response):
            matching_stages.add(str(stage_name))

    if matching_stages == _DEVICE_TRIPLE_STAGES:
        return XP2P_PROVISIONING_DEVICE_TRIPLE_MISSING
    return None


def _has_device_triple_missing_response(response: Mapping[str, Any]) -> bool:
    codes = response.get("codes")
    messages = response.get("messages")
    if not _is_sequence(codes) or not _is_sequence(messages):
        return False

    code_matches = any(
        isinstance(item, Mapping)
        and item.get("value") == _DEVICE_TRIPLE_MISSING_CODE
        for item in codes
    )
    message_matches = any(
        isinstance(item, Mapping)
        and str(item.get("text") or "").strip().casefold()
        in _DEVICE_TRIPLE_MISSING_MESSAGES
        for item in messages
    )
    return code_matches and message_matches


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(
        value,
        str | bytes | bytearray,
    )
