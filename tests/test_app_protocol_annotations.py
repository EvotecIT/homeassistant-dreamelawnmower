"""Regression checks for app-derived property annotations."""

from __future__ import annotations

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerDescriptor,
    decode_mower_status_blob,
)


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


def test_property_annotations_mark_raw_status_blob_frame() -> None:
    entry = DreameLawnMowerClient._annotate_cloud_property_entry(
        {
            "key": "1.1",
            "value": [
                206,
                0,
                0,
                0,
                0,
                16,
                0,
                33,
                0,
                0,
                0,
                100,
                1,
                255,
                0,
                0,
                128,
                212,
                196,
                206,
            ],
        },
        language="en",
    )

    assert entry["property_hint"] == "raw_status_blob"
    assert entry["value_bytes_len"] == 20
    assert entry["value_bytes_hex"].startswith("ce000000")
    assert entry["status_blob"]["frame_valid"] is True
    assert entry["status_blob"]["length"] == 20
    assert entry["status_blob"]["bytes_by_index"]["11"] == 100
    assert entry["status_blob"]["notes"] == ()


def test_status_blob_decoder_rejects_non_blob_values() -> None:
    assert decode_mower_status_blob({"not": "a blob"}) is None


def test_status_blob_decoder_keeps_unexpected_frames_as_evidence() -> None:
    decoded = decode_mower_status_blob("[1, 2, 3]")

    assert decoded is not None
    assert decoded.supported is True
    assert decoded.frame_valid is False
    assert decoded.notes == ("unexpected_length", "invalid_frame_markers")


def test_client_status_blob_prefers_cached_realtime_payload() -> None:
    client = DreameLawnMowerClient(
        username="user",
        password="pass",
        country="eu",
        account_type="dreame",
        descriptor=DreameLawnMowerDescriptor(
            did="1",
            name="Mower",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
    )
    client._device = type(
        "FakeDevice",
        (),
        {
            "realtime_properties": {
                "1.1": {
                    "value": [
                        206,
                        0,
                        0,
                        0,
                        0,
                        16,
                        0,
                        33,
                        0,
                        0,
                        0,
                        100,
                        1,
                        255,
                        0,
                        0,
                        128,
                        212,
                        196,
                        206,
                    ]
                },
            }
        },
    )()

    decoded = client._sync_get_status_blob(include_cloud=False)

    assert decoded is not None
    assert decoded.source == "realtime"
    assert decoded.frame_valid is True
