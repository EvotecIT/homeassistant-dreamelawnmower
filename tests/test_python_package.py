"""Regression checks for the standalone mower Python package."""

from dreame_lawn_mower_client import (
    CAMERA_PROBE_PROPERTY_KEYS,
    DEFAULT_APK_RESEARCH_TERMS,
    DEFAULT_DECOMPILED_SOURCE_SUFFIXES,
    DEFAULT_DREAMEHOME_ASSET_TERMS,
    MAP_PROBE_PROPERTY_KEYS,
    MOWER_ERROR_PROPERTY_KEY,
    MOWER_PROPERTY_HINTS,
    MOWER_RAW_STATUS_PROPERTY_KEY,
    MOWER_STATE_PROPERTY_KEY,
    DreameLawnMowerCameraFeatureSupport,
    DreameLawnMowerClient,
    DreameLawnMowerFirmwareUpdateSupport,
    DreameLawnMowerMapDiagnostics,
    DreameLawnMowerMapSummary,
    DreameLawnMowerMapView,
    DreameLawnMowerRemoteControlSupport,
    DreameLawnMowerStatusBlob,
    analyze_decompiled_sources,
    analyze_dreamehome_apk,
    analyze_dreamehome_assets,
    build_camera_probe_payload,
    build_cloud_key_definition_summary,
    build_cloud_property_summary,
    build_jadx_command,
    build_map_probe_payload,
    decode_mower_status_blob,
    firmware_update_support_from_device,
    map_diagnostics_from_device,
    map_summary_from_map_data,
    map_summary_to_dict,
    mower_error_label,
    mower_state_label,
    run_jadx_decompile,
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
    assert DreameLawnMowerFirmwareUpdateSupport.__name__.endswith(
        "FirmwareUpdateSupport"
    )
    assert DreameLawnMowerMapDiagnostics.__name__.endswith("MapDiagnostics")
    assert DreameLawnMowerStatusBlob.__name__.endswith("StatusBlob")
    assert callable(map_summary_from_map_data)
    assert callable(map_summary_to_dict)
    assert callable(map_diagnostics_from_device)
    assert callable(firmware_update_support_from_device)
    assert callable(analyze_decompiled_sources)
    assert callable(analyze_dreamehome_assets)
    assert callable(analyze_dreamehome_apk)
    assert callable(build_camera_probe_payload)
    assert callable(build_cloud_key_definition_summary)
    assert callable(build_cloud_property_summary)
    assert callable(build_jadx_command)
    assert callable(build_map_probe_payload)
    assert callable(run_jadx_decompile)
    assert "10001.1" in CAMERA_PROBE_PROPERTY_KEYS
    assert "sendAction" in DEFAULT_APK_RESEARCH_TERMS
    assert ".java" in DEFAULT_DECOMPILED_SOURCE_SUFFIXES
    assert "object_name" in DEFAULT_DREAMEHOME_ASSET_TERMS
    assert "2.1" in MAP_PROBE_PROPERTY_KEYS


def test_public_package_client_has_cloud_probe_helpers() -> None:
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_device_info")
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_user_features")
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_device_list_page")
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_properties")
    assert hasattr(DreameLawnMowerClient, "async_scan_cloud_properties")
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_key_definition")
    assert hasattr(DreameLawnMowerClient, "async_probe_map_sources")
    assert hasattr(DreameLawnMowerClient, "async_get_camera_feature_support")
    assert hasattr(DreameLawnMowerClient, "async_get_firmware_update_support")
    assert hasattr(DreameLawnMowerClient, "async_get_status_blob")
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
    assert decode_mower_status_blob([206, 0, 206]).frame_valid is True
