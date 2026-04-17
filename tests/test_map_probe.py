"""Unit tests for reusable map source probes."""

from __future__ import annotations

from dreame_lawn_mower_client import (
    MAP_PROBE_PROPERTY_KEYS,
    DreameLawnMowerMapView,
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
            "requested_key_count": 1,
            "entries": [{"key": "2.1", "value": 13}],
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
    )

    assert payload["descriptor"] == {
        "name": "Garage Mower",
        "model": "dreame.mower.g2408",
        "display_model": "A2",
        "account_type": "dreame",
        "country": "eu",
    }
    assert payload["legacy_current_map"]["source"] == "legacy_current_map"
    assert payload["cloud_properties"]["entries"][0]["value"] == 13
    assert payload["cloud_device_info"]["key_define_url_present"] is True
    assert payload["cloud_device_info"]["display_name"] == "A2"
    assert payload["cloud_device_list_record"]["display_name"] == "A2"
    assert "did" not in payload["cloud_device_info"]


def test_map_probe_keys_include_known_a2_docked_hits() -> None:
    assert "1.1" in MAP_PROBE_PROPERTY_KEYS
    assert "2.1" in MAP_PROBE_PROPERTY_KEYS
    assert "2.2" in MAP_PROBE_PROPERTY_KEYS
    assert "6.13" in MAP_PROBE_PROPERTY_KEYS
