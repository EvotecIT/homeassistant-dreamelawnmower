"""Contract tests for stable mower feature-capability resolution."""

from __future__ import annotations

from types import SimpleNamespace

from dreame_lawn_mower_client import (
    CAPABILITY_SOURCE_ADVERTISED,
    CAPABILITY_SOURCE_MODEL,
    CAPABILITY_SOURCE_OBSERVED,
    CAPABILITY_SOURCE_UNKNOWN,
    CAPABILITY_SUPPORTED,
    CAPABILITY_UNKNOWN,
    CAPABILITY_UNSUPPORTED,
    FEATURE_LIVE_VIDEO,
    resolve_feature_capability,
    resolved_feature_capabilities,
)


def _snapshot(
    model: str,
    *,
    capabilities: tuple[str, ...] = (),
    raw_info: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        descriptor=SimpleNamespace(model=model),
        capabilities=capabilities,
        raw_info=raw_info or {},
    )


def test_known_a1_is_explicitly_video_unsupported() -> None:
    capability = resolve_feature_capability(
        FEATURE_LIVE_VIDEO,
        snapshot=_snapshot("dreame.mower.p2255"),
    )

    assert capability.state == CAPABILITY_UNSUPPORTED
    assert capability.source == CAPABILITY_SOURCE_MODEL


def test_known_a2_is_video_supported_before_metadata_arrives() -> None:
    capability = resolve_feature_capability(
        FEATURE_LIVE_VIDEO,
        snapshot=_snapshot("dreame.mower.g2408"),
    )

    assert capability.state == CAPABILITY_SUPPORTED
    assert capability.source == CAPABILITY_SOURCE_MODEL


def test_validated_a3_is_video_supported_before_metadata_arrives() -> None:
    capability = resolve_feature_capability(
        FEATURE_LIVE_VIDEO,
        snapshot=_snapshot("dreame.mower.q2501a"),
    )

    assert capability.state == CAPABILITY_SUPPORTED
    assert capability.source == CAPABILITY_SOURCE_MODEL


def test_unknown_model_without_evidence_remains_unknown() -> None:
    capability = resolve_feature_capability(
        FEATURE_LIVE_VIDEO,
        snapshot=_snapshot("dreame.mower.future"),
    )

    assert capability.state == CAPABILITY_UNKNOWN
    assert capability.source == CAPABILITY_SOURCE_UNKNOWN


def test_unvalidated_recognized_model_remains_unknown() -> None:
    """A known model name is not evidence for an unvalidated feature."""
    capability = resolve_feature_capability(
        FEATURE_LIVE_VIDEO,
        descriptor=SimpleNamespace(model="dreame.mower.g2568d"),
    )

    assert capability.state == CAPABILITY_UNKNOWN
    assert capability.source == CAPABILITY_SOURCE_UNKNOWN


def test_external_descriptor_fills_missing_snapshot_model() -> None:
    capability = resolve_feature_capability(
        FEATURE_LIVE_VIDEO,
        snapshot=SimpleNamespace(
            descriptor=SimpleNamespace(model=None),
            capabilities=(),
            raw_info={},
        ),
        descriptor=SimpleNamespace(model="dreame.mower.p2255"),
    )

    assert capability.state == CAPABILITY_UNSUPPORTED
    assert capability.source == CAPABILITY_SOURCE_MODEL


def test_unknown_model_can_promote_from_advertised_metadata() -> None:
    capability = resolve_feature_capability(
        FEATURE_LIVE_VIDEO,
        snapshot=_snapshot(
            "dreame.mower.future",
            raw_info={"deviceInfo": {"permit": "pincode,video"}},
        ),
    )

    assert capability.state == CAPABILITY_SUPPORTED
    assert capability.source == CAPABILITY_SOURCE_ADVERTISED


def test_prior_advertisement_remains_advertised_not_observed() -> None:
    capability = resolve_feature_capability(
        FEATURE_LIVE_VIDEO,
        snapshot=_snapshot("dreame.mower.future"),
        advertised=True,
    )

    assert capability.state == CAPABILITY_SUPPORTED
    assert capability.source == CAPABILITY_SOURCE_ADVERTISED


def test_runtime_observation_can_correct_a_stale_model_catalog() -> None:
    capability = resolve_feature_capability(
        FEATURE_LIVE_VIDEO,
        snapshot=_snapshot("dreame.mower.p2255"),
        observed=True,
    )

    assert capability.state == CAPABILITY_SUPPORTED
    assert capability.source == CAPABILITY_SOURCE_OBSERVED


def test_generic_advertised_feature_uses_the_same_tri_state_contract() -> None:
    capability = resolve_feature_capability(
        "map",
        snapshot=_snapshot(
            "dreame.mower.future",
            capabilities=("lidar_navigation", "map"),
        ),
    )

    assert capability.state == CAPABILITY_SUPPORTED
    assert capability.source == CAPABILITY_SOURCE_ADVERTISED


def test_home_assistant_capability_matrix_is_compact_and_extensible() -> None:
    snapshot = _snapshot(
        "dreame.mower.future",
        capabilities=("map",),
    )

    assert resolved_feature_capabilities(
        snapshot,
        features=(FEATURE_LIVE_VIDEO, "map"),
    ) == {
        "live_video": {
            "state": CAPABILITY_UNKNOWN,
            "source": CAPABILITY_SOURCE_UNKNOWN,
        },
        "map": {
            "state": CAPABILITY_SUPPORTED,
            "source": CAPABILITY_SOURCE_ADVERTISED,
        },
    }
