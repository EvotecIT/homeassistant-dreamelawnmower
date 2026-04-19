"""Unit tests for reusable map source probes."""

from __future__ import annotations

from dreame_lawn_mower_client import (
    MAP_PROBE_PROPERTY_KEYS,
    DreameLawnMowerMapView,
    build_cloud_key_definition_summary,
    build_cloud_property_history_summary,
    build_cloud_property_summary,
    build_map_probe_payload,
)
from dreame_lawn_mower_client.models import DreameLawnMowerDescriptor


def test_map_probe_payload_trims_cloud_records() -> None:
    descriptor = DreameLawnMowerDescriptor(
        did="device-1",
        name="Garage Mower",
        model="dreame.mower.g2408",
        display_model="A2",
        account_type="dreame",
        country="eu",
    )
    map_view = DreameLawnMowerMapView(
        source="app_action_map",
        error="No map data returned.",
    )
    legacy_map_view = DreameLawnMowerMapView(source="legacy_current_map")
    vector_map_view = DreameLawnMowerMapView(source="batch_vector_map")

    payload = build_map_probe_payload(
        descriptor=descriptor,
        map_view=map_view,
        legacy_map_view=legacy_map_view,
        vector_map_view=vector_map_view,
        cloud_properties={
            "requested_key_count": 3,
            "returned_entry_count": 3,
            "displayed_entry_count": 3,
            "entries": [
                {
                    "key": "1.1",
                    "value": [206, 0, 0],
                    "property_hint": "raw_status_blob",
                    "value_bytes_len": 3,
                    "value_bytes_hex": "ce0000",
                },
                    {
                        "key": "2.1",
                        "value": 13,
                        "property_hint": "mower_state",
                        "decoded_label": "Charging Completed",
                        "decoded_label_source": "cloud_key_definition",
                        "state_key": "charging_completed",
                    },
                {
                    "key": "6.50",
                    "value": {"zones": [{"name": "front", "polygon": [[1, 2]]}]},
                },
                {"key": "6.1", "value": None},
            ],
        },
        cloud_device_info={
            "did": "device-1",
            "model": "dreame.mower.g2408",
            "customName": "Garage Mower",
            "latestStatus": 13,
            "battery": 100,
            "online": True,
            "deviceInfo": {
                "displayName": "A2",
                "status": "Live",
                "productId": "10425",
                "feature": "video_tx",
                "permit": "pincode,video,aiobs",
                "extensionId": "1423",
            },
            "keyDefine": {"ver": 10, "url": "https://example.invalid/key.json"},
        },
        cloud_device_list_page={
            "page": {
                "records": [
                    {
                        "did": "device-1",
                        "model": "dreame.mower.g2408",
                        "customName": "Garage Mower",
                        "deviceInfo": {"displayName": "A2"},
                    }
                ]
            }
        },
        cloud_user_features={
            "did": "device-1",
            "permit": "pincode,video,aiobs",
            "features": ["map", "video"],
        },
        cloud_key_definition={
            "url_present": True,
            "ver": 10,
            "fetched": True,
            "payload": {
                "keyDefine": {
                    "2.1": {"en": {"13": "Charging Completed"}},
                    "2.2": {"en": {"31": "Left wheel speed"}},
                },
                "ver": 10,
            },
        },
    )

    assert payload["descriptor"] == {
        "name": "Garage Mower",
        "model": "dreame.mower.g2408",
        "display_model": "A2",
        "account_type": "dreame",
        "country": "eu",
    }
    assert payload["selected_map_view"]["source"] == "app_action_map"
    assert payload["legacy_current_map"]["source"] == "legacy_current_map"
    assert payload["batch_vector_map"]["source"] == "batch_vector_map"
    assert payload["legacy_current_map"]["diagnostics"] is None
    assert payload["cloud_properties"]["entries"][1]["value"] == 13
    assert payload["cloud_property_summary"]["non_empty_keys"] == [
        "1.1",
        "2.1",
        "6.50",
    ]
    assert payload["cloud_property_summary"]["unknown_non_empty_keys"] == ["6.50"]
    assert payload["cloud_property_summary"]["hinted_keys"] == {
        "1.1": "raw_status_blob",
        "2.1": "mower_state",
    }
    assert payload["cloud_property_summary"]["decoded_labels"] == {
        "2.1": "Charging Completed",
    }
    assert payload["cloud_property_summary"]["decoded_label_sources"] == {
        "2.1": "cloud_key_definition",
    }
    assert payload["cloud_property_summary"]["state_keys"] == {
        "2.1": "charging_completed",
    }
    assert payload["cloud_property_summary"]["blob_keys"] == {"1.1": 3}
    assert payload["cloud_property_summary"]["value_type_counts"] == {
        "array": 1,
        "empty": 1,
        "int": 1,
        "object": 1,
    }
    assert payload["cloud_property_summary"]["candidate_map_entry_count"] == 1
    assert payload["cloud_property_summary"]["candidate_map_properties"][0] == {
        "key": "6.50",
        "reason": "contains_zone",
        "value_type": "object",
        "value_preview": {"zones": [{"name": "front", "polygon": [[1, 2]]}]},
    }
    assert payload["cloud_property_history_summary"] == {
        "requested_keys": [],
        "populated_keys": [],
        "populated_key_count": 0,
        "keys": {},
    }
    assert payload["cloud_device_info"]["key_define_url_present"] is True
    assert payload["cloud_device_info"]["display_name"] == "A2"
    assert payload["cloud_device_list_record"]["display_name"] == "A2"
    assert payload["cloud_user_features"]["permit"] == "pincode,video,aiobs"
    assert payload["cloud_user_features"]["did"] == "**REDACTED**"
    assert payload["cloud_device_otc_info"] == {"present": False}
    assert payload["cloud_key_definition"]["fetched"] is True
    assert payload["cloud_key_definition"]["key_define_count"] == 2
    assert payload["cloud_key_definition"]["key_define_keys"] == ["2.1", "2.2"]


