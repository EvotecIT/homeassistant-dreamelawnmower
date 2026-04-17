"""Regression checks for app-derived property annotations."""

from __future__ import annotations

from dreame_lawn_mower_client import DreameLawnMowerClient


def test_property_annotations_label_known_mower_state_and_error_keys() -> None:
    state_entry = DreameLawnMowerClient._annotate_cloud_property_entry(
        {"key": "2.1", "value": 13},
        language="en",
    )
    error_entry = DreameLawnMowerClient._annotate_cloud_property_entry(
        {"key": "2.2", "value": 31},
        language="en",
    )

    assert state_entry["property_hint"] == "mower_state"
    assert state_entry["decoded_label"] == "Charging Completed"
    assert error_entry["property_hint"] == "mower_error"
    assert error_entry["decoded_label"] == "Left wheel speed"


def test_property_annotations_mark_raw_status_blob_without_decoding_it() -> None:
    entry = DreameLawnMowerClient._annotate_cloud_property_entry(
        {"key": "1.1", "value": [206, 0, 0, 0]},
        language="en",
    )

    assert entry["property_hint"] == "raw_status_blob"
    assert entry["value_bytes_len"] == 4
    assert entry["value_bytes_hex"] == "ce000000"
