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
    operation_payload = {"snapshot": {"label": "field_test"}}
    log_text = "\n".join(
        [
            "noise before",
            "Captured Dreame lawn mower debug snapshot for Dreame A2 (A2): "
            f"{json.dumps(debug_payload)}",
            "another logger line",
            "Captured Dreame lawn mower operation snapshot for Dreame A2 (A2): "
            f"{json.dumps(operation_payload)}",
            "Captured Dreame lawn mower map probe for Dreame A2 (A2): "
            f"{json.dumps(map_payload)}",
            "noise after",
        ]
    )

    payloads = extract_payloads(log_text)

    assert [payload.kind for payload in payloads] == [
        "debug_snapshot",
        "operation_snapshot",
        "map_probe",
    ]
    assert [payload.payload for payload in payloads] == [
        debug_payload,
        operation_payload,
        map_payload,
    ]


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
        "diagnostic_schema_version": 4,
        "captured_at": "2026-04-17T13:15:46+00:00",
        "descriptor": {
            "name": "Dreame A2 Bodzio",
            "display_model": "A2",
            "model": "dreame.mower.g2408",
        },
        "triage": {
            "schema_version": 4,
            "issues": ["state:charging_true_but_docked_false"],
            "suggested_next_capture": ["download_diagnostics_after_state_change"],
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
                "raw_charging": False,
                "docked": False,
                "raw_docked": False,
                "started": True,
                "raw_started": True,
            },
            "manual_drive": {
                "safe": False,
                "block_reason": "Remote control is blocked while error is active.",
            },
            "warnings": ["active_error_code_but_display_says_no_error"],
        },
        "device": {
            "unknown_property_count": 0,
            "realtime_property_count": 14,
        },
    }

    assert summarize_payload(payload) == {
        "diagnostic_schema_version": 4,
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
            "raw_charging": False,
            "docked": False,
            "raw_docked": False,
            "started": True,
            "raw_started": True,
        },
        "manual_drive": {
            "safe": False,
            "block_reason": "Remote control is blocked while error is active.",
        },
        "warnings": ["active_error_code_but_display_says_no_error"],
        "triage_issues": ["state:charging_true_but_docked_false"],
        "suggested_next_capture": ["download_diagnostics_after_state_change"],
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


