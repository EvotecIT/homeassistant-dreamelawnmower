"""Unit tests for mower debug payload capture."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.dreame_lawn_mower.debug import build_debug_payload
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
            "1.2": {
                "siid": 1,
                "piid": 2,
                "did": "101",
                "code": 0,
                "value": 79,
                "property_name": "BATTERY_LEVEL",
                "last_seen": 123457.0,
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

    assert payload["entry"]["username"] == "**REDACTED**"
    assert payload["entry"]["password"] == "**REDACTED**"
    assert payload["entry"]["token"] == "**REDACTED**"
    assert payload["entry"]["did"] == "**REDACTED**"
    assert payload["cloud_record"]["mac"] == "**REDACTED**"
    assert payload["device"]["host"] == "**REDACTED**"
    assert payload["device"]["info_raw"]["sn"] == "**REDACTED**"
    assert payload["device"]["unknown_property_count"] == 1
    assert payload["device"]["unknown_properties"]["-113852866"]["value"] == 123
    assert payload["device"]["realtime_property_count"] == 2
    assert (
        payload["device"]["realtime_properties"]["1.2"]["property_name"]
        == "BATTERY_LEVEL"
    )
    assert payload["device"]["realtime_properties"]["9.4"]["value"] == {"blob": 123}
    assert (
        payload["device"]["last_realtime_message"]["message"]["method"]
        == "properties_changed"
    )
    assert payload["device"]["status_values"]["battery_level"] == 79
    assert payload["device"]["capabilities"]["list"] == ["lidar_navigation", "map"]
    assert payload["state_reconciliation"]["activity"] == "paused"
    assert payload["state_reconciliation"]["error"]["active"] is False
    assert payload["state_reconciliation"]["flags"]["started"] is True
    assert payload["state_reconciliation"]["warnings"] == []


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
        error_display="No error",
        charging=True,
        docked=True,
        raw_docked=False,
        started=True,
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
    assert reconciliation["error"]["display"] == "No error"
    assert reconciliation["raw_mower_state"] == "charging_completed"
    assert reconciliation["flags"]["docked"] is True
    assert reconciliation["flags"]["raw_docked"] is False
    assert reconciliation["warnings"] == [
        "active_error_code_but_display_says_no_error",
        "state_looks_docked_but_raw_docked_false",
        "raw_mower_state_looks_docked_but_raw_docked_false",
        "raw_mower_state_differs_from_state_name",
    ]
