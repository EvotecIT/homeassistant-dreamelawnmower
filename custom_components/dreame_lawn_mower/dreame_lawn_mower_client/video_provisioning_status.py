"""Classify privacy-safe Dreame XP2P provisioning diagnostics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

XP2P_PROVISIONING_DEVICE_TRIPLE_MISSING = "device_triple_missing"
XP2P_PROVISIONING_DEVICE_PERMISSION_DENIED = "device_permission_denied"

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
_DEVICE_PERMISSION_DENIED_MESSAGES = frozenset(
    {
        "用户对该设备无权限",
        "user has no permission for this device",
        "user does not have permission for this device",
    }
)


def classify_xp2p_provisioning_issue(
    diagnostics: Mapping[str, Any],
    *,
    missing_required: Sequence[str],
) -> str | None:
    """Return a stable provisioning issue from sanitized cloud-stage evidence."""
    missing = frozenset(missing_required)
    matching_stages: set[str] = set()
    account_is_ineligible = False
    stages = diagnostics.get("stages")
    if not _is_sequence(stages):
        return None

    for stage in stages:
        if not isinstance(stage, Mapping):
            continue
        stage_name = stage.get("stage")
        if stage_name == "cloud_user_eligibility":
            result = stage.get("result")
            if isinstance(result, Mapping):
                account_is_ineligible = _is_explicit_false(
                    result.get("is_device_user")
                )
            continue
        if stage_name not in _DEVICE_TRIPLE_STAGES:
            continue
        response = stage.get("response")
        if not isinstance(response, Mapping):
            continue
        if (
            stage_name == "cloud_p2p_info"
            and "p2p_info" in missing
            and _has_device_permission_denied_response(response)
        ):
            matching_stages.add(XP2P_PROVISIONING_DEVICE_PERMISSION_DENIED)
        if _has_device_triple_missing_response(response):
            matching_stages.add(str(stage_name))

    if (
        account_is_ineligible
        and XP2P_PROVISIONING_DEVICE_PERMISSION_DENIED in matching_stages
    ):
        return XP2P_PROVISIONING_DEVICE_PERMISSION_DENIED
    if (
        _DEVICE_TRIPLE_REQUIRED_FIELDS.issubset(missing)
        and matching_stages == _DEVICE_TRIPLE_STAGES
    ):
        return XP2P_PROVISIONING_DEVICE_TRIPLE_MISSING
    return None


def _is_explicit_false(value: Any) -> bool:
    if value is False or value == 0:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in {"false", "0", "no"}
    return False


def _has_device_permission_denied_response(response: Mapping[str, Any]) -> bool:
    messages = response.get("messages")
    if not _is_sequence(messages):
        return False

    return any(
        isinstance(item, Mapping)
        and str(item.get("text") or "").strip().casefold()
        in _DEVICE_PERMISSION_DENIED_MESSAGES
        for item in messages
    )


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
