"""Hardware capability checks shared by client writes and Home Assistant entities."""

from __future__ import annotations

MANUAL_MOWING_HEIGHT_MODELS = frozenset(
    {
        "mova.mower.g2405a",
        "g2405a",
        "mova.mower.g2405b",
        "g2405b",
        "mova.mower.g2405c",
        "g2405c",
        "mova.mower.g2420a",
        "g2420a",
        "mova.mower.g2420b",
        "g2420b",
        "mova.mower.g2552",
        "g2552",
        "mova.mower.g2583",
        "g2583",
    }
)
MANUAL_MOWING_HEIGHT_DISPLAY_MODELS = frozenset(
    {"viax 300", "mova viax 300"}
)


def mowing_height_adjustment_supported(
    model: str | None, display_model: str | None = None
) -> bool:
    """Return whether the mower supports electronic cutting-height writes."""
    return (
        str(model or "").strip().casefold() not in MANUAL_MOWING_HEIGHT_MODELS
        and str(display_model or "").strip().casefold()
        not in MANUAL_MOWING_HEIGHT_DISPLAY_MODELS
    )
