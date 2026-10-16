"""Factual profile evidence from the six researched mower app builds.

These are app presentation facts, not firmware permissions or proven device
features. Missing flags stay unknown. Area/efficiency values are not retail
specifications. See docs/dreame-app-catalog.md for artifact provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

APP_PROFILE_BUILDS: Final = (911, 920, 926, 977, 978, 983)


@dataclass(frozen=True, slots=True)
class AppModelProfile:
    """Observed UI flags for one raw five-character model family."""

    camera: bool
    pin_code: bool
    map_learning: bool = False
    two_eye: bool = False
    cruise_history: bool = False
    all_wheel_drive: bool | None = None
    built_in_cellular: bool | None = None
    ai_mapping: bool | None = None

    def feature_flags(self) -> dict[str, bool | None]:
        """Keep hardware/UI evidence separate from operational availability."""
        return {
            "camera_hardware": self.camera,
            "pin_code_ui": self.pin_code,
            "map_learning": self.map_learning,
            "dual_camera_ui": self.two_eye,
            "cruise_history_ui": self.cruise_history,
            "all_wheel_drive": self.all_wheel_drive,
            # isOnboardRemote is consumed by cellular/Link-module screens.
            "built_in_cellular": self.built_in_cellular,
            "ai_mapping": self.ai_mapping,
        }


APP_MODEL_PROFILES: Final[dict[str, AppModelProfile]] = {
    "p2255": AppModelProfile(camera=False, pin_code=True),
    "g2422": AppModelProfile(camera=False, pin_code=True),
    "g2540": AppModelProfile(camera=False, pin_code=True),
    "g2408": AppModelProfile(camera=True, pin_code=True),
    "g2568": AppModelProfile(camera=True, pin_code=True, built_in_cellular=True),
    "q2501": AppModelProfile(
        camera=True,
        pin_code=True,
        all_wheel_drive=True,
        built_in_cellular=True,
        ai_mapping=True,
    ),
    "g2541": AppModelProfile(
        camera=True,
        pin_code=True,
        all_wheel_drive=True,
        built_in_cellular=True,
        ai_mapping=True,
    ),
    "g2420": AppModelProfile(
        camera=True,
        pin_code=False,
        map_learning=True,
        two_eye=True,
        cruise_history=True,
    ),
    "g2566": AppModelProfile(
        camera=True,
        pin_code=False,
        map_learning=True,
        two_eye=True,
        cruise_history=True,
    ),
    "g2529": AppModelProfile(camera=True, pin_code=True),
    "g2538": AppModelProfile(camera=True, pin_code=True),
}


def split_mower_model(model: str | None) -> tuple[str | None, str, str] | None:
    """Parse a raw mower ID without treating an arbitrary suffix as a SKU."""
    value = str(model or "").strip().casefold()
    brand: str | None = None
    if "." in value:
        parts = value.split(".")
        if len(parts) != 3 or parts[0] not in {"dreame", "mova"}:
            return None
        if parts[1] != "mower":
            return None
        brand, _, value = parts
    if not value.isascii() or not value.isalnum() or len(value) < 5:
        return None
    return brand, value[:5], value[5:]


def app_model_profile(model: str | None) -> AppModelProfile | None:
    """Return only an observed app profile, accepting future suffix variants."""
    parsed = split_mower_model(model)
    return APP_MODEL_PROFILES.get(parsed[1]) if parsed is not None else None
