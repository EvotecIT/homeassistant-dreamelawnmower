"""Tests for Home Assistant log payload extraction helpers."""

from __future__ import annotations

import json

import pytest

from examples.extract_ha_payload import (
    extract_first_payload,
    extract_payloads,
    summarize_payload,
)


def test_extract_first_payload_from_debug_log_line() -> None:
    payload = {
        "captured_at": "2026-04-16T21:28:07.783516+00:00",
        "snapshot": {"state": "charging_completed"},
    }
    log_line = (
        "Captured Dreame lawn mower debug snapshot for Dreame A2 (A2): "
        f"{json.dumps(payload)}"
    )

    assert extract_first_payload(log_line) == payload


def test_extract_payloads_keeps_log_kind_for_multiple_entries() -> None:
    debug_payload = {"snapshot": {"state": "docked"}}
    map_payload = {"map": {"source": "placeholder"}}
    log_text = "\n".join(
        [
            "noise before",
            "Captured Dreame lawn mower debug snapshot for Dreame A2 (A2): "
            f"{json.dumps(debug_payload)}",
            "another logger line",
            "Captured Dreame lawn mower map probe for Dreame A2 (A2): "
            f"{json.dumps(map_payload)}",
            "noise after",
        ]
    )

    payloads = extract_payloads(log_text)

    assert [payload.kind for payload in payloads] == ["debug_snapshot", "map_probe"]
    assert [payload.payload for payload in payloads] == [debug_payload, map_payload]


def test_extract_payloads_accepts_plain_json_diagnostics() -> None:
    payload = {"data": {"snapshot": {"battery_level": 100}}}

    assert extract_payloads(json.dumps(payload))[0].payload == payload


def test_extract_payloads_can_filter_by_kind() -> None:
    debug_payload = {"snapshot": {"state": "docked"}}
    map_payload = {"map": {"source": "placeholder"}}
    log_text = "\n".join(
        [
            "Captured Dreame lawn mower debug snapshot for Dreame A2 (A2): "
            f"{json.dumps(debug_payload)}",
            "Captured Dreame lawn mower map probe for Dreame A2 (A2): "
            f"{json.dumps(map_payload)}",
        ]
    )

    payloads = extract_payloads(log_text, kind="map_probe")

    assert len(payloads) == 1
    assert payloads[0].kind == "map_probe"
    assert payloads[0].payload == map_payload


def test_extract_first_payload_raises_when_no_payload_is_found() -> None:
    with pytest.raises(ValueError, match="No Dreame lawn mower JSON payload found"):
        extract_first_payload("plain traceback without mower payload")


def test_summarize_payload_prefers_state_reconciliation() -> None:
    payload = {
        "captured_at": "2026-04-17T13:15:46+00:00",
        "descriptor": {
            "name": "Dreame A2 Bodzio",
            "display_model": "A2",
            "model": "dreame.mower.g2408",
        },
        "snapshot": {
            "activity": "error",
            "state": "charging",
            "state_name": "charging",
            "battery_level": 56,
            "error_code": -1,
            "error_display": "No error",
            "charging": True,
            "docked": False,
            "realtime_property_count": 14,
        },
        "state_reconciliation": {
            "activity": "error",
            "state": "charging",
            "state_name": "charging",
            "raw_mower_state": "charging_completed",
            "error": {
                "active": True,
                "code": 73,
                "name": "no_error",
                "display": "No error",
                "raw_attribute": "No error",
            },
            "flags": {
                "charging": True,
                "docked": False,
                "started": True,
            },
            "warnings": ["active_error_code_but_display_says_no_error"],
        },
        "device": {
            "unknown_property_count": 0,
            "realtime_property_count": 14,
        },
    }

    assert summarize_payload(payload) == {
        "captured_at": "2026-04-17T13:15:46+00:00",
        "name": "Dreame A2 Bodzio",
        "model": "A2",
        "activity": "error",
        "state": "charging",
        "state_name": "charging",
        "raw_mower_state": "charging_completed",
        "battery_level": 56,
        "error": {
            "active": True,
            "code": 73,
            "name": "no_error",
            "display": "No error",
            "raw_attribute": "No error",
        },
        "flags": {
            "charging": True,
            "docked": False,
            "started": True,
        },
        "warnings": ["active_error_code_but_display_says_no_error"],
        "unknown_property_count": 0,
        "realtime_property_count": 14,
    }


def test_summarize_payload_accepts_home_assistant_diagnostics_wrapper() -> None:
    payload = {
        "data": {
            "captured_at": "2026-04-16T14:48:30+02:00",
            "descriptor": {"name": "Dreame A2 Bodzio", "display_model": "A2"},
            "snapshot": {
                "activity": "error",
                "state": "paused",
                "state_name": "paused",
                "battery_level": 74,
                "error_code": 31,
                "error_display": "Left wheel speed",
                "raw_attributes": {"mower_state": "paused"},
            },
        }
    }

    assert summarize_payload(payload) == {
        "captured_at": "2026-04-16T14:48:30+02:00",
        "name": "Dreame A2 Bodzio",
        "model": "A2",
        "activity": "error",
        "state": "paused",
        "state_name": "paused",
        "raw_mower_state": "paused",
        "battery_level": 74,
        "error": {
            "code": 31,
            "display": "Left wheel speed",
        },
    }


def test_summarize_payload_includes_map_probe_summary() -> None:
    payload = {
        "cloud_property_summary": {
            "requested_key_count": 3,
            "non_empty_keys": ["2.1"],
            "decoded_labels": {"2.1": "Charging"},
            "blob_keys": {"1.1": 20},
        },
        "map": {
            "source": "legacy_current_map",
            "available": False,
            "has_image": False,
            "error": "No mapdata returned",
        },
    }

    assert summarize_payload(payload) == {
        "cloud_property_summary": {
            "requested_key_count": 3,
            "non_empty_keys": ["2.1"],
            "decoded_labels": {"2.1": "Charging"},
            "blob_keys": {"1.1": 20},
        },
        "map": {
            "source": "legacy_current_map",
            "available": False,
            "has_image": False,
            "error": "No mapdata returned",
        },
    }
