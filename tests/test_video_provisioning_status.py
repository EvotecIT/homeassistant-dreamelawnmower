"""Fixture-driven XP2P provisioning classification contracts."""

from __future__ import annotations

from copy import deepcopy

from dreame_lawn_mower_client import (
    XP2P_PROVISIONING_DEVICE_TRIPLE_MISSING,
    DreameLawnMowerCameraStreamRuntimeInputs,
    classify_xp2p_provisioning_issue,
)

from .fixture_data import load_json_fixture


def test_q2501a_ru_trace_classifies_missing_device_triple() -> None:
    diagnostics = load_json_fixture("q2501a_ru_xp2p_unprovisioned.json")
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="sanitized-device",
        diagnostics=diagnostics,
    )

    assert (
        inputs.provisioning_issue
        == XP2P_PROVISIONING_DEVICE_TRIPLE_MISSING
    )
    assert inputs.as_dict(redact=True)["provisioning_issue"] == (
        XP2P_PROVISIONING_DEVICE_TRIPLE_MISSING
    )


def test_q2501a_eu_trace_remains_ready_without_provisioning_issue() -> None:
    diagnostics = load_json_fixture("q2501a_eu_xp2p_ready.json")
    inputs = DreameLawnMowerCameraStreamRuntimeInputs(
        source="dreame_third_video_tx",
        did="sanitized-device",
        product_id="present",
        device_name="present",
        p2p_info="present",
        diagnostics=diagnostics,
    )

    assert inputs.ready is True
    assert inputs.provisioning_issue is None


def test_generic_missing_inputs_are_not_mislabeled_as_device_triple_issue() -> None:
    diagnostics = deepcopy(
        load_json_fixture("q2501a_ru_xp2p_unprovisioned.json")
    )
    diagnostics["stages"][2]["response"]["messages"][0]["text"] = (
        "temporary service failure"
    )

    issue = classify_xp2p_provisioning_issue(
        diagnostics,
        missing_required=("product_id", "device_name", "p2p_info"),
    )

    assert issue is None
