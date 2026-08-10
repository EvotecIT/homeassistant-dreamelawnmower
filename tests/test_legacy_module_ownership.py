"""Compatibility contracts for the decomposed legacy client modules."""

from __future__ import annotations

import dreame_lawn_mower_client.client as public_client
from dreame_lawn_mower_client._loader import load_internal_module

EXPECTED_HISTORICAL_CLIENT_EXPORTS = frozenset(
    """
    Any
    BytesIO
    CAMERA_PROBE_PROPERTY_KEYS
    CMS_GET_REQUEST
    DEFAULT_POINT_CLOUD_MAX_BYTES
    DeadlineExceededError
    DeviceException
    DreameLawnMowerAuthError
    DreameLawnMowerCameraFeatureSupport
    DreameLawnMowerCameraStreamRuntimeInputs
    DreameLawnMowerClient
    DreameLawnMowerConnectionError
    DreameLawnMowerDescriptor
    DreameLawnMowerError
    DreameLawnMowerFirmwareUpdateSupport
    DreameLawnMowerMapSummary
    DreameLawnMowerMapView
    DreameLawnMowerPointCloudDownload
    DreameLawnMowerPointCloudError
    DreameLawnMowerRemoteControlSupport
    DreameLawnMowerSnapshot
    DreameLawnMowerStatusBlob
    DreameLawnMowerTwoFactorRequiredError
    EMPTY_SCHEDULE_VERSION
    InvalidActionException
    MAP_HISTORY_PROPERTY_KEYS
    MAP_PROBE_PROPERTY_KEYS
    MOWER_BLUETOOTH_PROPERTY_KEY
    MOWER_ERROR_PROPERTY_KEY
    MOWER_PROPERTY_HINTS
    MOWER_RAW_STATUS_PROPERTY_KEY
    MOWER_RUNTIME_STATUS_PROPERTY_KEY
    MOWER_STATE_PROPERTY_KEY
    MOWER_TASK_PROPERTY_KEY
    MOWING_PREFERENCE_MODE_FIELD
    MOWING_PREFERENCE_PROPERTY_KEY
    Mapping
    MowingTaskResponseError
    REMOTE_CONTROL_MAX_ROTATION
    REMOTE_CONTROL_MAX_VELOCITY
    RESUME_MOWING_REQUEST
    SCHEDULE_CHUNK_SIZE
    SUPPORTED_ACCOUNT_TYPES
    Sequence
    UTC
    VOICE_LANGUAGE_CODES
    VOICE_LANGUAGE_INDEX_TO_CODE
    VOICE_LANGUAGE_INDEX_TO_LABEL
    VOICE_LANGUAGE_LABELS
    VOICE_LANGUAGE_LABEL_TO_INDEX
    VOICE_PROMPT_FIELDS
    annotations
    apply_mowing_preference_changes
    async_stop_then_dock
    asyncio
    build_camera_probe_payload
    build_cloud_property_summary
    build_cms_set_request
    build_debug_ota_catalog_url
    build_edge_mowing_request
    build_map_probe_payload
    build_operation_stage_diagnostics
    build_schedule_enable_status_request
    build_schedule_upload_requests
    build_spot_mowing_request
    build_zone_mowing_request
    camera_metadata_advertises_video
    camera_stream_block_reason
    datetime
    decode_batch_mowing_preferences
    decode_batch_ota_info
    decode_batch_schedule_payload
    decode_mower_status_blob
    decode_mower_task_status
    decode_mowing_preference_payload
    decode_schedule_payload_text
    derive_tx_video_app_credentials
    descriptor_from_cloud_record
    display_name_for_model
    encode_mowing_preference_payload
    encode_schedule_payload_text
    ensure_mowing_task_succeeded
    firmware_update_support_from_device
    hashlib
    html
    json
    key_definition_label
    maintenance_item_status
    maintenance_status_from_app_data
    map_diagnostics_from_device
    map_summary_from_map_data
    math
    mower_error_label
    mower_property_hint
    mower_realtime_property_name
    mower_state_key
    mower_state_label
    mowing_preference_mode_name
    normalize_debug_ota_catalog_payload
    normalize_mowing_preference_mode
    parse_batch_vector_map
    parse_pcd_metadata
    re
    remote_control_block_reason
    remote_control_state_safe
    render_app_map_payload_png
    render_vector_map_png
    replace
    reset_cms_counter
    run_with_deadline
    schedule_task_summary
    snapshot_from_device
    snapshot_session_control_state
    snapshot_with_cloud_presence
    snapshot_with_heartbeat_task_state
    summarize_mowing_preference_info
    time
    urllib
    vector_map_to_details
    vector_map_to_summary
    """.split()
)


