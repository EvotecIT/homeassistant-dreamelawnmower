"""Unit tests for mower model normalization."""

from __future__ import annotations

from types import SimpleNamespace

from dreame_lawn_mower_client.models import (
    descriptor_from_cloud_record,
    display_name_for_model,
    firmware_update_support_from_device,
    remote_control_state_safe,
    snapshot_from_device,
)

from .fixture_data import load_json_fixture


class _FakeInfo:
    def __init__(self) -> None:
        self.firmware_version = "4.3.6_0320"
        self.hardware_version = "Linux"
        self.raw = {
            "online": True,
            "sn": "G2408051LEE0090632",
            "ver": "4.3.6_0320",
            "updateTime": "2025-04-22 10:03:44",
            "latestStatus": 13,
            "featureCode": -1,
            "featureCode2": -1,
            "status": "Live",
            "deviceInfo": {
                "pluginForceUpdate": True,
                "firmwareDevelopType": "SINGLE_PLATFORM",
                "releaseAt": "1741340511687",
                "updatedAt": "1762737943926",
                "status": "Live",
            },
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
    assert snapshot.charging is False
    assert snapshot.raw_charging is False
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


def test_snapshot_uses_error_code_label_when_text_says_no_error() -> None:
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
    device.status.error = SimpleNamespace(value=31)
    device.status.error_name = "no_error"
    device.status.has_error = True
    device.status.attributes = {
        **device.status.attributes,
        "error": "No error",
    }

    snapshot = snapshot_from_device(descriptor, device)

    assert snapshot.activity == "error"
    assert snapshot.error_code == 31
    assert snapshot.error_name == "no_error"
    assert snapshot.error_text == "No error"
    assert snapshot.error_display == "Left wheel speed"


def test_snapshot_falls_back_to_error_code_when_label_is_unknown() -> None:
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
    device.status.error = SimpleNamespace(value=73)
    device.status.error_name = "no_error"
    device.status.has_error = True
    device.status.attributes = {
        **device.status.attributes,
        "error": "No error",
    }

    snapshot = snapshot_from_device(descriptor, device)

    assert snapshot.activity == "error"
    assert snapshot.error_display == "Error 73"


def test_snapshot_uses_realtime_error_when_status_says_no_error() -> None:
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
    device.status.state = SimpleNamespace(name="CHARGING")
    device.status.state_name = "charging"
    device.status.error = SimpleNamespace(value=-1)
    device.status.error_name = "no_error"
    device.status.has_error = False
    device.status.docked = True
    device.status.returning = True
    device.status.attributes = {
        **device.status.attributes,
        "charging": True,
        "error": "No error",
        "mower_state": "charging",
        "returning": True,
        "status": "Returning",
    }
    device.realtime_properties = {
        "2.2": {
            "siid": 2,
            "piid": 2,
            "property_name": "ERROR",
            "value": 54,
        }
    }

    snapshot = snapshot_from_device(descriptor, device)

    assert snapshot.activity == "error"
    assert snapshot.error_code == 54
    assert snapshot.raw_error_code == -1
    assert snapshot.realtime_error_code == 54
    assert snapshot.error_source == "realtime_property_2.2"
    assert snapshot.error_name == "no_error"
    assert snapshot.error_text == "No error"
    assert snapshot.error_display == "Edge"
    assert snapshot.docked is True
    assert snapshot.raw_docked is True
    assert snapshot.charging is True
    assert snapshot.raw_charging is True
    assert snapshot.returning is False
    assert snapshot.raw_returning is True
    assert snapshot.realtime_property_count == 1


def test_snapshot_treats_charging_state_as_effectively_docked() -> None:
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
    device.status.state = SimpleNamespace(name="CHARGING_COMPLETED")
    device.status.state_name = "charging_completed"
    device.status.docked = False
    device.status.attributes = {
        **device.status.attributes,
        "charging": False,
        "mower_state": "charging_completed",
    }

    snapshot = snapshot_from_device(descriptor, device)

    assert snapshot.activity == "docked"
    assert snapshot.docked is True
    assert snapshot.raw_docked is False
    assert snapshot.charging is False
    assert snapshot.raw_charging is False
    assert snapshot.started is False
    assert snapshot.raw_started is True


def test_snapshot_treats_charging_state_as_effectively_charging() -> None:
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
    device.status.state = SimpleNamespace(name="CHARGING")
    device.status.state_name = "charging"
    device.status.charging = False
    device.status.attributes = {
        **device.status.attributes,
        "charging": False,
        "mower_state": "charging",
    }

    snapshot = snapshot_from_device(descriptor, device)

    assert snapshot.activity == "docked"
    assert snapshot.charging is True
    assert snapshot.raw_charging is False
    assert snapshot.docked is True


def test_snapshot_treats_docked_sticky_returning_as_raw_only() -> None:
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
    device.status.state = SimpleNamespace(name="CHARGING")
    device.status.state_name = "charging"
    device.status.returning = True
    device.status.attributes = {
        **device.status.attributes,
        "charging": True,
        "mower_state": "charging",
        "returning": True,
    }

    snapshot = snapshot_from_device(descriptor, device)

    assert snapshot.activity == "docked"
    assert snapshot.docked is True
    assert snapshot.charging is True
    assert snapshot.raw_charging is True
    assert snapshot.started is False
    assert snapshot.raw_started is True
    assert snapshot.returning is False
    assert snapshot.raw_returning is True


def test_snapshot_treats_returning_running_flag_as_returning_not_mowing() -> None:
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
    device.status.state = SimpleNamespace(name="RETURNING")
    device.status.state_name = "returning"
    device.status.returning = True
    device.status.running = True
    device.status.docked = False
    device.status.attributes = {
        **device.status.attributes,
        "charging": False,
        "mower_state": "returning",
        "returning": True,
        "running": True,
    }

    snapshot = snapshot_from_device(descriptor, device)

    assert snapshot.activity == "returning"
    assert snapshot.returning is True
    assert snapshot.raw_returning is True
    assert snapshot.mowing is False
    assert snapshot.started is True
    assert snapshot.docked is False


def test_snapshot_treats_returning_state_as_returning_without_raw_flag() -> None:
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
    device.status.state = SimpleNamespace(name="RETURNING")
    device.status.state_name = "returning"
    device.status.returning = False
    device.status.running = False
    device.status.docked = False
    device.status.attributes = {
        **device.status.attributes,
        "charging": False,
        "mower_state": "returning",
        "returning": False,
        "running": False,
        "started": True,
    }

    snapshot = snapshot_from_device(descriptor, device)

    assert snapshot.activity == "returning"
    assert snapshot.returning is True
    assert snapshot.raw_returning is False
    assert snapshot.mowing is False
    assert snapshot.started is True
    assert snapshot.docked is False


def test_snapshot_treats_mowing_state_as_mowing_without_running_flag() -> None:
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
    device.status.state = SimpleNamespace(name="MOWING")
    device.status.state_name = "mowing"
    device.status.running = False
    device.status.docked = False
    device.status.attributes = {
        **device.status.attributes,
        "charging": False,
        "mower_state": "mowing",
        "running": False,
        "started": True,
    }

    snapshot = snapshot_from_device(descriptor, device)

    assert snapshot.activity == "mowing"
    assert snapshot.mowing is True
    assert snapshot.raw_started is True
    assert snapshot.started is True
    assert snapshot.docked is False


def test_field_trip_returning_fixture_matches_normalized_state() -> None:
    payload, snapshot = _field_trip_fixture_snapshot(
        "field_trip_returning_summary.json"
    )
    expected = payload["capture"]["expected"]

    assert snapshot.activity == expected["activity"]
    assert snapshot.mowing is expected["mowing"]
    assert snapshot.returning is expected["returning"]
    assert snapshot.docked is expected["docked"]
    assert snapshot.started is expected["started"]


def test_field_trip_charging_completed_fixture_matches_normalized_state() -> None:
    payload, snapshot = _field_trip_fixture_snapshot(
        "field_trip_charging_completed_summary.json"
    )
    _assert_field_trip_snapshot_matches_expected(payload, snapshot)


def test_field_trip_charging_fixture_matches_normalized_state() -> None:
    payload, snapshot = _field_trip_fixture_snapshot("field_trip_charging_summary.json")
    _assert_field_trip_snapshot_matches_expected(payload, snapshot)


def _field_trip_fixture_snapshot(fixture_name: str):
    payload = load_json_fixture(fixture_name)
    raw_status = payload["capture"]["raw_status"]
    descriptor = descriptor_from_cloud_record(
        {
            "did": "field-trip-device",
            "model": payload["model"],
            "customName": "Field Trip Mower",
        },
        account_type="dreame",
        country="eu",
    )

    assert descriptor is not None

    device = _FakeDevice()
    device.status.state = SimpleNamespace(name=raw_status["state"])
    device.status.state_name = raw_status["state_name"]
    device.status.battery_level = raw_status["battery_level"]
    device.status.charging = raw_status["charging"]
    device.status.docked = raw_status["docked"]
    device.status.running = raw_status["running"]
    device.status.returning = raw_status["returning"]
    device.status.started = raw_status["started"]
    device.status.attributes = raw_status["attributes"]

    snapshot = snapshot_from_device(descriptor, device)

    return payload, snapshot


def _assert_field_trip_snapshot_matches_expected(payload, snapshot) -> None:
    expected = payload["capture"]["expected"]

    assert snapshot.activity == expected["activity"]
    assert snapshot.mowing is expected["mowing"]
    assert snapshot.returning is expected["returning"]
    assert snapshot.docked is expected["docked"]
    assert snapshot.raw_docked is expected["raw_docked"]
    assert snapshot.charging is expected["charging"]
    assert snapshot.raw_charging is expected["raw_charging"]
    assert snapshot.started is expected["started"]
    assert snapshot.raw_started is expected["raw_started"]
    assert remote_control_state_safe(snapshot) is expected["manual_drive_safe"]


def test_snapshot_ignores_sticky_has_error_when_error_details_say_no_error() -> None:
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
    device.status.state = SimpleNamespace(name="CHARGING")
    device.status.state_name = "charging"
    device.status.error = SimpleNamespace(value=-1)
    device.status.error_name = "no_error"
    device.status.has_error = True
    device.status.attributes = {
        **device.status.attributes,
        "charging": True,
        "error": "No error",
        "mower_state": "charging",
    }

    snapshot = snapshot_from_device(descriptor, device)

    assert snapshot.activity == "docked"
    assert snapshot.error_code == -1
    assert snapshot.error_name == "no_error"
    assert snapshot.error_text == "No error"


def test_snapshot_keeps_bare_has_error_when_no_error_details_exist() -> None:
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
    device.status.error = None
    device.status.error_name = None
    device.status.has_error = True
    device.status.attributes = {
        key: value
        for key, value in device.status.attributes.items()
        if key != "error"
    }

    snapshot = snapshot_from_device(descriptor, device)

    assert snapshot.activity == "error"
    assert snapshot.error_code is None
    assert snapshot.error_name is None
    assert snapshot.error_text is None


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


def test_firmware_update_support_preserves_evidence_without_guessing_ota() -> None:
    device = _FakeDevice()

    support = firmware_update_support_from_device(
        device,
        cloud_device_info={
            "ver": "4.3.6_0320",
            "deviceInfo": {"pluginForceUpdate": False},
        },
        cloud_device_list_page={
            "page": {
                "records": [
                    {
                        "latestStatus": 13,
                        "deviceInfo": {"firmwareDevelopType": "SINGLE_PLATFORM"},
                    }
                ]
            }
        },
    )

    assert support.current_version == "4.3.6_0320"
    assert support.hardware_version == "Linux"
    assert support.cloud_update_time == "2025-04-22 10:03:44"
    assert support.plugin_force_update is True
    assert support.plugin_force_update_sources == {
        "cached_device_info": True,
        "cloud_device_info": False,
    }
    assert support.plugin_status == "Live"
    assert support.firmware_develop_type == "SINGLE_PLATFORM"
    assert support.latest_status == 13
    assert support.update_available is None
    assert support.candidate_update_fields["info.ver"] == "4.3.6_0320"
    assert support.candidate_update_fields["info.updateTime"] == (
        "2025-04-22 10:03:44"
    )
    assert support.candidate_update_fields["deviceInfo.pluginForceUpdate"] is True
    assert support.candidate_update_fields[
        "cloud_device_info.deviceInfo.pluginForceUpdate"
    ] is False
    assert support.candidate_update_fields[
        "cloud_device_list_page.page.records[0].latestStatus"
    ] == 13
    assert support.candidate_update_fields[
        "cloud_device_list_page.page.records[0].deviceInfo.firmwareDevelopType"
    ] == "SINGLE_PLATFORM"
    assert support.warnings == ("plugin_force_update_conflict",)
    assert "differs across cloud metadata sources" in support.reason
    assert support.evidence["info"]["latestStatus"] == 13
    assert support.evidence["cloud_device_info"]["deviceInfo"] == {
        "pluginForceUpdate": False
    }
    assert support.evidence["cloud_device_list_page"]["page"] == {"record_count": 1}
    assert support.evidence["cloud_device_list_page"]["records"][0] == {
        "latestStatus": 13,
        "deviceInfo": {"firmwareDevelopType": "SINGLE_PLATFORM"},
    }
    assert support.evidence["pluginForceUpdateSources"] == {
        "cached_device_info": True,
        "cloud_device_info": False,
    }


def test_firmware_update_support_tracks_live_like_plugin_sources() -> None:
    device = _FakeDevice()

    support = firmware_update_support_from_device(
        device,
        cloud_device_info={
            "ver": "4.3.6_0320",
            "latestStatus": 6,
            "deviceInfo": {"pluginForceUpdate": False},
        },
        cloud_device_list_page={
            "records": [
                {
                    "ver": "4.3.6_0320",
                    "latestStatus": 6,
                    "deviceInfo": {
                        "pluginForceUpdate": True,
                        "firmwareDevelopType": "SINGLE_PLATFORM",
                    },
                }
            ]
        },
    )

    assert support.plugin_force_update_sources == {
        "cached_device_info": True,
        "cloud_device_info": False,
        "cloud_device_list_page.records[0]": True,
    }
    assert support.warnings == ("plugin_force_update_conflict",)
    assert support.update_available is None
    assert support.candidate_update_fields[
        "cloud_device_list_page.records[0].deviceInfo.pluginForceUpdate"
    ] is True
    assert support.candidate_update_fields[
        "cloud_device_list_page.records[0].deviceInfo.firmwareDevelopType"
    ] == "SINGLE_PLATFORM"


def test_firmware_update_support_marks_update_state() -> None:
    device = _FakeDevice()
    device.status.state = SimpleNamespace(name="UPGRADING")

    support = firmware_update_support_from_device(device)

    assert support.update_state == "upgrading"
    assert support.update_available is None
    assert support.reason == "Mower reports an update-related state."


def test_firmware_update_support_summarizes_root_records() -> None:
    device = _FakeDevice()

    support = firmware_update_support_from_device(
        device,
        cloud_device_list_page={
            "current": 1,
            "size": 20,
            "total": 1,
            "records": [{"latestStatus": 13}],
        },
    )

    assert support.evidence["cloud_device_list_page"] == {
        "current": 1,
        "size": 20,
        "total": 1,
        "root": {"record_count": 1},
        "records": [{"latestStatus": 13}],
    }
    assert "cloud_device_list_page.total" not in support.candidate_update_fields
    assert support.candidate_update_fields[
        "cloud_device_list_page.records[0].latestStatus"
    ] == 13
