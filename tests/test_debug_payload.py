"""Unit tests for mower debug payload capture."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.dreame_lawn_mower.debug import (
    build_debug_payload,
    sanitize_debug_data,
)
from dreame_lawn_mower_client.models import (
    DreameLawnMowerDescriptor,
    DreameLawnMowerSnapshot,
)


def test_build_debug_payload_redacts_sensitive_fields() -> None:
    descriptor = DreameLawnMowerDescriptor(
        did="device-1",
        name="Garage Mower",
        model="dreame.mower.g2408",
        display_model="A2",
        account_type="dreame",
        country="eu",
        host="example.invalid",
        mac="AA:BB:CC:DD:EE:FF",
        token="secret-token",
        raw={
            "did": "device-1",
            "mac": "AA:BB:CC:DD:EE:FF",
            "bindDomain": "example.invalid",
            "sn": "SERIAL123",
        },
    )
    snapshot = DreameLawnMowerSnapshot(
        descriptor=descriptor,
        available=True,
        state="paused",
        state_name="paused",
        activity="paused",
        battery_level=79,
        firmware_version="4.3.6_0320",
        hardware_version="Linux",
        serial_number="SERIAL123",
        cloud_update_time="2025-04-22 10:03:44",
        online=True,
        charging=False,
        started=True,
        mapping_available=False,
        capabilities=("lidar_navigation", "map"),
        raw_attributes={"started": True, "capabilities": ["lidar_navigation", "map"]},
        raw_info={"sn": "SERIAL123", "mac": "AA:BB:CC:DD:EE:FF"},
    )
    device = SimpleNamespace(
        name="Garage Mower",
        available=True,
        host="example.invalid",
        token="secret-token",
        unknown_properties={
            -113852866: {
                "did": -113852866,
                "code": 0,
                "siid": 9,
                "piid": 4,
                "value": 123,
                "last_seen": 123456.0,
            }
        },
        realtime_properties={
            "1.1": {
                "siid": 1,
                "piid": 1,
                "did": "100",
                "code": 0,
                "value": [206, 0, 206],
                "property_name": "RAW_STATUS",
                "last_seen": 123456.5,
            },
            "1.2": {
                "siid": 1,
                "piid": 2,
                "did": "101",
                "code": 0,
                "value": 79,
                "property_name": "BATTERY_LEVEL",
                "last_seen": 123457.0,
            },
            "2.51": {
                "siid": 2,
                "piid": 51,
                "did": None,
                "code": None,
                "value": {"time": "1776587727", "tz": "Europe/Warsaw"},
                "property_name": "UNKNOWN_REALTIME_2.51",
                "last_seen": 123457.5,
            },
            "9.4": {
                "siid": 9,
                "piid": 4,
                "did": None,
                "code": None,
                "value": {"blob": 123},
                "property_name": "UNKNOWN_REALTIME_9.4",
                "last_seen": 123458.0,
            },
        },
        last_realtime_message={
            "received_at": 123459.0,
            "message": {
                "method": "properties_changed",
                "params": [
                    {"siid": 1, "piid": 2, "value": 79},
                    {"siid": 9, "piid": 4, "value": {"blob": 123}},
                ],
            },
        },
        status=SimpleNamespace(
            state_name="paused",
            task_status_name="unknown",
            battery_level=79,
            charging=False,
            started=True,
            paused=False,
            running=False,
            returning=False,
            docked=False,
            scheduled_clean=False,
            shortcut_task=False,
            cleaning_mode_name="unknown",
            child_lock=None,
            attributes={"started": True, "capabilities": ["lidar_navigation", "map"]},
        ),
        capability=SimpleNamespace(
            list=["lidar_navigation", "map"],
            lidar_navigation=True,
            map=True,
            custom_cleaning_mode=False,
            shortcuts=False,
            camera_streaming=False,
            camera_light=None,
            obstacles=False,
            ai_detection=False,
            disable_sensor_cleaning=True,
        ),
        info=SimpleNamespace(
            raw={
                "did": "device-1",
                "mac": "AA:BB:CC:DD:EE:FF",
                "sn": "SERIAL123",
                "bindDomain": "example.invalid",
            }
        ),
    )

    payload = build_debug_payload(
        entry_data={
            "username": "user@example.com",
            "password": "secret-password",
            "token": "secret-token",
            "did": "device-1",
        },
        snapshot=snapshot,
        device=device,
    )

    assert payload["diagnostic_schema_version"] == 5
    assert payload["entry"]["username"] == "**REDACTED**"
    assert payload["entry"]["password"] == "**REDACTED**"
    assert payload["entry"]["token"] == "**REDACTED**"
    assert payload["entry"]["did"] == "**REDACTED**"
    assert payload["cloud_record"]["mac"] == "**REDACTED**"
    assert payload["snapshot"]["serial_number"] == "**REDACTED**"
    assert payload["device"]["host"] == "**REDACTED**"
    assert payload["device"]["info_raw"]["sn"] == "**REDACTED**"
    assert payload["device"]["unknown_property_count"] == 1
    assert payload["device"]["unknown_properties"]["-113852866"]["value"] == 123
    assert payload["device"]["unknown_property_summary"] == {
        "count": 1,
        "keys": ["-113852866"],
        "value_type_counts": {"number": 1},
        "candidate_map_property_count": 0,
        "candidate_map_properties": [],
        "entries": [
            {
                "key": "-113852866",
                "siid": 9,
                "piid": 4,
                "code": 0,
                "value_type": "number",
                "value_preview": 123,
                "map_candidate_reason": None,
            }
        ],
    }
    assert payload["device"]["realtime_property_count"] == 4
    assert (
        payload["device"]["realtime_properties"]["1.2"]["property_name"]
        == "BATTERY_LEVEL"
    )
    assert payload["device"]["realtime_properties"]["9.4"]["value"] == {"blob": 123}
    assert payload["device"]["realtime_summary"]["known_keys"] == [
        "1.1",
        "1.2",
        "2.51",
    ]
    assert payload["device"]["realtime_summary"]["unknown_keys"] == ["9.4"]
    assert payload["device"]["realtime_summary"]["value_type_counts"] == {
        "array": 1,
        "number": 1,
        "object": 2,
    }
    assert payload["device"]["realtime_summary"]["status_blob_keys"] == ["1.1"]
    assert payload["device"]["realtime_summary"]["candidate_map_property_count"] == 2
    assert payload["device"]["realtime_summary"]["candidate_map_properties"] == [
        {
            "key": "2.51",
            "reason": "object_payload",
            "value_type": "object",
            "value_preview": {"time": "1776587727", "tz": "Europe/Warsaw"},
        },
        {
            "key": "9.4",
            "reason": "object_payload",
            "value_type": "object",
            "value_preview": {"blob": 123},
        }
    ]
    assert payload["device"]["realtime_summary"]["entries"][0]["status_blob"] == {
        "length": 3,
        "frame_valid": True,
        "hex": "ce00ce",
        "notes": ["unexpected_length"],
        "bytes_by_index": {"0": 206, "1": 0, "2": 206},
    }
    assert payload["device"]["realtime_summary"]["entries"][2] == {
        "key": "2.51",
        "property_name": "device_time",
        "siid": 2,
        "piid": 51,
        "code": None,
        "value_type": "object",
        "value_preview": {"time": "1776587727", "tz": "Europe/Warsaw"},
        "map_candidate_reason": "object_payload",
        "status_blob": None,
        "property_hint": "device_time",
    }
    assert payload["device"]["realtime_summary"]["entries"][3] == {
        "key": "9.4",
        "property_name": "UNKNOWN_REALTIME_9.4",
        "siid": 9,
        "piid": 4,
        "code": None,
        "value_type": "object",
        "value_preview": {"blob": 123},
        "map_candidate_reason": "object_payload",
        "status_blob": None,
    }
    assert (
        payload["device"]["last_realtime_message"]["message"]["method"]
        == "properties_changed"
    )
    assert payload["device"]["status_values"]["battery_level"] == 79
    assert payload["device"]["capabilities"]["list"] == ["lidar_navigation", "map"]
    assert payload["state_reconciliation"]["activity"] == "paused"
    assert payload["state_reconciliation"]["error"]["active"] is False
    assert payload["state_reconciliation"]["flags"]["raw_charging"] is None
    assert payload["state_reconciliation"]["flags"]["started"] is True
    assert payload["state_reconciliation"]["manual_drive"] == {
        "safe": True,
        "block_reason": None,
    }
    assert payload["state_reconciliation"]["warnings"] == []
    assert payload["triage"] == {
        "schema_version": 5,
        "known_model": True,
        "model": "dreame.mower.g2408",
        "display_model": "A2",
        "activity": "paused",
        "state": "paused",
        "error": {"active": False, "code": None, "display": None, "source": None},
        "manual_drive": {"safe": True, "block_reason": None},
        "available": True,
        "capabilities": ["lidar_navigation", "map"],
        "unknown_property_count": 1,
        "unknown_realtime_count": 1,
        "state_warning_count": 0,
        "issues": [
            "unknown_device_properties_present",
            "unknown_realtime_properties_present",
        ],
        "suggested_next_capture": [
            "capture_map_probe",
            "download_diagnostics_after_state_change",
        ],
    }


def test_sanitize_debug_data_redacts_operation_snapshot_identifiers() -> None:
    payload = sanitize_debug_data(
        {
            "snapshot": {
                "descriptor": {
                    "did": "device-1",
                    "host": "192.0.2.10",
                    "token_present": True,
                },
                "serial_number": "SERIAL123",
            },
            "status_blob": {"hex": "ce00ce"},
        }
    )

    assert payload["snapshot"]["descriptor"]["did"] == "**REDACTED**"
    assert payload["snapshot"]["descriptor"]["host"] == "**REDACTED**"
    assert payload["snapshot"]["serial_number"] == "**REDACTED**"
    assert payload["status_blob"]["hex"] == "ce00ce"


def test_build_debug_payload_highlights_state_disagreements() -> None:
    descriptor = DreameLawnMowerDescriptor(
        did="device-1",
        name="Garage Mower",
        model="dreame.mower.g2408",
        display_model="A2",
        account_type="dreame",
        country="eu",
    )
    snapshot = DreameLawnMowerSnapshot(
        descriptor=descriptor,
        available=True,
        state="charging",
        state_name="charging",
        activity="error",
        battery_level=56,
        error_code=73,
        error_name="no_error",
        error_text="No error",
        error_display="Error 73",
        charging=True,
        docked=True,
        raw_docked=False,
        started=False,
        raw_started=True,
        returning=False,
        raw_returning=True,
        raw_attributes={
            "mower_state": "charging_completed",
            "status": "Returning",
            "error": "No error",
        },
    )
    device = SimpleNamespace(
        name="Garage Mower",
        available=True,
        host=None,
        token=None,
        unknown_properties={},
        realtime_properties={},
        last_realtime_message=None,
        status=SimpleNamespace(
            state_name="charging",
            task_status_name="unknown",
            battery_level=56,
            charging=True,
            started=True,
            paused=False,
            running=False,
            returning=False,
            docked=False,
            scheduled_clean=False,
            shortcut_task=False,
            cleaning_mode_name="unknown",
            child_lock=None,
            attributes={
                "mower_state": "charging_completed",
                "status": "Returning",
                "error": "No error",
            },
        ),
        capability=None,
        info=None,
    )

    payload = build_debug_payload(entry_data={}, snapshot=snapshot, device=device)
    reconciliation = payload["state_reconciliation"]

    assert reconciliation["activity"] == "error"
    assert reconciliation["error"]["active"] is True
    assert reconciliation["error"]["code"] == 73
    assert reconciliation["error"]["display"] == "Error 73"
    assert reconciliation["raw_mower_state"] == "charging_completed"
    assert reconciliation["flags"]["docked"] is True
    assert reconciliation["flags"]["raw_docked"] is False
    assert reconciliation["flags"]["raw_charging"] is None
    assert reconciliation["flags"]["started"] is False
    assert reconciliation["flags"]["raw_started"] is True
    assert reconciliation["flags"]["returning"] is False
    assert reconciliation["flags"]["raw_returning"] is True
    assert reconciliation["manual_drive"] == {
        "safe": False,
        "block_reason": "Remote control is blocked while error is active.",
    }
    assert reconciliation["warnings"] == [
        "state_looks_docked_but_raw_docked_false",
        "raw_mower_state_looks_docked_but_raw_docked_false",
        "raw_mower_state_differs_from_state_name",
    ]
    assert payload["triage"]["schema_version"] == 5
    assert payload["triage"]["error"] == {
        "active": True,
        "code": 73,
        "display": "Error 73",
        "source": None,
    }
    assert payload["triage"]["manual_drive"] == {
        "safe": False,
        "block_reason": "Remote control is blocked while error is active.",
    }
    assert payload["triage"]["state_warning_count"] == 3
    assert payload["triage"]["issues"] == [
        "state:state_looks_docked_but_raw_docked_false",
        "state:raw_mower_state_looks_docked_but_raw_docked_false",
        "state:raw_mower_state_differs_from_state_name",
    ]
