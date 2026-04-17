"""Regression checks for the standalone mower Python package."""

from dreame_lawn_mower_client import (
    CAMERA_PROBE_PROPERTY_KEYS,
    MAP_PROBE_PROPERTY_KEYS,
    MOWER_ERROR_PROPERTY_KEY,
    MOWER_PROPERTY_HINTS,
    MOWER_RAW_STATUS_PROPERTY_KEY,
    MOWER_STATE_PROPERTY_KEY,
    DreameLawnMowerCameraFeatureSupport,
    DreameLawnMowerClient,
    DreameLawnMowerMapSummary,
    DreameLawnMowerMapView,
    DreameLawnMowerRemoteControlSupport,
    build_camera_probe_payload,
    build_cloud_property_summary,
    build_map_probe_payload,
    map_summary_from_map_data,
    map_summary_to_dict,
    mower_error_label,
    mower_state_label,
)
from dreame_lawn_mower_client.client import DreameLawnMowerClient as ClientFromModule
from dreame_lawn_mower_client.models import (
    DreameLawnMowerMapSummary as MapSummaryFromModule,
)
from dreame_lawn_mower_client.models import (
    DreameLawnMowerMapView as MapViewFromModule,
)


def test_public_package_exports_client() -> None:
    assert DreameLawnMowerClient is ClientFromModule


def test_public_package_exports_map_helpers() -> None:
    assert DreameLawnMowerMapSummary is MapSummaryFromModule
    assert DreameLawnMowerMapView is MapViewFromModule
    assert DreameLawnMowerCameraFeatureSupport.__name__.endswith(
        "CameraFeatureSupport"
    )
    assert DreameLawnMowerRemoteControlSupport.__name__.endswith(
        "RemoteControlSupport"
    )
    assert callable(map_summary_from_map_data)
    assert callable(map_summary_to_dict)
    assert callable(build_camera_probe_payload)
    assert callable(build_cloud_property_summary)
    assert callable(build_map_probe_payload)
    assert "10001.1" in CAMERA_PROBE_PROPERTY_KEYS
    assert "2.1" in MAP_PROBE_PROPERTY_KEYS


def test_public_package_client_has_cloud_probe_helpers() -> None:
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_device_info")
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_user_features")
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_device_list_page")
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_properties")
    assert hasattr(DreameLawnMowerClient, "async_scan_cloud_properties")
    assert hasattr(DreameLawnMowerClient, "async_probe_map_sources")
    assert hasattr(DreameLawnMowerClient, "async_get_camera_feature_support")
    assert hasattr(DreameLawnMowerClient, "async_probe_camera_sources")
    assert hasattr(DreameLawnMowerClient, "async_probe_camera_stream_handshake")
    assert hasattr(DreameLawnMowerClient, "async_request_photo_info")
    assert hasattr(DreameLawnMowerClient, "async_get_remote_control_support")
    assert hasattr(DreameLawnMowerClient, "async_remote_control_move_step")
    assert hasattr(DreameLawnMowerClient, "async_remote_control_stop")


def test_public_package_exports_app_protocol_helpers() -> None:
    assert MOWER_RAW_STATUS_PROPERTY_KEY == "1.1"
    assert MOWER_STATE_PROPERTY_KEY == "2.1"
    assert MOWER_ERROR_PROPERTY_KEY == "2.2"
    assert MOWER_PROPERTY_HINTS["1.1"] == "raw_status_blob"
    assert mower_state_label(11) == "Mapping"
    assert mower_state_label("13") == "Charging Completed"
    assert mower_state_label(999) is None
    assert mower_error_label(31) == "Left wheel speed"
    assert mower_error_label(-1) == "No error"
    assert mower_error_label(0) == "No error"
    assert mower_error_label(99999) is None
