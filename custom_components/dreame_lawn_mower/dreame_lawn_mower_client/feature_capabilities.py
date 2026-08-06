"""Stable feature-capability resolution for mower consumers."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Final

from .models import snapshot_advertises_video

FEATURE_LIVE_VIDEO: Final = "live_video"

CAPABILITY_SUPPORTED: Final = "supported"
CAPABILITY_UNSUPPORTED: Final = "unsupported"
CAPABILITY_UNKNOWN: Final = "unknown"

CAPABILITY_SOURCE_OBSERVED: Final = "observed"
CAPABILITY_SOURCE_MODEL: Final = "model"
CAPABILITY_SOURCE_ADVERTISED: Final = "advertised"
CAPABILITY_SOURCE_UNKNOWN: Final = "unknown"

# Keep this catalog sparse. An omitted feature is unknown, not unsupported.
# Runtime evidence remains available for new models and hardware revisions.
MODEL_FEATURE_CAPABILITIES: Final[dict[str, dict[str, str]]] = {
    "dreame.mower.p2255": {
        FEATURE_LIVE_VIDEO: CAPABILITY_UNSUPPORTED,
    },
    "dreame.mower.g2408": {
        FEATURE_LIVE_VIDEO: CAPABILITY_SUPPORTED,
    },
    "dreame.mower.q2501a": {
        FEATURE_LIVE_VIDEO: CAPABILITY_SUPPORTED,
    },
}


@dataclass(slots=True, frozen=True)
class DreameLawnMowerFeatureCapability:
    """Resolved support state for one stable mower feature."""

    state: str
    source: str

    def as_dict(self) -> dict[str, str]:
        """Return the Home Assistant state-attribute representation."""
        return {
            "state": self.state,
            "source": self.source,
        }


def _normalized_feature(value: Any) -> str:
    return str(value or "").strip().casefold()


def _descriptor_model(snapshot: Any, descriptor: Any | None) -> str | None:
    for current_descriptor in (getattr(snapshot, "descriptor", None), descriptor):
        model = getattr(current_descriptor, "model", None)
        if isinstance(model, str) and (normalized := model.strip().casefold()):
            return normalized
    return None


def _snapshot_advertises_feature(snapshot: Any, feature: str) -> bool:
    if snapshot is None:
        return False
    if feature == FEATURE_LIVE_VIDEO and snapshot_advertises_video(snapshot):
        return True
    capabilities = getattr(snapshot, "capabilities", ()) or ()
    return any(_normalized_feature(item) == feature for item in capabilities)


def resolve_feature_capability(
    feature: str,
    *,
    snapshot: Any = None,
    descriptor: Any = None,
    observed: bool = False,
    advertised: bool = False,
) -> DreameLawnMowerFeatureCapability:
    """Resolve one feature without treating missing metadata as unsupported.

    Proven runtime behavior has the strongest precedence so new hardware revisions
    are not blocked by a stale model catalog. Exact model facts come next, followed
    by passive capability metadata. Silence remains unknown.
    """
    normalized_feature = _normalized_feature(feature)
    if observed:
        return DreameLawnMowerFeatureCapability(
            CAPABILITY_SUPPORTED,
            CAPABILITY_SOURCE_OBSERVED,
        )

    model = _descriptor_model(snapshot, descriptor)
    model_state = (
        MODEL_FEATURE_CAPABILITIES.get(model, {}).get(normalized_feature)
        if model is not None
        else None
    )
    if model_state is not None:
        return DreameLawnMowerFeatureCapability(
            model_state,
            CAPABILITY_SOURCE_MODEL,
        )

    if advertised or _snapshot_advertises_feature(snapshot, normalized_feature):
        return DreameLawnMowerFeatureCapability(
            CAPABILITY_SUPPORTED,
            CAPABILITY_SOURCE_ADVERTISED,
        )

    return DreameLawnMowerFeatureCapability(
        CAPABILITY_UNKNOWN,
        CAPABILITY_SOURCE_UNKNOWN,
    )


def resolved_feature_capabilities(
    snapshot: Any,
    *,
    descriptor: Any = None,
    features: tuple[str, ...] = (FEATURE_LIVE_VIDEO,),
    observed: Collection[str] = (),
    advertised: Collection[str] = (),
) -> dict[str, dict[str, str]]:
    """Return a compact capability matrix for Home Assistant consumers."""
    observed_features = {_normalized_feature(feature) for feature in observed}
    advertised_features = {
        _normalized_feature(feature) for feature in advertised
    }
    return {
        feature: resolve_feature_capability(
            feature,
            snapshot=snapshot,
            descriptor=descriptor,
            observed=_normalized_feature(feature) in observed_features,
            advertised=_normalized_feature(feature) in advertised_features,
        ).as_dict()
        for feature in features
    }
