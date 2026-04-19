"""Regression checks for reusable remote-control support helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.dreame_client.types import (
    DreameMowerProperty,
    DreameMowerState,
    DreameMowerStatus,
)
from dreame_lawn_mower_client import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    DreameLawnMowerDescriptor,
    DreameLawnMowerRemoteControlSupport,
)


class _FakeRemoteControlDevice:
    def __init__(self) -> None:
        self.available = True
        self.name = "Garage Mower"
        self.host = "192.0.2.10"
        self.mac = "00:11:22:33:44:55"
        self.token = "token"
        self.info = SimpleNamespace(
            firmware_version="4.3.6_0320",
            hardware_version="Linux",
            raw={
                "sn": "serial-1",
                "online": True,
                "updateTime": "2026-04-18 12:00:00",
            },
        )
        self.property_mapping = {
            DreameMowerProperty.REMOTE_CONTROL: {"siid": 4, "piid": 15}
        }
        self.status = SimpleNamespace(
            state=DreameMowerState.CHARGING,
            state_name="Charging",
            status=DreameMowerStatus.CHARGING,
            task_status=None,
            task_status_name=None,
            error=None,
            error_name="no_error",
            has_error=False,
            attributes={
                "mower_state": "charging",
                "charging": True,
                "started": True,
                "capabilities": ["map", "lidar_navigation"],
            },
            battery_level=84,
            charging=True,
            docked=True,
            started=True,
            paused=False,
            returning=False,
            running=False,
            scheduled_clean=False,
            shortcut_task=False,
            mapping_available=False,
            cleaning_mode=None,
            cleaning_mode_name=None,
            fast_mapping=False,
        )
        self.unknown_properties = {}
        self.realtime_properties = {
            "1.1": {
                "siid": 1,
                "piid": 1,
                "code": 0,
                "value": [206, 0, 206],
                "property_name": "UNKNOWN_REALTIME_1.1",
            }
        }
        self.last_realtime_message = {
            "message": {"method": "properties_changed", "params": []}
        }
        self._remote_control = False
        self.commands: list[dict[str, object]] = []
        self.update_count = 0

    def update(self) -> None:
        self.update_count += 1

    def get_property(self, prop: object) -> object:
        return None

    def remote_control_move_step(
        self,
        *,
        rotation: int = 0,
        velocity: int = 0,
        prompt: bool | None = None,
    ) -> dict[str, object]:
        self._remote_control = True
        command = {
            "rotation": rotation,
            "velocity": velocity,
            "prompt": prompt,
        }
        self.commands.append(command)
        return {"code": 0, "command": command}


def _client_with_device(device: object) -> DreameLawnMowerClient:
    client = DreameLawnMowerClient(
        username="user@example.com",
        password="secret",
        country="eu",
        account_type="dreame",
        descriptor=DreameLawnMowerDescriptor(
            did="device-1",
            name="Garage Mower",
            model="dreame.mower.g2408",
            display_model="A2",
            account_type="dreame",
            country="eu",
        ),
    )
    client._device = device
    return client


def test_remote_control_support_reports_protocol_mapping() -> None:
    client = _client_with_device(_FakeRemoteControlDevice())

    support = client._sync_get_remote_control_support()

    assert isinstance(support, DreameLawnMowerRemoteControlSupport)
    assert support.supported is True
    assert support.active is False
    assert support.state_safe is True
    assert support.state_block_reason is None
    assert support.siid == 4
    assert support.piid == 15
    assert support.state == "charging"
    assert support.status == "charging"
    assert support.as_dict()["supported"] is True


def test_remote_control_move_step_delegates_to_protocol_device() -> None:
    device = _FakeRemoteControlDevice()
    client = _client_with_device(device)

    result = client._sync_remote_control_move_step(
        rotation=120,
        velocity=240,
        prompt=False,
    )

    assert result == {
        "code": 0,
        "command": {"rotation": 120, "velocity": 240, "prompt": False},
    }
    assert device.commands == [
        {"rotation": 120, "velocity": 240, "prompt": False}
    ]
    assert client._sync_get_remote_control_support().active is True


def test_remote_control_support_blocks_fast_mapping() -> None:
    device = _FakeRemoteControlDevice()
    device.status.fast_mapping = True
    client = _client_with_device(device)

    support = client._sync_get_remote_control_support()

    assert support.supported is False
    assert support.state_safe is False
    assert support.state_block_reason == (
        "Remote control is blocked while fast mapping."
    )
    assert support.siid == 4
    assert support.piid == 15
    assert support.reason == "Remote control is blocked while fast mapping."


def test_remote_control_support_reports_unsafe_state_without_hiding_protocol() -> None:
    device = _FakeRemoteControlDevice()
    device.status.battery_level = 19
    client = _client_with_device(device)

    support = client._sync_get_remote_control_support()

    assert support.supported is True
    assert support.state_safe is False
    assert support.state_block_reason == (
        "Remote control is blocked while battery is low."
    )


def test_remote_control_move_step_blocks_unsafe_nonzero_commands() -> None:
    device = _FakeRemoteControlDevice()
    device.status.battery_level = 19
    client = _client_with_device(device)

    with pytest.raises(DreameLawnMowerConnectionError, match="battery is low"):
        client._sync_remote_control_move_step(
            rotation=0,
            velocity=120,
            prompt=False,
        )

    assert device.commands == []


def test_remote_control_move_step_allows_stop_when_state_is_unsafe() -> None:
    device = _FakeRemoteControlDevice()
    device.status.battery_level = 19
    client = _client_with_device(device)

    result = client._sync_remote_control_move_step(
        rotation=0,
        velocity=0,
        prompt=False,
    )

    assert result == {
        "code": 0,
        "command": {"rotation": 0, "velocity": 0, "prompt": False},
    }


def test_remote_control_move_step_rejects_missing_mapping() -> None:
    device = _FakeRemoteControlDevice()
    device.property_mapping = {}
    client = _client_with_device(device)

    with pytest.raises(
        DreameLawnMowerConnectionError,
        match="Remote-control property mapping is not available",
    ):
        client._sync_remote_control_move_step(rotation=0, velocity=0, prompt=False)


def test_remote_control_step_validation_rejects_unsafe_values() -> None:
    client = _client_with_device(_FakeRemoteControlDevice())

    with pytest.raises(ValueError, match="rotation must be an integer"):
        client._sync_remote_control_move_step(rotation=True, velocity=0, prompt=False)

    with pytest.raises(ValueError, match="velocity must be between"):
        client._sync_remote_control_move_step(rotation=0, velocity=1001, prompt=False)


def test_operation_snapshot_combines_safe_field_test_evidence() -> None:
    device = _FakeRemoteControlDevice()
    client = _client_with_device(device)

    payload = client._sync_capture_operation_snapshot(
        "before_drive",
        True,
        False,
        True,
        False,
        False,
        1.0,
        0.1,
        "en",
    )

    assert payload["label"] == "before_drive"
    assert payload["errors"] == []
    assert payload["snapshot"]["device"] == "Garage Mower (A2)"
    assert payload["snapshot"]["state"] == "charging"
    assert payload["snapshot"]["activity"] == "docked"
    assert payload["snapshot"]["battery_level"] == 84
    assert payload["snapshot"]["manual_drive_safe"] is True
    assert payload["snapshot"]["manual_drive_block_reason"] is None
    assert payload["snapshot"]["raw_state_signals"] == {
        "mower_state": "charging",
        "charging": True,
        "started": True,
    }
    assert payload["unknown_property_summary"] == {
        "count": 0,
        "value_type_counts": {},
        "entries": [],
    }
    assert payload["realtime_summary"]["count"] == 1
    assert payload["realtime_summary"]["known_keys"] == ["1.1"]
    assert payload["realtime_summary"]["unknown_keys"] == []
    assert payload["realtime_summary"]["entries"][0]["key"] == "1.1"
    assert (
        payload["realtime_summary"]["entries"][0]["property_name"]
        == "raw_status_blob"
    )
    assert (
        payload["realtime_summary"]["entries"][0]["property_hint"]
        == "raw_status_blob"
    )
    assert payload["realtime_summary"]["entries"][0]["value_type"] == "array"
    assert payload["realtime_summary"]["entries"][0]["value_preview"] == [206, 0, 206]
    assert (
        payload["realtime_summary"]["entries"][0]["status_blob"]["source"]
        == "operation"
    )
    assert payload["status_blob"]["source"] == "realtime"
    assert payload["status_blob"]["frame_valid"] is True
    assert payload["remote_control_support"]["supported"] is True
    assert payload["remote_control_support"]["state_safe"] is True
    assert payload["remote_control_support"]["state_block_reason"] is None
    assert payload["remote_control_support"]["siid"] == 4
    assert payload["remote_control_support"]["piid"] == 15
    assert "map_view" not in payload
    assert device.update_count == 1
