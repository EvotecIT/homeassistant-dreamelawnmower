"""Unit tests for reusable map source probes."""

from __future__ import annotations

from dreame_lawn_mower_client import (
    MAP_PROBE_PROPERTY_KEYS,
    DreameLawnMowerMapView,
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
        source="legacy_current_map",
        error="No map data returned.",
    )

    payload = build_map_probe_payload(
        descriptor=descriptor,
        map_view=map_view,
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
    )

    assert payload["descriptor"] == {
        "name": "Garage Mower",
        "model": "dreame.mower.g2408",
        "display_model": "A2",
        "account_type": "dreame",
        "country": "eu",
    }
    assert payload["legacy_current_map"]["source"] == "legacy_current_map"
    assert payload["legacy_current_map"]["diagnostics"] is None
    assert payload["cloud_properties"]["entries"][1]["value"] == 13
    assert payload["cloud_property_summary"]["non_empty_keys"] == ["1.1", "2.1"]
    assert payload["cloud_property_summary"]["hinted_keys"] == {
        "1.1": "raw_status_blob",
        "2.1": "mower_state",
    }
    assert payload["cloud_property_summary"]["decoded_labels"] == {
        "2.1": "Charging Completed",
    }
    assert payload["cloud_property_summary"]["blob_keys"] == {"1.1": 3}
    assert payload["cloud_device_info"]["key_define_url_present"] is True
    assert payload["cloud_device_info"]["display_name"] == "A2"
    assert payload["cloud_device_list_record"]["display_name"] == "A2"
    assert payload["cloud_user_features"]["permit"] == "pincode,video,aiobs"
    assert payload["cloud_user_features"]["did"] == "**REDACTED**"
    assert "did" not in payload["cloud_device_info"]


def test_map_probe_keys_include_known_a2_docked_hits() -> None:
    assert "1.1" in MAP_PROBE_PROPERTY_KEYS
    assert "2.1" in MAP_PROBE_PROPERTY_KEYS
    assert "2.2" in MAP_PROBE_PROPERTY_KEYS
    assert "6.13" in MAP_PROBE_PROPERTY_KEYS


def test_cloud_property_summary_handles_empty_or_unexpected_payloads() -> None:
    assert build_cloud_property_summary(None) == {
        "requested_key_count": 0,
        "returned_entry_count": 0,
        "displayed_entry_count": 0,
        "non_empty_entry_count": 0,
        "hinted_entry_count": 0,
        "decoded_entry_count": 0,
        "blob_entry_count": 0,
        "non_empty_keys": [],
        "hinted_keys": {},
        "decoded_labels": {},
        "blob_keys": {},
    }
    assert build_cloud_property_summary({"entries": "not-a-list"})[
        "displayed_entry_count"
    ] == 0