def test_summarize_payload_accepts_operation_snapshot() -> None:
    payload = {
        "label": "after_dock",
        "errors": [{"section": "map_view", "error": "No mapdata returned"}],
        "snapshot": {
            "device": "Dreame A2 Bodzio (A2)",
            "descriptor": {
                "name": "Dreame A2 Bodzio",
                "model": "dreame.mower.g2408",
                "display_model": "A2",
            },
            "activity": "returning",
            "state": "returning",
            "state_name": "returning",
            "battery_level": 100,
            "error_code": -1,
            "error_display": "No error",
            "charging": False,
            "raw_charging": False,
            "docked": False,
            "raw_docked": False,
            "returning": True,
            "raw_returning": True,
            "started": True,
            "raw_started": True,
            "realtime_property_count": 3,
            "manual_drive_safe": False,
            "manual_drive_block_reason": (
                "Remote control is blocked while returning to dock."
            ),
            "raw_state_signals": {"mower_state": "returning"},
        },
        "unknown_property_summary": {"count": 0},
        "realtime_summary": {"count": 3},
        "status_blob": {
            "source": "realtime",
            "length": 20,
            "frame_valid": True,
            "notes": [],
            "hex": "ce000000000000000000000000000000000000ce",
        },
        "remote_control_support": {
            "supported": True,
            "active": False,
            "state_safe": True,
            "state_block_reason": None,
            "siid": 4,
            "piid": 15,
        },
        "map_view": {
            "source": "legacy_current_map",
            "available": False,
            "has_image": False,
            "error": "No mapdata returned",
            "diagnostics": {
                "reason": "No mapdata returned",
                "cloud_property_summary": {
                    "requested_key_count": 3,
                    "non_empty_keys": ["2.1"],
                    "decoded_labels": {"2.1": "Returning Charge"},
                    "state_keys": {"2.1": "returning"},
                    "blob_keys": {"1.1": 20},
                },
            },
        },
        "firmware_update": {
            "current_version": "4.3.6_0320",
            "update_available": None,
            "warnings": ["plugin_force_update_conflict"],
            "reason": (
                "No verified mower firmware update availability signal was found."
            ),
        },
    }

    assert summarize_payload(payload) == {
        "label": "after_dock",
        "name": "Dreame A2 Bodzio",
        "model": "A2",
        "activity": "returning",
        "state": "returning",
        "state_name": "returning",
        "raw_mower_state": "returning",
        "battery_level": 100,
        "error": {
            "code": -1,
            "display": "No error",
        },
        "flags": {
            "charging": False,
            "raw_charging": False,
            "docked": False,
            "raw_docked": False,
            "returning": True,
            "raw_returning": True,
            "started": True,
            "raw_started": True,
        },
        "manual_drive": {
            "safe": False,
            "block_reason": "Remote control is blocked while returning to dock.",
        },
        "errors": [{"section": "map_view", "error": "No mapdata returned"}],
        "unknown_property_count": 0,
        "realtime_property_count": 3,
        "status_blob": {
            "source": "realtime",
            "length": 20,
            "frame_valid": True,
        },
        "map_view": {
            "source": "legacy_current_map",
            "available": False,
            "has_image": False,
            "error": "No mapdata returned",
            "diagnostic_reason": "No mapdata returned",
            "cloud_property_summary": {
                "requested_key_count": 3,
                "non_empty_keys": ["2.1"],
                "decoded_labels": {"2.1": "Returning Charge"},
                "state_keys": {"2.1": "returning"},
                "blob_keys": {"1.1": 20},
            },
        },
        "firmware_update": {
            "current_version": "4.3.6_0320",
            "warnings": ["plugin_force_update_conflict"],
            "reason": (
                "No verified mower firmware update availability signal was found."
            ),
        },
        "remote_control_support": {
            "supported": True,
            "active": False,
            "state_safe": True,
            "siid": 4,
            "piid": 15,
        },
    }


def test_summarize_payload_accepts_field_trip_wrapper() -> None:
    payload = {
        "execute": False,
        "device_index": 0,
        "settings": {
            "dock": False,
            "include_map": True,
            "include_firmware": True,
            "velocity": 60,
            "rotation": 45,
            "duration": 0.5,
            "settle": 1.0,
        },
        "steps": [
            {
                "label": "read_only",
                "ok": True,
                "result": "No movement commands sent.",
            }
        ],
        "captures": [
            {
                "label": "before",
                "snapshot": {
                    "descriptor": {
                        "name": "Dreame A2 Bodzio",
                        "display_model": "A2",
                    },
                    "activity": "docked",
                    "state": "charging_completed",
                    "state_name": "charging_completed",
                    "battery_level": 100,
                    "charging": False,
                    "raw_charging": False,
                    "docked": True,
                    "raw_docked": False,
                    "manual_drive_safe": True,
                },
                "map_view": {
                    "available": False,
                    "has_image": False,
                    "error": "No map data returned.",
                },
                "firmware_update": {
                    "current_version": "4.3.6_0320",
                    "update_available": None,
                    "reason": "No verified mower firmware update availability signal.",
                },
                "remote_control_support": {
                    "supported": True,
                    "active": False,
                    "state_safe": True,
                    "siid": 4,
                    "piid": 15,
                },
            },
            {
                "label": "final",
                "snapshot": {
                    "descriptor": {
                        "name": "Dreame A2 Bodzio",
                        "display_model": "A2",
                    },
                    "activity": "docked",
                    "state": "charging_completed",
                    "state_name": "charging_completed",
                    "battery_level": 100,
                },
            },
        ],
    }

    assert summarize_payload(payload) == {
        "execute": False,
        "device_index": 0,
        "capture_count": 2,
        "step_count": 1,
        "settings": {
            "dock": False,
            "include_map": True,
            "include_firmware": True,
            "velocity": 60,
            "rotation": 45,
            "duration": 0.5,
            "settle": 1.0,
        },
        "steps": [{"label": "read_only", "ok": True}],
        "captures": [
            {
                "label": "before",
                "name": "Dreame A2 Bodzio",
                "model": "A2",
                "activity": "docked",
                "state": "charging_completed",
                "state_name": "charging_completed",
                "battery_level": 100,
                "flags": {
                    "charging": False,
                    "raw_charging": False,
                    "docked": True,
                    "raw_docked": False,
                },
                "manual_drive": {
                    "safe": True,
                },
                "map_view": {
                    "available": False,
                    "has_image": False,
                    "error": "No map data returned.",
                },
                "firmware_update": {
                    "current_version": "4.3.6_0320",
                    "reason": (
                        "No verified mower firmware update availability signal."
                    ),
                },
                "remote_control_support": {
                    "supported": True,
                    "active": False,
                    "state_safe": True,
                    "siid": 4,
                    "piid": 15,
                },
            },
            {
                "label": "final",
                "name": "Dreame A2 Bodzio",
                "model": "A2",
                "activity": "docked",
                "state": "charging_completed",
                "state_name": "charging_completed",
                "battery_level": 100,
            },
        ],
    }


