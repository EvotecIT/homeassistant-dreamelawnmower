"""Dreame mower families observed in the app's model-selection table.

The first five characters choose a family; remaining alphanumeric characters
identify variants. Family recognition supplies a fallback name and catalog,
not a retail SKU, rated lawn area or permission to enable a device feature.
"""

from __future__ import annotations

from typing import Final

DREAME_MODEL_FAMILY_NAMES: Final[dict[str, str]] = {
    "p2255": "A1",
    "g2422": "A1 Pro",
    "g2540": "A1 Pro",
    "g2408": "A2",
    "g2568": "A2",
    "q2501": "A3",
    "g2541": "A3 Pro",
}


def dreame_model_family(model: str | None) -> str | None:
    """Recognize a Dreame mower ID or bare model token without guessing its SKU."""
    token = str(model or "").strip().casefold()
    if token.startswith("dreame.mower."):
        token = token.removeprefix("dreame.mower.")
    if not token.isascii() or not token.isalnum():
        return None
    family = token[:5]
    return family if family in DREAME_MODEL_FAMILY_NAMES else None
