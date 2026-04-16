"""Unit tests for mower model normalization."""

from __future__ import annotations

from types import SimpleNamespace

from dreame_lawn_mower_client.models import (
    descriptor_from_cloud_record,
    display_name_for_model,
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
        self.unknown_properties = {}
        self.realtime_properties = {}
        self.last_realtime_message = None

    def get_property(self, _prop):
        return None


class _FakeErrorStatus(_FakeStatus):
    def __init__(self) -> None:
        super().__init__()
        self.error = SimpleNamespace(value=31)
        self.error_name = "left_wheell_speed"
        self.has_error = True
        self.attributes = {
            **self.attributes,
            "error": "Left wheell speed",
        }


class _FakeErrorDevice(_FakeDevice):
    def __init__(self) -> None:
        super().__init__()
        self.status = _FakeErrorStatus()


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


def test_descriptor_uses_cloud_display_name_for_unknown_rebadge_model() -> None:
    descriptor = descriptor_from_cloud_record(
        {
            "did": "device-2",
            "model": "mova.mower.x1234",
            "customName": "Front Mower",
            "deviceInfo": {"displayName": "Lidax Ultra 800"},
        },
        account_type="mova",
        country="eu",
    )

    assert descriptor is not None
    assert descriptor.display_model == "LiDAX Ultra 800"
    assert descriptor.title == "Front Mower (LiDAX Ultra 800)"


def test_descriptor_accepts_generic_mower_prefix() -> None:
    descriptor = descriptor_from_cloud_record(
        {
            "did": "device-3",
            "model": "lidax.mower.z1000",
            "deviceInfo": {"displayName": "Viax 300"},
        },
        account_type="dreame",
        country="eu",
    )

    assert descriptor is not None
    assert descriptor.model == "lidax.mower.z1000"
    assert descriptor.display_model == "Viax 300"


def test_descriptor_rejects_non_mower_models() -> None:
    descriptor = descriptor_from_cloud_record(
        {
            "did": "device-4",
            "model": "dreame.vacuum.r2216",
            "deviceInfo": {"displayName": "Vacuum"},
        },
        account_type="dreame",
        country="eu",
    )

    assert descriptor is None


def test_display_name_for_model_prefers_known_mapping_over_fallback_name() -> None:
    assert (
        display_name_for_model(
            "dreame.mower.g2408",
            fallback_name="Lidax Ultra 800",
        )
        == "A2"
    )


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
    assert snapshot.unknown_property_count == 0
    assert snapshot.realtime_property_count == 0
    assert snapshot.last_realtime_method is None
    assert snapshot.capabilities == ("lidar_navigation", "map")


def test_snapshot_prioritizes_error_activity_but_keeps_paused_state_context() -> None:
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

    snapshot = snapshot_from_device(descriptor, _FakeErrorDevice())

    assert snapshot.activity == "error"
    assert snapshot.state == "paused"
    assert snapshot.error_code == 31
    assert snapshot.error_name == "left_wheell_speed"
    assert snapshot.error_text == "Left wheell speed"
    assert snapshot.error_display == "Left wheel speed"


def test_snapshot_tracks_realtime_and_unknown_diagnostics() -> None:
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

    device = _FakeDevice()
    device.unknown_properties = {
        -113852866: {"did": -113852866, "value": 123},
    }
    device.realtime_properties = {
        "3.1": {"siid": 3, "piid": 1, "value": 80},
        "9.4": {"siid": 9, "piid": 4, "value": {"blob": 123}},
    }
    device.last_realtime_message = {
        "received_at": 123456.0,
        "message": {"method": "properties_changed", "params": []},
    }

    snapshot = snapshot_from_device(descriptor, device)

    assert snapshot.unknown_property_count == 1
    assert snapshot.realtime_property_count == 2
    assert snapshot.last_realtime_method == "properties_changed"
