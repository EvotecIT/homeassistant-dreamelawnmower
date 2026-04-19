"""Regression checks for app-derived property annotations."""

from __future__ import annotations

from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerDescriptor,
    decode_mower_status_blob,
    decode_mower_task_status,
    key_definition_label,
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
    assert error_entry["decoded_label_source"] == "bundled_mower_errors"


def test_property_annotations_prefer_cloud_key_definition_labels() -> None:
    key_definition = {
        "payload": {
            "keyDefine": {
                "2.1": {
                    "en": {"13": "Charging Complete From Cloud"},
                    "pl": {"13": "Ladowanie zakonczone"},
                }
            }
        }
    }

    entry = DreameLawnMowerClient._annotate_cloud_property_entry(
        {"key": "2.1", "value": 13},
        language="pl",
        key_definition=key_definition,
    )

    assert entry["decoded_label"] == "Ladowanie zakonczone"
    assert entry["decoded_label_source"] == "cloud_key_definition"


def test_key_definition_label_falls_back_to_english_and_raw_payload() -> None:
    raw_key_definition = {
        "keyDefine": {
            "2.1": {
                "en": {"13": "Charging Completed"},
            }
        }
    }

    assert (
        key_definition_label(
            raw_key_definition,
            "2.1",
            13,
            language="missing",
        )
        == "Charging Completed"
    )
    assert key_definition_label(raw_key_definition, "2.2", 31) is None


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
    assert entry["status_blob"]["candidate_battery_level"] == 100
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
    assert decoded.candidate_battery_level == 100


def test_status_blob_decoder_exposes_candidate_battery_byte() -> None:
    decoded = decode_mower_status_blob(
        [
            206,
            0,
            0,
            0,
            0,
            0,
            0,
            32,
            0,
            0,
            0,
            93,
            5,
            35,
            135,
            1,
            128,
            186,
            196,
            206,
        ]
    )

    assert decoded is not None
    assert decoded.candidate_battery_level == 93


def test_property_annotations_mark_runtime_status_blob_frame() -> None:
    entry = DreameLawnMowerClient._annotate_cloud_property_entry(
        {
            "key": "1.4",
            "value": [
                206,
                79,
                2,
                128,
                77,
                0,
                45,
                7,
                1,
                0,
                125,
                1,
                34,
                1,
                125,
                1,
                50,
                1,
                246,
                255,
                234,
                255,
                1,
                100,
                94,
                5,
                108,
                207,
                0,
                127,
                28,
                0,
                206,
            ],
        },
        language="en",
    )

    assert entry["property_hint"] == "runtime_status_blob"
    assert entry["value_bytes_len"] == 33
    assert entry["status_blob"]["frame_valid"] is True
    assert entry["status_blob"]["notes"] == ("unexpected_length",)


def test_task_status_decoder_keeps_obvious_task_fields() -> None:
    decoded = decode_mower_task_status(
        '{"d":{"exe":true,"o":6,"status":true},"t":"TASK"}'
    )

    assert decoded == {
        "type": "TASK",
        "executing": True,
        "status": True,
        "operation": 6,
    }


def test_property_annotations_decode_task_status_property() -> None:
    entry = DreameLawnMowerClient._annotate_cloud_property_entry(
        {
            "key": "2.50",
            "value": '{"d":{"exe":true,"o":6,"status":true},"t":"TASK"}',
        },
        language="en",
    )

    assert entry["property_hint"] == "task_status"
    assert entry["task_status"] == {
        "type": "TASK",
        "executing": True,
        "status": True,
        "operation": 6,
    }


def test_scan_cloud_properties_returns_summary_with_dynamic_labels() -> None:
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
    client._sync_get_cloud_properties = lambda keys: [
        {"key": "2.1", "value": 13},
        {"key": "9.9", "value": '{"current_map":true}'},
    ]
    client._sync_get_cloud_key_definition = lambda language: {
        "payload": {
            "keyDefine": {
                "2.1": {"en": {"13": "Charging Completed From Cloud"}},
            }
        }
    }

    result = client._sync_scan_cloud_properties(
        keys=("2.1", "9.9"),
        siids=None,
        piid_start=1,
        piid_end=1,
        chunk_size=10,
        language="en",
        only_values=True,
    )

    assert result["entries"][0]["decoded_label"] == "Charging Completed From Cloud"
    assert result["entries"][0]["decoded_label_source"] == "cloud_key_definition"
    assert result["summary"]["candidate_map_entry_count"] == 1
    assert result["summary"]["candidate_map_properties"][0]["key"] == "9.9"