def test_summarize_payload_includes_map_probe_summary() -> None:
    payload = {
        "cloud_property_summary": {
            "requested_key_count": 3,
            "non_empty_keys": ["2.1"],
            "decoded_labels": {"2.1": "Charging"},
            "state_keys": {"2.1": "charging"},
            "blob_keys": {"1.1": 20},
        },
        "map": {
            "source": "legacy_current_map",
            "available": False,
            "has_image": False,
            "error": "No mapdata returned",
        },
        "app_maps": {
            "source": "app_action_map",
            "available": True,
            "map_count": 2,
            "current_map_index": 0,
            "errors": [],
            "maps": [
                {
                    "idx": 0,
                    "current": True,
                    "created": True,
                    "available": True,
                    "hash_match": True,
                    "summary": {
                        "map_area_count": 2,
                        "boundary_point_count": 401,
                        "spot_count": 2,
                        "spot_boundary_point_count": 8,
                        "semantic_count": 0,
                        "semantic_boundary_point_count": 0,
                        "semantic_key_counts": {},
                        "trajectory_count": 1,
                        "trajectory_point_count": 63,
                        "cut_relation_count": 0,
                    },
                },
                {
                    "idx": 1,
                    "current": False,
                    "created": True,
                    "available": False,
                    "error": "hash mismatch",
                },
            ],
        },
    }

    assert summarize_payload(payload) == {
        "cloud_property_summary": {
            "requested_key_count": 3,
            "non_empty_keys": ["2.1"],
            "decoded_labels": {"2.1": "Charging"},
            "state_keys": {"2.1": "charging"},
            "blob_keys": {"1.1": 20},
        },
        "map": {
            "source": "legacy_current_map",
            "available": False,
            "has_image": False,
            "error": "No mapdata returned",
        },
        "app_maps": {
            "source": "app_action_map",
            "available": True,
            "map_count": 2,
            "current_map_index": 0,
            "maps": [
                {
                    "idx": 0,
                    "current": True,
                    "created": True,
                    "available": True,
                    "hash_match": True,
                    "summary": {
                        "map_area_count": 2,
                        "boundary_point_count": 401,
                        "spot_count": 2,
                        "spot_boundary_point_count": 8,
                        "semantic_count": 0,
                        "semantic_boundary_point_count": 0,
                        "trajectory_count": 1,
                        "trajectory_point_count": 63,
                        "cut_relation_count": 0,
                    },
                },
                {
                    "idx": 1,
                    "current": False,
                    "created": True,
                    "available": False,
                    "error": "hash mismatch",
                },
            ],
        },
    }
