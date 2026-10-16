"""Original recovery guidance and explicit uncertainty for mower device codes.

Presentation is independent of operational fault classification. App categories
and suppression hints never clear a hard fault or authorize a mower operation.
"""

from __future__ import annotations

from typing import Any

from .device_code_semantics import (
    MowerDeviceCodeTier,
    mower_device_code,
    mower_device_code_definition,
)
from .model_families import dreame_model_family

_A1_APP_MEANINGS = {
    0: "robot_lifted",
    16: "scheduled_mowing_suspended_low_light",
    19: "location_lost",
    47: "new_map_created",
    53: "scheduled_mowing_started",
    59: "battery_temperature_too_low",
    70: "resuming_unfinished_task",
    73: "top_cover_open",
}


def mower_device_code_context(
    value: object,
    *,
    model: str | None = None,
    firmware: str | None = None,
) -> dict[str, Any] | None:
    """Explain a code without guessing firmware transitions or changing its tier."""
    code = mower_device_code(value)
    if code in (None, -1):
        return None
    definition = mower_device_code_definition(code, model=model)
    family = dreame_model_family(model)
    name = definition.name if definition else f"unknown_mower_device_code_{code}"
    tier = definition.tier.value if definition else "unknown"
    alternatives: list[str] = []
    if family in {"p2255", "g2422", "g2540"}:
        alternative = _A1_APP_MEANINGS.get(code)
        if alternative and alternative != name:
            alternatives = [name, alternative]
    if code == 25 and family in {"g2408", "g2568", "q2501", "g2541"}:
        alternatives = ["map_file_damaged", "robot_startup_abnormality"]
    checks = _recovery_checks(name, tier, bool(alternatives))
    return {
        "code": code,
        "name": name,
        "tier": tier,
        "firmware": firmware,
        "catalog_source": "family_catalog"
        if family in {"g2408", "g2568", "q2501", "g2541"}
        else "field_compatibility_and_shared_registry"
        if family in {"p2255", "g2422", "g2540"}
        else "shared_registry",
        "meaning_ambiguous": bool(alternatives),
        "possible_meanings": alternatives,
        "firmware_transition_known": False if alternatives else None,
        "recovery_checks": checks,
        "automatic_recovery": False,
    }


def _recovery_checks(name: str, tier: str, ambiguous: bool) -> list[str]:
    if ambiguous:
        return [
            "Compare the message in the vendor app with the mower's actual state.",
            "Keep the raw code, full model and firmware in a sanitized "
            "diagnostic report.",
            "Preserve existing maps and settings before considering a reset.",
        ]
    if name.startswith("unknown_mower_device_code_"):
        return [
            "Check the vendor app for the current message.",
            "Capture sanitized diagnostics before and after the condition changes.",
        ]
    if tier == MowerDeviceCodeTier.INFO:
        return []
    if any(part in name for part in ("blade", "cutter", "wheel", "lift_motor")):
        return [
            "Stop the mower and follow the manufacturer's power-off safety "
            "procedure before inspection.",
            "Check the indicated component for obstruction, wear or visible damage.",
            "If the condition returns, use the app's support flow with the "
            "raw code and firmware.",
        ]
    if "temperature" in name or "overheat" in name:
        return [
            "Allow the mower to return to its specified operating temperature.",
            "Check the vendor app before resuming; do not bypass "
            "temperature protection.",
        ]
    if any(part in name for part in ("lidar", "camera_dirty", "location", "map_")):
        return [
            "Check the app's positioning and map status and inspect "
            "exposed sensors safely.",
            "Preserve the map before attempting a remap or reset.",
        ]
    return [
        "Check the vendor app's current message and confirm the mower is "
        "safe before resuming."
    ]
