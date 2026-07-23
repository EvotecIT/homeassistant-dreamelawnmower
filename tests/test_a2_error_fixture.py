"""Regression checks for the historical A2 code-31 diagnostic capture."""

from __future__ import annotations

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device_code_semantics,
)

from .fixture_data import load_json_fixture


def test_a2_capture_proves_the_old_vacuum_label_was_not_mower_evidence() -> None:
    payload = load_json_fixture("a2_paused_left_wheel_error_diagnostics.json")
    snapshot = payload["data"]["snapshot"]

    # Keep the immutable capture as provenance: the old client used the vacuum
    # enum to turn A2 code 31 into "left wheel speed".
    assert snapshot["state"] == "paused"
    assert snapshot["error_code"] == 31
    assert snapshot["error_name"] == "left_wheell_speed"

    # The mower-native A2 catalog identifies the same value as a recoverable
    # return-to-station alert, so it must not latch LawnMowerActivity.ERROR.
    model = snapshot["descriptor"]["model"]
    assert (
        device_code_semantics.mower_device_code_name(31, model=model)
        == "return_to_station_failed"
    )
    assert (
        device_code_semantics.mower_device_code_tier(31, model=model)
        is device_code_semantics.MowerDeviceCodeTier.ALERT
    )
    assert device_code_semantics.mower_fault_active(31, model=model) is False
