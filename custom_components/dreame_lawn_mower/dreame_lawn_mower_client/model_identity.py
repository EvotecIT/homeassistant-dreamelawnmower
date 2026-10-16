"""Explain family recognition separately from exact retail and field support."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from .app_model_profiles import app_model_profile, split_mower_model
from .model_families import DREAME_MODEL_FAMILY_NAMES, dreame_model_family
from .models import MODEL_NAME_MAP, display_name_for_model

# Observed raw identity only. No retail name or family is inferred from it.
OBSERVED_UNNAMED_MODEL_IDS: Final = frozenset({"dreame.mower.g3255"})


def descriptor_identity_summary(descriptor: Any) -> dict[str, Any]:
    """Use the vendor product-name field, never the user's custom device name."""
    raw = getattr(descriptor, "raw", None)
    info = raw.get("deviceInfo") if isinstance(raw, Mapping) else None
    cloud_name = info.get("displayName") if isinstance(info, Mapping) else None
    return model_identity_summary(
        getattr(descriptor, "model", None),
        cloud_name=cloud_name if isinstance(cloud_name, str) else None,
    )


def model_identity_summary(
    model: str | None,
    *,
    cloud_name: str | None = None,
) -> dict[str, Any]:
    """Report what the client recognizes without claiming hardware validation."""
    normalized = str(model or "").strip().casefold()
    cloud_name = cloud_name.strip() if cloud_name and cloud_name.strip() else None
    parsed = split_mower_model(normalized)
    family = dreame_model_family(normalized)
    exact = normalized in MODEL_NAME_MAP
    observed = normalized in OBSERVED_UNNAMED_MODEL_IDS
    profile = app_model_profile(normalized)
    recognized = exact or family is not None or observed
    return {
        "raw_model": model,
        "brand": parsed[0] if parsed else None,
        "family": parsed[1] if parsed else None,
        "variant": parsed[2] if parsed else None,
        "family_name": DREAME_MODEL_FAMILY_NAMES.get(family or ""),
        "recognized": recognized,
        "exact_retail_name_known": exact,
        "observed_identifier": exact or observed,
        "app_profile_known": profile is not None,
        "name_source": "exact_model"
        if exact
        else "cloud"
        if cloud_name
        else "family"
        if family is not None
        else "raw_identifier",
        "display_model": display_name_for_model(normalized, fallback_name=cloud_name),
        "validation": "not_established_by_identity",
    }
