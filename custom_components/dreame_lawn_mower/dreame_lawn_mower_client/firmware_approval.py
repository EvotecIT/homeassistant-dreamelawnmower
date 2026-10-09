"""Shared firmware approval response contract for native and sync clients."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .payload_utils import _as_optional_text, _json_safe


def firmware_approval_result(raw: Any) -> dict[str, Any]:
    """Preserve cloud wrapper acceptance and separate device result evidence."""
    result: dict[str, Any] = {
        "source": "cloud_manual_firmware_update",
        "available": isinstance(raw, Mapping),
        "accepted": False,
        "success": False,
    }
    if isinstance(raw, Mapping):
        code = raw.get("code")
        success = raw.get("success")
        data = raw.get("data")
        inner_code = data.get("code") if isinstance(data, Mapping) else None
        inner_success = data.get("success") if isinstance(data, Mapping) else None
        accepted = bool(success) if isinstance(success, bool) else code == 0
        result.update(
            {
                "code": code,
                "accepted": accepted,
                "success": accepted,
                "msg": _as_optional_text(raw.get("msg")),
                "data": _json_safe(data, max_depth=3),
                "wrapper_success": success if isinstance(success, bool) else None,
                "inner_code": inner_code,
                "inner_success": (
                    inner_success if isinstance(inner_success, bool) else None
                ),
            }
        )
    else:
        result["errors"] = [{"stage": "response", "error": "invalid_response"}]
    return result
