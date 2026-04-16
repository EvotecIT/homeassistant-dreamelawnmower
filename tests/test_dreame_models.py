"""Unit tests for mower model normalization."""

from __future__ import annotations

from types import SimpleNamespace

from dreame_lawn_mower_client.models import (
    descriptor_from_cloud_record,
    snapshot_from_device,
)


class _FakeInfo:
    def __init__(self) -> None:
        self.firmware_version = "4.3.6_0320"
        self.hardware_version = "Linux"
        self.raw = {
            "online": True,
            "sn": "G2408051LEE0090632",
            "updateTime": "2025-04-22 10:03:44",
        }


class _FakeStatus:
    def __init__(self) -> None:
        self.state = SimpleNamespace(name="PAUSED")
        self.state_name = "paused"
        self.task_status = None
        self.task_status_name = "unknown"
        self.error = None
        self.error_name = "no_error"
        self.has_error = False
        self.battery_level = 79
        self.paused = False
        self.returning = False
        self.docked = False
        self.running = False
        self.scheduled_clean = False
        self.shortcut_task = False
        self.cleaning_mode = None
        self.cleaning_mode_name = "unknown"
        self.attributes = {
            "started": True,
            "charging": False,
            "mapping_available": True,
            "capabilities": ["lidar_navigation", "map"],
        }


class _FakeDevice:
    def __init__(self) -> None:
        self.available = True
        self.status = _FakeStatus()
        self.info = _FakeInfo()
        self.capability = SimpleNamespace(list=["lidar_navigation", "map"])

    def get_property(self, _prop):
        return None


def test_descriptor_maps_known_model_names() -> None:
    descriptor = descriptor_from_cloud_record(
        {
            "did": "device-1",
            "model": "dreame.mower.g2408",
            "customName": "Garage Mower",
            "bindDomain": "example.invalid",
            "mac": "AA:BB:CC:DD:EE:FF",
        },
        account_type="dreame",
        country="eu",
    )

    assert descriptor is not None
    assert descriptor.display_model == "A2"
    assert descriptor.title == "Garage Mower (A2)"


def test_snapshot_uses_state_name_before_boolean_helpers() -> None:
    descriptor = descriptor_from_cloud_record(
        {
            "did": "device-1",
            "model": "dreame.mower.g2408",
            "customName": "Garage Mower",
        },
        account_type="dreame",
        country="eu",
    )

    assert descriptor is not None

    snapshot = snapshot_from_device(descriptor, _FakeDevice())

    assert snapshot.activity == "paused"
    assert snapshot.state == "paused"
    assert snapshot.started is True
    assert snapshot.mapping_available is True
    assert snapshot.online is True
    assert snapshot.serial_number == "G2408051LEE0090632"
    assert snapshot.cloud_update_time == "2025-04-22 10:03:44"
    assert snapshot.capabilities == ("lidar_navigation", "map")
