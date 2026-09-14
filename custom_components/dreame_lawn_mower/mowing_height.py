"""Model-specific mowing-height capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.exceptions import HomeAssistantError

MOWING_HEIGHT_MIN_CM = 3.0
MOWING_HEIGHT_MAX_CM = 7.0
MOWING_HEIGHT_TALL_MAX_CM = 10.0
MOWING_HEIGHT_MANUAL_MIN_CM = 2.0
MOWING_HEIGHT_MANUAL_MAX_CM = 6.0
MOWING_HEIGHT_STEP_CM = 0.5

TALL_MOWING_HEIGHT_MODEL_PREFIXES = (
    "dreame.mower.q2501",
    "q2501",
    "dreame.mower.g2541",
    "g2541",
    "mova.mower.g2529",
    "g2529",
    "mova.mower.g2584",
    "g2584",
)

MANUAL_MOWING_HEIGHT_MODELS = frozenset(
    {
        "mova.mower.g2405a",
        "g2405a",
        "mova.mower.g2405b",
        "g2405b",
        "mova.mower.g2405c",
        "g2405c",
        "mova.mower.g2552",
        "g2552",
        "mova.mower.g2583",
        "g2583",
    }
)


def mowing_height_limits(model: str | None) -> tuple[float, float]:
    """Return official mower-family cutting-height bounds."""
    normalized = str(model or "").strip().casefold()
    if normalized in MANUAL_MOWING_HEIGHT_MODELS:
        return MOWING_HEIGHT_MANUAL_MIN_CM, MOWING_HEIGHT_MANUAL_MAX_CM
    maximum = (
        MOWING_HEIGHT_TALL_MAX_CM
        if normalized.startswith(TALL_MOWING_HEIGHT_MODEL_PREFIXES)
        else MOWING_HEIGHT_MAX_CM
    )
    return MOWING_HEIGHT_MIN_CM, maximum


def mowing_height_adjustment_supported(model: str | None) -> bool:
    """Return whether the mower supports changing blade height electronically."""
    normalized = str(model or "").strip().casefold()
    return normalized not in MANUAL_MOWING_HEIGHT_MODELS


def guard_mowing_height_changes(
    model: str | None,
    changes: Mapping[str, Any],
) -> None:
    """Reject electronic height writes for a manual-height mower."""
    if (
        "mowing_height_cm" in changes
        and not mowing_height_adjustment_supported(model)
    ):
        raise HomeAssistantError("Mowing height is adjusted manually on this mower.")