def test_map_probe_payload_summarizes_cloud_otc_info() -> None:
    descriptor = DreameLawnMowerDescriptor(
        did="device-1",
        name="Garage Mower",
        model="dreame.mower.g2408",
        display_model="A2",
        account_type="dreame",
        country="eu",
    )

    payload = build_map_probe_payload(
        descriptor=descriptor,
        map_view=DreameLawnMowerMapView(source="legacy_current_map"),
        cloud_properties={},
        cloud_device_info={},
        cloud_device_list_page={},
        cloud_device_otc_info={
            "did": "device-1",
            "map": {"object_name": "MAP.123"},
            "status": "ok",
        },
    )

    assert payload["cloud_device_otc_info"] == {
        "present": True,
        "type": "object",
        "keys": ["did", "map", "status"],
        "preview": {
            "did": "**REDACTED**",
            "map": {"object_name": "MAP.123"},
            "status": "ok",
        },
    }
    assert "did" not in payload["cloud_device_info"]
    assert payload["selected_map_view"]["source"] == "legacy_current_map"
    assert payload["legacy_current_map"]["source"] == "legacy_current_map"
    assert payload["batch_vector_map"] is None


def test_map_probe_keys_include_known_a2_docked_hits() -> None:
    assert "1.1" in MAP_PROBE_PROPERTY_KEYS
    assert "2.1" in MAP_PROBE_PROPERTY_KEYS
    assert "2.2" in MAP_PROBE_PROPERTY_KEYS
    assert "6.13" in MAP_PROBE_PROPERTY_KEYS


def test_cloud_property_history_summary_marks_populated_map_history() -> None:
    summary = build_cloud_property_history_summary(
        {
            "6.1": [
                {
                    "time": 1776341619,
                    "value": '["MAP.123",1,2,3]',
                }
            ],
            "6.3": [],
            "6.13": {"error": "timeout"},
        }
    )

    assert summary["requested_keys"] == ["6.1", "6.3", "6.13"]
    assert summary["populated_keys"] == ["6.1"]
    assert summary["keys"]["6.1"] == {
        "entry_count": 1,
        "has_value": True,
        "latest_time": 1776341619,
        "latest_value_type": "str",
        "latest_value_preview": '["MAP.123",1,2,3]',
    }
    assert summary["keys"]["6.3"]["has_value"] is False
    assert summary["keys"]["6.13"]["error"] == "timeout"


def test_cloud_property_summary_handles_empty_or_unexpected_payloads() -> None:
    assert build_cloud_property_summary(None) == {
        "requested_key_count": 0,
        "returned_entry_count": 0,
        "displayed_entry_count": 0,
        "non_empty_entry_count": 0,
        "hinted_entry_count": 0,
        "decoded_entry_count": 0,
        "blob_entry_count": 0,
        "unknown_non_empty_entry_count": 0,
        "candidate_map_entry_count": 0,
        "non_empty_keys": [],
        "unknown_non_empty_keys": [],
        "hinted_keys": {},
        "decoded_labels": {},
        "decoded_label_sources": {},
        "state_keys": {},
        "blob_keys": {},
        "value_type_counts": {},
        "candidate_map_properties": [],
    }
    assert build_cloud_property_summary({"entries": "not-a-list"})[
        "displayed_entry_count"
    ] == 0


def test_cloud_key_definition_summary_handles_missing_payloads() -> None:
    assert build_cloud_key_definition_summary(None) == {
        "url_present": False,
        "ver": None,
        "source": None,
        "fetched": False,
        "error": None,
        "root_keys": [],
        "key_define_count": 0,
        "key_define_keys": [],
    }
    assert build_cloud_key_definition_summary(
        {"url": "https://example.invalid/key.json", "error": "timeout"}
    )["error"] == "timeout"
