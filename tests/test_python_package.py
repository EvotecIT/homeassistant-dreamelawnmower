"""Regression checks for the standalone mower Python package."""

from dreame_lawn_mower_client import (
    CAMERA_PROBE_PROPERTY_KEYS,
    DEBUG_OTA_LIST_URL,
    DEBUG_OTA_TRACKS,
    DEFAULT_APK_RESEARCH_TERMS,
    DEFAULT_DECOMPILED_SOURCE_SUFFIXES,
    DEFAULT_DREAMEHOME_ASSET_TERMS,
    MAP_HISTORY_PROPERTY_KEYS,
    MAP_PROBE_PROPERTY_KEYS,
    MOWER_BATTERY_PROPERTY_KEY,
    MOWER_BLUETOOTH_PROPERTY_KEY,
    MOWER_ERROR_PROPERTY_KEY,
    MOWER_PROPERTY_HINTS,
    MOWER_RAW_STATUS_PROPERTY_KEY,
    MOWER_RUNTIME_STATUS_PROPERTY_KEY,
    MOWER_STATE_KEYS,
    MOWER_STATE_PROPERTY_KEY,
    MOWER_TASK_PROPERTY_KEY,
    MOWER_TIME_PROPERTY_KEY,
    MOWING_PREFERENCE_PROPERTY_KEY,
    MOWING_PREFERENCE_UPDATE_FIELDS,
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
    apply_mowing_preference_changes,
    batch_data_text,
    build_camera_probe_payload,
    build_cloud_key_definition_summary,
    build_cloud_property_history_summary,
    build_cloud_property_summary,
    build_debug_ota_catalog_url,
    build_jadx_command,
    build_map_probe_payload,
    build_schedule_enable_status_request,
    build_schedule_upload_requests,
    decode_batch_mowing_preferences,
    decode_batch_ota_info,
    decode_batch_schedule_payload,
    decode_mower_status_blob,
    decode_mower_task_status,
    decode_mowing_preference_payload,
    decode_schedule_payload_text,
    encode_mowing_preference_payload,
    encode_schedule_payload_text,
    firmware_update_support_from_device,
    key_definition_label,
    map_diagnostics_from_device,
    map_summary_from_map_data,
    map_summary_to_dict,
    mower_error_label,
    mower_property_hint,
    mower_realtime_property_name,
    mower_state_key,
    mower_state_label,
    normalize_debug_ota_catalog_payload,
    remote_control_block_reason,
    remote_control_state_safe,
    run_jadx_decompile,
    summarize_mowing_preference_info,
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
    assert callable(remote_control_block_reason)
    assert callable(remote_control_state_safe)
    assert callable(key_definition_label)
    assert callable(analyze_decompiled_sources)
    assert callable(analyze_dreamehome_assets)
    assert callable(analyze_dreamehome_apk)
    assert callable(build_camera_probe_payload)
    assert callable(build_debug_ota_catalog_url)
    assert callable(build_cloud_key_definition_summary)
    assert callable(build_cloud_property_history_summary)
    assert callable(build_cloud_property_summary)
    assert callable(build_jadx_command)
    assert callable(build_map_probe_payload)
    assert callable(batch_data_text)
    assert callable(apply_mowing_preference_changes)
    assert callable(decode_batch_mowing_preferences)
    assert callable(decode_batch_ota_info)
    assert callable(decode_batch_schedule_payload)
    assert callable(decode_mowing_preference_payload)
    assert callable(encode_mowing_preference_payload)
    assert callable(normalize_debug_ota_catalog_payload)
    assert callable(run_jadx_decompile)
    assert callable(summarize_mowing_preference_info)
    assert "10001.1" in CAMERA_PROBE_PROPERTY_KEYS
    assert "BUILD" in DEBUG_OTA_TRACKS
    assert "https://ota.tsingting.tech/api/version/" in DEBUG_OTA_LIST_URL
    assert "sendAction" in DEFAULT_APK_RESEARCH_TERMS
    assert ".java" in DEFAULT_DECOMPILED_SOURCE_SUFFIXES
    assert "object_name" in DEFAULT_DREAMEHOME_ASSET_TERMS
    assert "2.1" in MAP_PROBE_PROPERTY_KEYS
    assert "6.1" in MAP_HISTORY_PROPERTY_KEYS
    assert MOWING_PREFERENCE_PROPERTY_KEY == "2.52"
    assert "mowing_height_cm" in MOWING_PREFERENCE_UPDATE_FIELDS


def test_public_package_client_has_cloud_probe_helpers() -> None:
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_device_info")
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_device_otc_info")
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_user_features")
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_device_list_page")
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_properties")
    assert hasattr(DreameLawnMowerClient, "async_scan_cloud_properties")
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_key_definition")
    assert hasattr(DreameLawnMowerClient, "async_get_cloud_firmware_check")
    assert hasattr(DreameLawnMowerClient, "async_approve_firmware_update")
    assert hasattr(DreameLawnMowerClient, "async_probe_map_sources")
    assert hasattr(DreameLawnMowerClient, "async_get_camera_feature_support")
    assert hasattr(DreameLawnMowerClient, "async_get_firmware_update_support")
    assert hasattr(DreameLawnMowerClient, "async_get_status_blob")
    assert hasattr(DreameLawnMowerClient, "async_probe_camera_sources")
    assert hasattr(DreameLawnMowerClient, "async_probe_camera_stream_handshake")
    assert hasattr(DreameLawnMowerClient, "async_request_photo_info")
    assert hasattr(DreameLawnMowerClient, "async_capture_operation_snapshot")
    assert hasattr(DreameLawnMowerClient, "async_get_remote_control_support")
    assert hasattr(DreameLawnMowerClient, "async_remote_control_move_step")
    assert hasattr(DreameLawnMowerClient, "async_remote_control_stop")
    assert hasattr(DreameLawnMowerClient, "async_get_app_schedules")
    assert hasattr(DreameLawnMowerClient, "async_get_batch_schedules")
    assert hasattr(DreameLawnMowerClient, "async_get_mowing_preferences")
    assert hasattr(DreameLawnMowerClient, "async_plan_app_mowing_preference_update")
    assert hasattr(DreameLawnMowerClient, "async_plan_app_schedule_upload")
    assert hasattr(DreameLawnMowerClient, "async_get_batch_mowing_preferences")
    assert hasattr(DreameLawnMowerClient, "async_get_batch_ota_info")
    assert hasattr(DreameLawnMowerClient, "async_get_debug_ota_catalog")
    assert hasattr(DreameLawnMowerClient, "async_get_weather_protection")
    assert hasattr(DreameLawnMowerClient, "async_set_app_schedule_plan_enabled")


def test_public_package_exports_app_map_renderer() -> None:
    import dreame_lawn_mower_client

    assert hasattr(dreame_lawn_mower_client, "render_app_map_payload_png")


def test_public_package_exports_schedule_helpers() -> None:
    payload_text = '{"d":[[0,1,"","EODBJwAAADDgwScAAAA="]]}'
    plans = decode_schedule_payload_text(payload_text)

    assert encode_schedule_payload_text(plans) == payload_text
    assert build_schedule_enable_status_request(
        map_index=0,
        version=123,
        plans=plans,
    ) == {"m": "s", "t": "SCHDSV2", "d": {"i": 0, "v": 123, "s": [1]}}
    assert build_schedule_upload_requests(
        map_index=0,
        payload_text='{"d":[]}',
        version=123,
        chunk_size=100,
    ) == [
        {"m": "s", "t": "SCHDIV2", "d": {"i": 0, "l": 8, "v": 123}},
        {"m": "s", "t": "SCHDDV2", "d": {"s": 0, "l": 8, "d": '{"d":[]}', "v": 123}},
    ]


def test_public_package_exports_app_protocol_helpers() -> None:
    assert MOWER_RAW_STATUS_PROPERTY_KEY == "1.1"
    assert MOWER_RUNTIME_STATUS_PROPERTY_KEY == "1.4"
    assert MOWER_STATE_PROPERTY_KEY == "2.1"
    assert MOWER_ERROR_PROPERTY_KEY == "2.2"
    assert MOWER_TASK_PROPERTY_KEY == "2.50"
    assert MOWER_TIME_PROPERTY_KEY == "2.51"
    assert MOWER_BATTERY_PROPERTY_KEY == "3.1"
    assert MOWER_BLUETOOTH_PROPERTY_KEY == "1.53"
    assert MOWER_PROPERTY_HINTS["1.1"] == "raw_status_blob"
    assert MOWER_PROPERTY_HINTS["1.4"] == "runtime_status_blob"
    assert MOWER_PROPERTY_HINTS["1.53"] == "bluetooth_connected"
    assert MOWER_PROPERTY_HINTS["2.50"] == "task_status"
    assert MOWER_PROPERTY_HINTS["2.51"] == "device_time"
    assert MOWER_PROPERTY_HINTS["3.1"] == "battery_level"
    assert mower_property_hint("2.51") == "device_time"
    assert mower_property_hint("9.4") is None
    assert mower_realtime_property_name("2.51", "UNKNOWN_REALTIME_2.51") == (
        "device_time"
    )
    assert mower_realtime_property_name("9.4", "UNKNOWN_REALTIME_9.4") == (
        "UNKNOWN_REALTIME_9.4"
    )
    assert MOWER_STATE_KEYS["5"] == "returning"
    assert mower_state_key("1") == "mowing"
    assert mower_state_key(5) == "returning"
    assert mower_state_key("13") == "charging_completed"
    assert mower_state_key(999) is None
    assert mower_state_label(11) == "Mapping"
    assert mower_state_label("13") == "Charging Completed"
    assert mower_state_label(999) is None
    assert mower_error_label(31) == "Left wheel speed"
    assert mower_error_label(-1) == "No error"
    assert mower_error_label(0) == "No error"
    assert mower_error_label(99999) is None
    assert decode_mower_status_blob([206, 0, 206]).frame_valid is True
    assert decode_mower_task_status('{"d":{"exe":true},"t":"TASK"}') == {
        "type": "TASK",
        "executing": True,
    }
