"""Family recognition never invents a retail SKU or hardware validation."""

from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.debug import _collect_triage_summary
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    app_model_profiles,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.compatibility import (
    build_compatibility_summary,
)
from dreame_lawn_mower_client import (
    model_identity_summary,
    profile_capability_evidence,
    resolve_feature_capability,
)


@pytest.mark.parametrize(
    ("model", "family", "variant", "name"),
    [
        ("dreame.mower.p2255x", "p2255", "x", "A1"),
        ("dreame.mower.g2422z", "g2422", "z", "A1 Pro"),
        ("dreame.mower.g2540a", "g2540", "a", "A1 Pro"),
        ("dreame.mower.g2408a", "g2408", "a", "A2"),
        ("dreame.mower.g2568e", "g2568", "e", "A2"),
        ("dreame.mower.q2501c", "q2501", "c", "A3"),
        ("dreame.mower.g2541g", "g2541", "g", "A3 Pro"),
    ],
)
def test_family_variants_are_recognized_without_claiming_retail_validation(
    model,
    family,
    variant,
    name,
):
    result = model_identity_summary(model)
    assert result["recognized"] is True
    assert result["exact_retail_name_known"] is False
    assert result["family"] == family
    assert result["variant"] == variant
    assert result["display_model"] == name
    assert result["name_source"] == "family"
    assert result["validation"] == "not_established_by_identity"


def test_identity_preserves_exact_and_cloud_names_before_family_fallback():
    assert (
        model_identity_summary("dreame.mower.g2568d", cloud_name="Other")[
            "display_model"
        ]
        == "A2 3000"
    )
    result = model_identity_summary("dreame.mower.g2568e", cloud_name="A2 Regional")
    assert result["display_model"] == "A2 Regional"
    assert result["name_source"] == "cloud"
    assert result["exact_retail_name_known"] is False


@pytest.mark.parametrize(
    "model", [None, "dreame.mower.g2541/", "other.mower.g2541g", "dreame.vacuum.g2541g"]
)
def test_invalid_model_identifiers_cannot_inherit_app_profiles(model):
    result = model_identity_summary(model)
    assert result["recognized"] is False
    assert result["app_profile_known"] is False
    assert result["family_name"] is None


def test_observed_unnamed_model_remains_honest_about_missing_family_and_profile():
    result = model_identity_summary("dreame.mower.g3255")
    assert result["recognized"] is True
    assert result["observed_identifier"] is True
    assert result["family_name"] is None
    assert result["app_profile_known"] is False
    assert result["display_model"] == "dreame.mower.g3255"


def test_profile_only_evidence_does_not_authorize_operations_or_video():
    descriptor = SimpleNamespace(model="dreame.mower.g2541g")
    result = profile_capability_evidence(descriptor=descriptor)
    for feature in ("all_wheel_drive", "ai_mapping", "built_in_cellular"):
        assert result[feature]["app_profile_flag"] is True
        assert result[feature]["state"] == "unknown"
        assert result[feature]["source"] == "app_profile"
        assert result[feature]["reason"] == "app_profile_only"
    assert (
        resolve_feature_capability("live_video", descriptor=descriptor).state
        == "unknown"
    )


def test_absent_and_false_app_flags_remain_distinct_from_unsupported():
    result = profile_capability_evidence(
        descriptor=SimpleNamespace(model="dreame.mower.p2255")
    )
    assert result["camera_hardware"]["app_profile_flag"] is False
    assert result["camera_hardware"]["source"] == "app_profile"
    assert result["camera_hardware"]["state"] == "unknown"
    assert result["all_wheel_drive"]["app_profile_flag"] is None
    assert result["all_wheel_drive"]["source"] == "unknown"
    for feature in ("map_learning", "dual_camera_ui", "cruise_history_ui"):
        assert result[feature]["app_profile_flag"] is False
        assert result[feature]["source"] == "app_profile"
    assert (
        resolve_feature_capability(
            "live_video", descriptor=SimpleNamespace(model="dreame.mower.p2255")
        ).state
        == "unsupported"
    )


def test_omitted_fields_in_a_new_profile_have_no_asserted_evidence(monkeypatch):
    monkeypatch.setitem(
        app_model_profiles.APP_MODEL_PROFILES,
        "g9999",
        app_model_profiles.AppModelProfile(camera=True, pin_code=True),
    )
    report = profile_capability_evidence(
        descriptor=SimpleNamespace(model="dreame.mower.g9999a"),
    )
    for feature in ("map_learning", "dual_camera_ui", "cruise_history_ui"):
        assert report[feature]["app_profile_flag"] is None
        assert report[feature]["source"] == "unknown"
        assert report[feature]["app_profile_builds"] == []


def test_runtime_capability_evidence_precedes_app_ui_flags():
    snapshot = SimpleNamespace(
        descriptor=SimpleNamespace(model="dreame.mower.g2422"),
        capabilities=("camera_hardware",),
    )
    result = profile_capability_evidence(snapshot)["camera_hardware"]
    assert result["state"] == "supported"
    assert result["source"] == "advertised"
    assert result["app_profile_flag"] is False
    assert (
        resolve_feature_capability(
            "camera_hardware", snapshot=snapshot, observed=True
        ).source
        == "observed"
    )


def test_diagnostics_recognize_family_variants_without_unknown_model_warning():
    result = _collect_triage_summary(
        snapshot=SimpleNamespace(
            descriptor=SimpleNamespace(model="dreame.mower.g2568e"),
            available=True,
        ),
        device=None,
        state_reconciliation={},
        unknown_property_summary={},
        realtime_summary={},
    )
    assert result["known_model"] is True
    assert result["model_identity"]["exact_retail_name_known"] is False
    assert "model_not_in_known_model_map" not in result["issues"]


def test_compatibility_preserves_cloud_product_name_without_custom_account_names():
    descriptor = SimpleNamespace(
        model="dreame.mower.g2568e",
        display_model="A2 Regional",
        account_type="dreame",
        country="eu",
        raw={
            "customName": "Private garden",
            "deviceInfo": {"displayName": "A2 Regional"},
        },
    )
    result = build_compatibility_summary(
        descriptor,
        SimpleNamespace(firmware_version="4.3.6_0625"),
        features={},
        schedules=None,
    )
    assert result["model_identity"]["name_source"] == "cloud"
    assert result["model_identity"]["display_model"] == "A2 Regional"
    assert result["model_identity"]["exact_retail_name_known"] is False
    assert "Private garden" not in repr(result)