def test_client_facade_preserves_historical_public_exports() -> None:
    assert set(public_client.__all__) == EXPECTED_HISTORICAL_CLIENT_EXPORTS


def test_client_facade_preserves_historical_private_bindings() -> None:
    facade = load_internal_module("client")

    assert facade._CLOUD_PRESENCE_REFRESH_INTERVAL == 60.0
    assert facade._FIRMWARE_DESCRIPTION_METADATA_KEYS
    assert facade._FIRMWARE_DESCRIPTION_PREFERRED_KEYS
    assert callable(facade._as_optional_text)
    assert callable(facade._json_safe)
    assert callable(facade._lower_enum_name)


def test_client_facade_composes_canonical_domain_owners() -> None:
    facade = load_internal_module("client")
    camera = load_internal_module("client_camera")
    core = load_internal_module("client_core")
    device_settings = load_internal_module("client_device_settings")
    maps = load_internal_module("client_maps")
    settings = load_internal_module("client_settings")

    assert facade.DreameLawnMowerClient.__bases__ == (
        camera._DreameLawnMowerCameraMixin,
        core._DreameLawnMowerClientCoreMixin,
        device_settings._DreameLawnMowerClientDeviceSettingsMixin,
        settings._DreameLawnMowerClientSettingsMixin,
        maps._DreameLawnMowerClientMapsMixin,
    )


def test_client_helper_facade_reexports_canonical_owners() -> None:
    facade = load_internal_module("client_helpers")
    core = load_internal_module("client_core_helpers")
    maps = load_internal_module("client_map_helpers")
    settings = load_internal_module("client_settings_helpers")
    shared = load_internal_module("client_shared_helpers")

    assert facade._sync_discover_devices is core._sync_discover_devices
    assert facade._download_point_cloud_content is maps._download_point_cloud_content
    assert facade._voice_settings_summary is settings._voice_settings_summary
    assert facade._positive_int is shared._positive_int


def test_map_facade_reexports_canonical_owner_types() -> None:
    facade = load_internal_module("map")
    editor = load_internal_module("map_editor")
    manager = load_internal_module("map_manager")
    renderer = load_internal_module("map_renderer")

    assert facade.DreameMapMowerMapEditor is editor.DreameMapMowerMapEditor
    assert facade.DreameMapMowerMapManager is manager.DreameMapMowerMapManager
    assert facade.DreameMowerMapRenderer is renderer.DreameMowerMapRenderer


def test_device_facade_reexports_types_and_composes_map_owner() -> None:
    commands = load_internal_module("device_commands")
    facade = load_internal_module("device")
    info = load_internal_module("device_info")
    map_owner = load_internal_module("device_map")
    state = load_internal_module("device_state")
    status = load_internal_module("device_status")

    assert facade.DreameMowerDeviceInfo is info.DreameMowerDeviceInfo
    assert facade.DreameMowerDeviceStatus is status.DreameMowerDeviceStatus
    assert state._DreameMowerDeviceStateMixin in facade.DreameMowerDevice.__mro__
    assert commands._DreameMowerDeviceCommandMixin in facade.DreameMowerDevice.__mro__
    assert map_owner._DreameMowerDeviceMapMixin in facade.DreameMowerDevice.__mro__


def test_protocol_facade_reexports_cloud_owner() -> None:
    facade = load_internal_module("protocol")
    cloud = load_internal_module("protocol_cloud")

    assert (
        facade.DreameMowerDreameHomeCloudProtocol
        is cloud.DreameMowerDreameHomeCloudProtocol
    )
    assert facade._cloud_request_log_value is cloud._cloud_request_log_value
    assert facade._post_cloud_response is cloud._post_cloud_response
