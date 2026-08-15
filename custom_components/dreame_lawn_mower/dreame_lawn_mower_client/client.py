"""Async-friendly reusable mower client."""

from __future__ import annotations

import asyncio
import hashlib as hashlib
import html as html
import json as json
import logging as _logging
import math as math
import re as re
import threading as _threading
import time
import typing as _typing
import urllib as urllib
from collections.abc import Mapping, Sequence
from dataclasses import replace as replace
from datetime import UTC as UTC
from datetime import datetime as datetime
from io import BytesIO as BytesIO
from typing import Any

from requests.exceptions import Timeout as _RequestsTimeout

from . import client_camera as _client_camera
from . import client_constants as _client_constants
from . import client_helpers as _client_helpers
from .app_protocol import (
    MOWER_BLUETOOTH_PROPERTY_KEY as MOWER_BLUETOOTH_PROPERTY_KEY,
)
from .app_protocol import MOWER_ERROR_PROPERTY_KEY as MOWER_ERROR_PROPERTY_KEY
from .app_protocol import MOWER_PROPERTY_HINTS as MOWER_PROPERTY_HINTS
from .app_protocol import (
    MOWER_RAW_STATUS_PROPERTY_KEY as MOWER_RAW_STATUS_PROPERTY_KEY,
)
from .app_protocol import (
    MOWER_RUNTIME_STATUS_PROPERTY_KEY as MOWER_RUNTIME_STATUS_PROPERTY_KEY,
)
from .app_protocol import MOWER_STATE_PROPERTY_KEY as MOWER_STATE_PROPERTY_KEY
from .app_protocol import MOWER_TASK_PROPERTY_KEY as MOWER_TASK_PROPERTY_KEY
from .app_protocol import decode_mower_status_blob as decode_mower_status_blob
from .app_protocol import decode_mower_task_status as decode_mower_task_status
from .app_protocol import key_definition_label as key_definition_label
from .app_protocol import mower_error_label as mower_error_label
from .app_protocol import mower_property_hint as mower_property_hint
from .app_protocol import mower_realtime_property_name as mower_realtime_property_name
from .app_protocol import mower_state_key as mower_state_key
from .app_protocol import mower_state_label as mower_state_label
from .batch_device_data import (
    decode_batch_mowing_preferences as decode_batch_mowing_preferences,
)
from .batch_device_data import decode_batch_ota_info as decode_batch_ota_info
from .batch_device_data import (
    decode_batch_schedule_payload as decode_batch_schedule_payload,
)
from .client_camera import _DreameLawnMowerCameraMixin
from .client_constants import (
    REMOTE_CONTROL_MAX_ROTATION as REMOTE_CONTROL_MAX_ROTATION,
)
from .client_constants import (
    REMOTE_CONTROL_MAX_VELOCITY as REMOTE_CONTROL_MAX_VELOCITY,
)
from .client_constants import VOICE_LANGUAGE_CODES as VOICE_LANGUAGE_CODES
from .client_constants import (
    VOICE_LANGUAGE_INDEX_TO_CODE as VOICE_LANGUAGE_INDEX_TO_CODE,
)
from .client_constants import (
    VOICE_LANGUAGE_INDEX_TO_LABEL as VOICE_LANGUAGE_INDEX_TO_LABEL,
)
from .client_constants import (
    VOICE_LANGUAGE_LABEL_TO_INDEX as VOICE_LANGUAGE_LABEL_TO_INDEX,
)
from .client_constants import VOICE_LANGUAGE_LABELS as VOICE_LANGUAGE_LABELS
from .client_constants import VOICE_PROMPT_FIELDS as VOICE_PROMPT_FIELDS
from .client_core import _DreameLawnMowerClientCoreMixin
from .client_core_helpers import (
    _FIRMWARE_DESCRIPTION_METADATA_KEYS as _FIRMWARE_DESCRIPTION_METADATA_KEYS,
)
from .client_core_helpers import (
    _FIRMWARE_DESCRIPTION_PREFERRED_KEYS as _FIRMWARE_DESCRIPTION_PREFERRED_KEYS,
)
from .client_device_settings import _DreameLawnMowerClientDeviceSettingsMixin
from .client_maps import (
    _POINT_CLOUD_CLOUD_SETUP_TIMEOUT_SECONDS,
    _POINT_CLOUD_GENERATION_PREFLIGHT_BUDGET_SECONDS,
    _POINT_CLOUD_STORED_PREFLIGHT_BUDGET_SECONDS,
    _DreameLawnMowerClientMapsMixin,
)
from .client_settings import (
    SCHEDULE_READ_TIMEOUT_SECONDS as _SCHEDULE_READ_TIMEOUT_SECONDS,
)
from .client_settings import _DreameLawnMowerClientSettingsMixin
from .deadline import DeadlineExceededError as DeadlineExceededError
from .deadline import run_with_deadline as run_with_deadline
from .debug_ota_catalog import (
    build_debug_ota_catalog_url as build_debug_ota_catalog_url,
)
from .debug_ota_catalog import (
    normalize_debug_ota_catalog_payload as normalize_debug_ota_catalog_payload,
)
from .docking import async_stop_then_dock
from .exceptions import DeviceException as DeviceException
from .exceptions import (
    DreameLawnMowerAuthError as DreameLawnMowerAuthError,
)
from .exceptions import (
    DreameLawnMowerCommandRejectedError as _DreameLawnMowerCommandRejectedError,
)
from .exceptions import (
    DreameLawnMowerConnectionError as DreameLawnMowerConnectionError,
)
from .exceptions import DreameLawnMowerError as DreameLawnMowerError
from .exceptions import (
    DreameLawnMowerTwoFactorRequiredError as DreameLawnMowerTwoFactorRequiredError,
)
from .exceptions import InvalidActionException as InvalidActionException
from .maintenance import CMS_GET_REQUEST as CMS_GET_REQUEST
from .maintenance import build_cms_set_request as build_cms_set_request
from .maintenance import maintenance_item_status as maintenance_item_status
from .maintenance import (
    maintenance_status_from_app_data as maintenance_status_from_app_data,
)
from .maintenance import reset_cms_counter as reset_cms_counter
from .map_probe import MAP_HISTORY_PROPERTY_KEYS as MAP_HISTORY_PROPERTY_KEYS
from .map_probe import MAP_PROBE_PROPERTY_KEYS as MAP_PROBE_PROPERTY_KEYS
from .map_probe import (
    build_cloud_property_summary as build_cloud_property_summary,
)
from .map_probe import build_map_probe_payload as build_map_probe_payload
from .models import (
    SUPPORTED_ACCOUNT_TYPES,
    DreameLawnMowerDescriptor,
    DreameLawnMowerFirmwareUpdateSupport,
    DreameLawnMowerMapSummary,
    DreameLawnMowerMapView,
    DreameLawnMowerRemoteControlSupport,
    DreameLawnMowerSnapshot,
    DreameLawnMowerStatusBlob,
    display_name_for_model,
)
from .models import descriptor_from_cloud_record as descriptor_from_cloud_record
from .models import (
    firmware_update_support_from_device as firmware_update_support_from_device,
)
from .models import map_diagnostics_from_device as map_diagnostics_from_device
from .models import map_summary_from_map_data as map_summary_from_map_data
from .models import remote_control_block_reason as remote_control_block_reason
from .models import remote_control_state_safe as remote_control_state_safe
from .models import snapshot_from_device as snapshot_from_device
from .mowing_preferences import (
    MOWING_PREFERENCE_MODE_FIELD as MOWING_PREFERENCE_MODE_FIELD,
)
from .mowing_preferences import (
    MOWING_PREFERENCE_PROPERTY_KEY as MOWING_PREFERENCE_PROPERTY_KEY,
)
from .mowing_preferences import (
    apply_mowing_preference_changes as apply_mowing_preference_changes,
)
from .mowing_preferences import (
    decode_mowing_preference_payload as decode_mowing_preference_payload,
)
from .mowing_preferences import (
    encode_mowing_preference_payload as encode_mowing_preference_payload,
)
from .mowing_preferences import (
    mowing_preference_mode_name as mowing_preference_mode_name,
)
from .mowing_preferences import (
    normalize_mowing_preference_mode as normalize_mowing_preference_mode,
)
from .mowing_preferences import (
    summarize_mowing_preference_info as summarize_mowing_preference_info,
)
from .mowing_tasks import MowingTaskResponseError as MowingTaskResponseError
from .mowing_tasks import build_edge_mowing_request as build_edge_mowing_request
from .mowing_tasks import build_spot_mowing_request as build_spot_mowing_request
from .mowing_tasks import build_zone_mowing_request as build_zone_mowing_request
from .mowing_tasks import (
    ensure_mowing_task_succeeded as ensure_mowing_task_succeeded,
)
from .payload_utils import _as_optional_text as _as_optional_text
from .payload_utils import _json_safe as _json_safe
from .payload_utils import _lower_enum_name as _lower_enum_name
from .point_cloud import (
    DEFAULT_POINT_CLOUD_MAX_BYTES,
    DreameLawnMowerPointCloudDownload,
    DreameLawnMowerPointCloudError,
)
from .point_cloud import parse_pcd_metadata as parse_pcd_metadata
from .runtime_state import RESUME_MOWING_REQUEST as RESUME_MOWING_REQUEST
from .runtime_state import (
    snapshot_session_control_state,
    snapshot_with_cloud_presence,
    snapshot_with_heartbeat_task_state,
)
from .schedule import EMPTY_SCHEDULE_VERSION as EMPTY_SCHEDULE_VERSION
from .schedule import (
    SCHEDULE_CHUNK_SIZE,
)
from .schedule import (
    build_schedule_enable_status_request as build_schedule_enable_status_request,
)
from .schedule import (
    build_schedule_upload_requests as build_schedule_upload_requests,
)
from .schedule import decode_schedule_payload_text as decode_schedule_payload_text
from .schedule import encode_schedule_payload_text as encode_schedule_payload_text
from .schedule import schedule_task_summary as schedule_task_summary
from .vector_map import parse_batch_vector_map as parse_batch_vector_map
from .vector_map import render_vector_map_png as render_vector_map_png
from .vector_map import vector_map_to_details as vector_map_to_details
from .vector_map import vector_map_to_summary as vector_map_to_summary

if _typing.TYPE_CHECKING:
    from .map_visuals import MapRenderStyle
    from .work_log import DreameLawnMowerWorkLogTotals

_CLOUD_PRESENCE_REFRESH_INTERVAL = _client_constants.CLOUD_PRESENCE_REFRESH_INTERVAL
_DEVICE_DISCONNECT_TIMEOUT_SECONDS = 2.0
_LOGGER = _logging.getLogger(__name__)

_app_action_data = _client_helpers._app_action_data
_app_map_area_label = _client_helpers._app_map_area_label
_app_map_coordinate_entries = _client_helpers._app_map_coordinate_entries
_app_map_coordinate_sets = _client_helpers._app_map_coordinate_sets
_app_map_entry_label = _client_helpers._app_map_entry_label
_app_map_entry_view_metadata = _client_helpers._app_map_entry_view_metadata
_app_map_label_font = _client_helpers._app_map_label_font
_app_map_objects_view_metadata = _client_helpers._app_map_objects_view_metadata
_app_map_payload_summary = _client_helpers._app_map_payload_summary
_app_map_points = _client_helpers._app_map_points
_app_map_polygon_center = _client_helpers._app_map_polygon_center
_app_map_view_details = _client_helpers._app_map_view_details
_app_map_view_summary = _client_helpers._app_map_view_summary
_app_maps_view_metadata = _client_helpers._app_maps_view_metadata
_app_object_extension = _client_helpers._app_object_extension
_as_optional_int = _client_helpers._as_optional_int
_batch_ota_keys = _client_helpers._batch_ota_keys
_batch_schedule_keys = _client_helpers._batch_schedule_keys
_batch_settings_keys = _client_helpers._batch_settings_keys
_coordinate_path_length_m = _client_helpers._coordinate_path_length_m
_debug_ota_model_name = _client_helpers._debug_ota_model_name
_dedupe_ints = _client_helpers._dedupe_ints
_device_list_records = _client_helpers._device_list_records
_download_point_cloud_content = _client_helpers._download_point_cloud_content
_draw_app_map_label = _client_helpers._draw_app_map_label
_epoch_to_iso = _client_helpers._epoch_to_iso
_firmware_description_parts = _client_helpers._firmware_description_parts
_firmware_description_text = _client_helpers._firmware_description_text
_HttpsOnlyPointCloudRedirectHandler = (
    _client_helpers._HttpsOnlyPointCloudRedirectHandler
)
_key_define_from_device_list_page = _client_helpers._key_define_from_device_list_page
_key_define_from_mapping = _client_helpers._key_define_from_mapping
_map_view_current_app_map_index = _client_helpers._map_view_current_app_map_index
_map_view_has_live_path = _client_helpers._map_view_has_live_path
_merge_error_text = _client_helpers._merge_error_text
_mowing_preference_map_overview = _client_helpers._mowing_preference_map_overview
_mowing_preference_overview = _client_helpers._mowing_preference_overview
_normalize_app_map_entries = _client_helpers._normalize_app_map_entries
_normalize_app_map_label_scale = _client_helpers._normalize_app_map_label_scale
_normalize_cloud_firmware_check = _client_helpers._normalize_cloud_firmware_check
_normalize_firmware_description_text = (
    _client_helpers._normalize_firmware_description_text
)
_normalize_voice_prompt_flags = _client_helpers._normalize_voice_prompt_flags
_open_point_cloud_response = _client_helpers._open_point_cloud_response
_operation_property_summary = _client_helpers._operation_property_summary
_operation_short_preview = _client_helpers._operation_short_preview
_operation_snapshot_summary = _client_helpers._operation_snapshot_summary
_operation_value_type = _client_helpers._operation_value_type
_parse_firmware_description = _client_helpers._parse_firmware_description
_point_cloud_action_data = _client_helpers._point_cloud_action_data
_point_cloud_download_url = _client_helpers._point_cloud_download_url
_point_cloud_object_name = _client_helpers._point_cloud_object_name
_positive_int = _client_helpers._positive_int
_property_entry_received_at = _client_helpers._property_entry_received_at
_rain_protect_end_time_timestamp = _client_helpers._rain_protect_end_time_timestamp
_render_app_map_payload_png = _client_helpers._render_app_map_payload_png
_runtime_blob_position = _client_helpers._runtime_blob_position
_schedule_entry_overview = _client_helpers._schedule_entry_overview
_schedule_plan_overview = _client_helpers._schedule_plan_overview
_schedule_upload_overview = _client_helpers._schedule_upload_overview
_schedule_week_tasks = _client_helpers._schedule_week_tasks
_select_app_map_payload = _client_helpers._select_app_map_payload
_set_point_cloud_response_timeout = _client_helpers._set_point_cloud_response_timeout
_setting_bool = _client_helpers._setting_bool
_sync_discover_devices = _client_helpers._sync_discover_devices
_validate_app_map_chunk_size = _client_helpers._validate_app_map_chunk_size
_validate_point_cloud_map_index = _client_helpers._validate_point_cloud_map_index
_validate_positive_number = _client_helpers._validate_positive_number
_validate_remote_control_step = _client_helpers._validate_remote_control_step
_voice_settings_summary = _client_helpers._voice_settings_summary
_weather_protection_active_summary = _client_helpers._weather_protection_active_summary
_weather_protection_summary = _client_helpers._weather_protection_summary
render_app_map_payload_png = _client_helpers.render_app_map_payload_png

_camera_stream_runtime_inputs_from_cloud_payload = (
    _client_camera._camera_stream_runtime_inputs_from_cloud_payload
)
_normalize_tx_p2p_info = _client_camera._normalize_tx_p2p_info

# The standalone ``dreame_lawn_mower_client.client`` wrapper republishes every
# non-private binding from this module. Preserve its historical camera exports
# while their implementation lives in the camera domain.
CAMERA_PROBE_PROPERTY_KEYS = _client_camera.CAMERA_PROBE_PROPERTY_KEYS
DreameLawnMowerCameraFeatureSupport = _client_camera.DreameLawnMowerCameraFeatureSupport
DreameLawnMowerCameraStreamRuntimeInputs = (
    _client_camera.DreameLawnMowerCameraStreamRuntimeInputs
)
build_camera_probe_payload = _client_camera.build_camera_probe_payload
build_operation_stage_diagnostics = _client_camera.build_operation_stage_diagnostics
camera_metadata_advertises_video = _client_camera.camera_metadata_advertises_video
camera_stream_block_reason = _client_camera.camera_stream_block_reason
derive_tx_video_app_credentials = _client_camera.derive_tx_video_app_credentials

_ZONE_TASK_CONFIRMATION_STATUSES = frozenset(
    {"zone_cleaning", "segment_cleaning"}
)
_EDGE_TASK_CONFIRMATION_STATUSES = frozenset(
    {"segment_cleaning", "zone_cleaning"}
)
_SPOT_TASK_CONFIRMATION_STATUSES = frozenset({"spot_cleaning"})


def _task_confirmation_key(snapshot: DreameLawnMowerSnapshot) -> tuple[Any, ...]:
    """Return state that must change when a new targeted task is accepted."""
    return (
        getattr(snapshot, "task_status", None),
        getattr(snapshot, "state", None),
        getattr(snapshot, "current_zone_id", None),
        getattr(snapshot, "active_segment_count", None),
        getattr(snapshot, "mowing_session_active", None),
    )


def _targeted_task_confirmed(
    snapshot: DreameLawnMowerSnapshot,
    baseline: DreameLawnMowerSnapshot | None,
    expected_statuses: frozenset[str],
    *,
    requested_zone_ids: frozenset[int] | None = None,
) -> bool:
    """Require requested task evidence and a transition from pre-command state."""
    if baseline is None:
        return False
    task_status = str(getattr(snapshot, "task_status", "") or "").lower()
    if task_status not in expected_statuses:
        return False
    if not (
        getattr(snapshot, "mowing_session_active", None) is True
        or getattr(snapshot, "started", False)
        or getattr(snapshot, "mowing", False)
    ):
        return False
    current_zone_id = getattr(snapshot, "current_zone_id", None)
    if (
        requested_zone_ids
        and current_zone_id is not None
        and int(current_zone_id) not in requested_zone_ids
    ):
        return False
    return _task_confirmation_key(snapshot) != _task_confirmation_key(baseline)


class DreameLawnMowerClient(
    _DreameLawnMowerCameraMixin,
    _DreameLawnMowerClientCoreMixin,
    _DreameLawnMowerClientDeviceSettingsMixin,
    _DreameLawnMowerClientSettingsMixin,
    _DreameLawnMowerClientMapsMixin,
):
    """Small async wrapper around the reverse-engineered mower protocol."""

    def __init__(
        self,
        *,
        username: str,
        password: str,
        country: str,
        account_type: str,
        descriptor: DreameLawnMowerDescriptor,
    ) -> None:
        if account_type not in SUPPORTED_ACCOUNT_TYPES:
            raise ValueError(f"Unsupported account type: {account_type}")

        self._username = username
        self._password = password
        self._country = country
        self._account_type = account_type
        self._descriptor = descriptor
        self._device: Any | None = None
        self._device_ownership_lock = _threading.Lock()
        self._closing = False
        self._update_callback: _typing.Callable[[], None] | None = None
        self._latest_snapshot: DreameLawnMowerSnapshot | None = None
        self._latest_runtime_status_blob: DreameLawnMowerStatusBlob | None = None
        self._runtime_live_track_segments: tuple[
            tuple[tuple[int, int], ...],
            ...,
        ] = ()
        self._last_runtime_track_blob_hex: str | None = None
        self._runtime_live_map_index: int | None = None
        self._runtime_live_task_id: int | None = None
        self._runtime_session_active: bool | None = None
        self._latest_cloud_device_info: Mapping[str, Any] | None = None
        self._cloud_device_info_refreshed_at = 0.0
        self._last_camera_stream_diagnostics: Mapping[str, Any] = {}
        self._app_map_object_cache_lock = _threading.Lock()
        self._point_cloud_generation_lock = _threading.Lock()
        self._latest_app_map_inventory_identity: str | None = None
        self._latest_app_map_object_inventory_identity: str | None = None
        self._latest_app_map_object_names: tuple[str | None, ...] = ()

    @property
    def descriptor(self) -> DreameLawnMowerDescriptor:
        """Return the selected mower descriptor."""
        return self._descriptor

    @property
    def device(self) -> Any | None:
        """Return the currently connected upstream device instance."""
        return self._device

    def set_update_callback(
        self,
        callback: _typing.Callable[[], None] | None,
    ) -> None:
        """Register a callback for cached device updates from MQTT or polling."""
        self._update_callback = callback
        if self._device is not None:
            self._device.listen(callback)

    def update_runtime_live_tracking(
        self,
        status_blob: DreameLawnMowerStatusBlob | None,
        *,
        active: bool,
        map_index: int | None = None,
    ) -> None:
        """Cache active-session runtime track history for live map overlays."""
        self._latest_runtime_status_blob = status_blob
        self._runtime_session_active = active
        if not active:
            self._runtime_live_track_segments = ()
            self._last_runtime_track_blob_hex = None
            self._runtime_live_map_index = None
            self._runtime_live_task_id = None
            return

        if status_blob is None:
            return

        task_id = getattr(status_blob, "candidate_runtime_task_id", None)
        context_changed = (
            self._runtime_live_map_index is not None
            and map_index is not None
            and self._runtime_live_map_index != map_index
        ) or (
            self._runtime_live_task_id is not None
            and task_id is not None
            and self._runtime_live_task_id != task_id
        )
        if context_changed:
            self._runtime_live_track_segments = ()
            self._last_runtime_track_blob_hex = None
        if map_index is not None:
            self._runtime_live_map_index = map_index
        if task_id is not None:
            self._runtime_live_task_id = task_id

        blob_hex = getattr(status_blob, "hex", None)
        if blob_hex and blob_hex == self._last_runtime_track_blob_hex:
            return

        segments = getattr(status_blob, "candidate_runtime_track_segments", ()) or ()
        if not segments:
            if blob_hex:
                self._last_runtime_track_blob_hex = blob_hex
            return

        self._runtime_live_track_segments = (
            *self._runtime_live_track_segments,
            *tuple(tuple(tuple(point) for point in segment) for segment in segments),
        )
        if len(self._runtime_live_track_segments) > 64:
            self._runtime_live_track_segments = self._runtime_live_track_segments[-64:]
        if blob_hex:
            self._last_runtime_track_blob_hex = blob_hex

    @classmethod
    async def async_discover_devices(
        cls,
        *,
        username: str,
        password: str,
        country: str,
        account_type: str,
    ) -> Sequence[DreameLawnMowerDescriptor]:
        """Log in and return mower devices from the user's account."""
        return await asyncio.to_thread(
            _sync_discover_devices,
            username,
            password,
            country,
            account_type,
        )

    async def async_refresh_authoritative_snapshot(
        self,
    ) -> DreameLawnMowerSnapshot:
        """Force a device-property read for a safety-critical decision."""
        return await self._async_refresh_authoritative_snapshot()

    async def async_refresh(self) -> DreameLawnMowerSnapshot:
        """Refresh device state and return a normalized snapshot."""
        device = await asyncio.to_thread(self._sync_update_device)
        info_raw = getattr(getattr(device, "info", None), "raw", {}) or {}
        device_info = info_raw.get("deviceInfo", {}) or {}
        refreshed_model = (
            getattr(getattr(device, "info", None), "model", None)
            or self._descriptor.model
        )
        self._descriptor = self._descriptor.__class__(
            did=self._descriptor.did,
            name=getattr(device, "name", None) or self._descriptor.name,
            model=refreshed_model,
            display_model=display_name_for_model(
                refreshed_model,
                fallback_name=device_info.get("displayName"),
            )
            or self._descriptor.display_model,
            account_type=self._descriptor.account_type,
            country=self._descriptor.country,
            host=getattr(device, "host", None) or self._descriptor.host,
            mac=getattr(device, "mac", None) or self._descriptor.mac,
            token=getattr(device, "token", None) or self._descriptor.token,
            raw=self._descriptor.raw,
        )
        snapshot = self._snapshot_from_device(device)
        try:
            status_blob = await asyncio.to_thread(
                self._sync_get_status_blob,
                False,
                True,
            )
        except DreameLawnMowerConnectionError:
            status_blob = None
        if status_blob is not None:
            snapshot = snapshot_with_heartbeat_task_state(snapshot, status_blob)

        try:
            cloud_device_info = await asyncio.to_thread(
                self._sync_get_cached_cloud_device_info,
            )
        except DreameLawnMowerConnectionError:
            cloud_device_info = self._latest_cloud_device_info
        if isinstance(cloud_device_info, Mapping):
            snapshot = snapshot_with_cloud_presence(snapshot, cloud_device_info)
        self._latest_snapshot = snapshot
        return snapshot

    async def async_start_mowing(self) -> bool | None:
        """Start mowing and report fresh, resumed, or unknown session identity."""
        try:
            status_blob = await self.async_get_status_blob(
                refresh=True,
                include_cloud=True,
            )
        except DreameLawnMowerConnectionError:
            status_blob = None
        if status_blob is not None and status_blob.task_resumable:
            try:
                await asyncio.to_thread(self._sync_resume_mowing)
            except _DreameLawnMowerCommandRejectedError:
                raise
            except DreameLawnMowerConnectionError as err:
                await self._async_reconcile_ambiguous_mutation(
                    "resume mowing",
                    err,
                    lambda snapshot: bool(
                        snapshot_session_control_state(snapshot) == "mowing"
                        and not getattr(snapshot, "task_resumable", False)
                    ),
                )
            return False
        if (
            status_blob is not None
            and status_blob.mowing_session_active is True
            and status_blob.task_status in {"starting", "mowing"}
            and await self._async_get_cached_start_mowing_session_identity() is False
        ):
            return False
        return await self._async_call_start_mowing_with_session_identity()

    async def async_pause(self) -> None:
        """Pause mowing."""
        await self._async_call_device_method("pause")

    async def async_dock(self) -> None:
        """End an active mowing session and return the mower to base."""
        try:
            snapshot = await self.async_refresh()
        except DreameLawnMowerConnectionError:
            await self._async_call_device_method("dock")
            return
        initial_state = snapshot_session_control_state(snapshot)

        async def async_refresh_state() -> str | None:
            return snapshot_session_control_state(await self.async_refresh())

        await async_stop_then_dock(
            initial_state=initial_state,
            stop=lambda: self._async_call_device_method("stop"),
            dock=lambda: self._async_call_device_method("dock"),
            refresh_state=async_refresh_state,
        )

    async def async_dock_without_stopping(self) -> None:
        """Return to base while preserving a resumable mowing session."""
        await self._async_call_device_method("dock")

    async def async_start_zone_mowing(self, zone_ids: Sequence[int]) -> Any:
        """Start mower-native zone mowing for explicit map area ids."""
        normalized_zone_ids = [int(zone_id) for zone_id in zone_ids]
        baseline = await self.async_get_cached_snapshot()
        try:
            return await asyncio.to_thread(
                self._sync_start_zone_mowing,
                normalized_zone_ids,
            )
        except _DreameLawnMowerCommandRejectedError:
            raise
        except DreameLawnMowerConnectionError as err:
            return await self._async_reconcile_ambiguous_mutation(
                "start zone mowing",
                err,
                lambda snapshot: _targeted_task_confirmed(
                    snapshot,
                    baseline,
                    _ZONE_TASK_CONFIRMATION_STATUSES,
                    requested_zone_ids=frozenset(normalized_zone_ids),
                ),
            )

    async def async_start_edge_mowing(
        self,
        contour_ids: Sequence[Sequence[int]],
    ) -> Any:
        """Start edge mowing for one or more contour id pairs."""
        normalized_contour_ids = [
            [int(value) for value in contour_id[:2]]
            for contour_id in contour_ids
        ]
        baseline = await self.async_get_cached_snapshot()
        try:
            return await asyncio.to_thread(
                self._sync_start_edge_mowing,
                normalized_contour_ids,
            )
        except _DreameLawnMowerCommandRejectedError:
            raise
        except DreameLawnMowerConnectionError as err:
            return await self._async_reconcile_ambiguous_mutation(
                "start edge mowing",
                err,
                lambda snapshot: _targeted_task_confirmed(
                    snapshot,
                    baseline,
                    _EDGE_TASK_CONFIRMATION_STATUSES,
                ),
            )

    async def async_start_spot_mowing(self, spot_ids: Sequence[int]) -> Any:
        """Start mower-native spot mowing for explicit saved spot area ids."""
        normalized_spot_ids = [int(spot_id) for spot_id in spot_ids]
        baseline = await self.async_get_cached_snapshot()
        try:
            return await asyncio.to_thread(
                self._sync_start_spot_mowing,
                normalized_spot_ids,
            )
        except _DreameLawnMowerCommandRejectedError:
            raise
        except DreameLawnMowerConnectionError as err:
            return await self._async_reconcile_ambiguous_mutation(
                "start spot mowing",
                err,
                lambda snapshot: _targeted_task_confirmed(
                    snapshot,
                    baseline,
                    _SPOT_TASK_CONFIRMATION_STATUSES,
                ),
            )

    async def async_go_to_maintenance_point(self, point_id: int) -> Any:
        """Drive to one configured map maintenance point."""
        return await asyncio.to_thread(
            self._sync_go_to_maintenance_point,
            int(point_id),
        )

    async def async_switch_current_map(self, map_index: int) -> Any:
        """Switch the active map only while idle and require map-list readback."""
        map_index = int(map_index)
        snapshot = await self.async_refresh_authoritative_snapshot()
        session_unknown_outside_safe_state = (
            snapshot.mowing_session_active is None
            and snapshot.activity not in {"docked", "idle"}
        )
        if (
            snapshot.mowing_session_active is True
            or session_unknown_outside_safe_state
            or snapshot.mowing
            or snapshot.paused
            or snapshot.returning
        ):
            raise _DreameLawnMowerCommandRejectedError(
                "The active map cannot be changed while a mowing task is active, "
                "paused, or returning to the dock. Finish or cancel the task first."
            )
        try:
            response = await asyncio.to_thread(self._sync_switch_current_map, map_index)
        except _DreameLawnMowerCommandRejectedError:
            raise
        except DreameLawnMowerConnectionError:
            # A timed-out setter can still have reached the mower. The same
            # mandatory readback below decides whether it took effect.
            response = None

        readable = False
        for delay in (0.0, 0.75, 1.5, 3.0):
            if delay:
                await asyncio.sleep(delay)
            try:
                current_map_index = await self.async_get_current_app_map_index()
            except DreameLawnMowerConnectionError:
                continue
            readable = True
            if current_map_index == map_index:
                return response

        if readable:
            raise _DreameLawnMowerCommandRejectedError(
                "The mower acknowledged the map switch but stayed on its previous "
                "map. Map switching is only supported while no task is active."
            )
        raise DreameLawnMowerConnectionError(
            "The active map could not be confirmed because every map-list "
            "readback failed. Refresh the mower before trying again."
        )

    async def async_get_vector_map_details(self) -> dict[str, Any]:
        """Return JSON-safe parsed batch vector-map details."""
        return await asyncio.to_thread(self._sync_get_vector_map_details)

    async def async_get_remote_control_support(
        self,
        *,
        refresh: bool = False,
    ) -> DreameLawnMowerRemoteControlSupport:
        """Return whether the mower currently exposes remote-control support."""
        return await asyncio.to_thread(self._sync_get_remote_control_support, refresh)

    async def async_remote_control_move_step(
        self,
        *,
        rotation: int = 0,
        velocity: int = 0,
        prompt: bool | None = None,
    ) -> Any:
        """Send one remote-control movement step.

        This can physically move the mower, so Home Assistant controls should be
        added only after the command shape is validated on real hardware.
        """
        _validate_remote_control_step(rotation=rotation, velocity=velocity)
        return await asyncio.to_thread(
            self._sync_remote_control_move_step,
            rotation,
            velocity,
            prompt,
        )

    async def async_remote_control_stop(self) -> Any:
        """Send a remote-control stop step."""
        return await self.async_remote_control_move_step(
            rotation=0,
            velocity=0,
            prompt=False,
        )

    async def async_get_firmware_update_support(
        self,
        *,
        refresh: bool = False,
        include_cloud: bool = True,
        include_debug_ota_catalog: bool = False,
        language: str | None = "en",
    ) -> DreameLawnMowerFirmwareUpdateSupport:
        """Return firmware/update evidence without guessing availability."""
        return await asyncio.to_thread(
            self._sync_get_firmware_update_support,
            refresh,
            include_cloud,
            include_debug_ota_catalog,
            language,
        )

    async def async_get_status_blob(
        self,
        *,
        refresh: bool = False,
        include_cloud: bool = True,
    ) -> DreameLawnMowerStatusBlob | None:
        """Return the latest decoded raw realtime status blob, if available."""
        return await asyncio.to_thread(
            self._sync_get_status_blob,
            refresh,
            include_cloud,
        )

    async def async_get_runtime_status_blob(
        self,
        *,
        refresh: bool = False,
        include_cloud: bool = True,
    ) -> DreameLawnMowerStatusBlob | None:
        """Return the latest decoded runtime-status blob, if available."""
        return await asyncio.to_thread(
            self._sync_get_runtime_status_blob,
            refresh,
            include_cloud,
        )

    async def async_get_bluetooth_connected(
        self,
        *,
        refresh: bool = False,
        include_cloud: bool = True,
    ) -> bool | None:
        """Return whether the mower reports an active Bluetooth connection."""
        return await asyncio.to_thread(
            self._sync_get_bluetooth_connected,
            refresh,
            include_cloud,
        )

    async def async_capture_operation_snapshot(
        self,
        *,
        label: str | None = None,
        include_status_blob: bool = True,
        include_cloud_status_blob: bool = True,
        include_remote_control: bool = True,
        include_map_view: bool = False,
        include_firmware: bool = False,
        map_timeout: float = 6.0,
        map_interval: float = 0.5,
        language: str | None = "en",
    ) -> dict[str, Any]:
        """Capture a JSON-safe operational snapshot for supervised field tests.

        The snapshot is read-only. It refreshes mower state once, then optionally
        adds decoded realtime status, remote-control support, map-view
        diagnostics, and firmware/update evidence. It never starts mowing,
        remote control, camera streaming, or docking.
        """
        return await asyncio.to_thread(
            self._sync_capture_operation_snapshot,
            label,
            include_status_blob,
            include_cloud_status_blob,
            include_remote_control,
            include_map_view,
            include_firmware,
            map_timeout,
            map_interval,
            language,
        )

    async def async_refresh_map_summary(
        self,
        *,
        timeout: float = 8.0,
        interval: float = 0.5,
    ) -> DreameLawnMowerMapSummary | None:
        """Try to refresh map data and return a normalized summary."""
        view = await self.async_refresh_map_view(timeout=timeout, interval=interval)
        return view.summary

    async def async_get_map_png(
        self,
        *,
        timeout: float = 8.0,
        interval: float = 0.5,
        label_scale: float = 1.0,
        style: MapRenderStyle | None = None,
    ) -> bytes | None:
        """Try to refresh the current mower map and return a rendered PNG."""
        view = await self.async_refresh_map_view(
            timeout=timeout,
            interval=interval,
            label_scale=label_scale,
            style=style,
        )
        return view.image_png

    async def async_refresh_map_view(
        self,
        *,
        timeout: float = 8.0,
        interval: float = 0.5,
        label_scale: float = 1.0,
        style: MapRenderStyle | None = None,
    ) -> DreameLawnMowerMapView:
        """Try to refresh map data and return metadata plus rendered image bytes."""
        return await asyncio.to_thread(
            self._sync_refresh_map_view,
            timeout,
            interval,
            label_scale,
            style,
        )

    async def async_refresh_vector_map_view(
        self,
        *,
        label_scale: float = 1.0,
        current_map_index: int | None = None,
        style: MapRenderStyle | None = None,
    ) -> DreameLawnMowerMapView:
        """Refresh the batch/vector map path used for live mowing overlays."""
        return await asyncio.to_thread(
            self._sync_refresh_vector_map_view,
            label_scale=label_scale,
            current_map_index=current_map_index,
            style=style,
        )

    async def async_get_app_schedules(
        self,
        *,
        include_raw: bool = False,
        map_indices: Sequence[int] | None = None,
        chunk_size: int = SCHEDULE_CHUNK_SIZE,
        include_current_task: bool = True,
    ) -> dict[str, Any]:
        """Return read-only mower schedules from the app action protocol."""
        return await asyncio.to_thread(
            self._sync_get_app_schedules,
            include_raw,
            map_indices,
            chunk_size,
            include_current_task,
        )

    async def async_set_app_schedule_plan_enabled(
        self,
        *,
        map_index: int,
        plan_id: int,
        enabled: bool,
        execute: bool = False,
        confirm_write: bool = False,
    ) -> dict[str, Any]:
        """Build or execute the app action request to toggle a schedule plan."""
        return await asyncio.to_thread(
            self._sync_set_app_schedule_plan_enabled,
            map_index,
            plan_id,
            enabled,
            execute,
            confirm_write,
        )

    async def async_plan_app_schedule_upload(
        self,
        *,
        map_index: int,
        plans: Sequence[Mapping[str, Any]],
        execute: bool = False,
        confirm_write: bool = False,
        chunk_size: int = SCHEDULE_CHUNK_SIZE,
    ) -> dict[str, Any]:
        """Build or execute a full schedule upload from readable plans."""
        return await asyncio.to_thread(
            self._sync_plan_app_schedule_upload,
            map_index,
            plans,
            execute,
            confirm_write,
            chunk_size,
        )

    async def async_get_mowing_preferences(
        self,
        *,
        include_raw: bool = False,
        map_indices: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """Return read-only mower preference settings from app actions."""
        return await asyncio.to_thread(
            self._sync_get_mowing_preferences,
            include_raw,
            map_indices,
        )

    async def async_plan_app_mowing_preference_update(
        self,
        *,
        map_index: int,
        area_id: int | None,
        changes: Mapping[str, Any],
        execute: bool = False,
        confirm_write: bool = False,
    ) -> dict[str, Any]:
        """Build or execute a mower preference update from the current app state."""
        return await asyncio.to_thread(
            self._sync_plan_app_mowing_preference_update,
            map_index,
            area_id,
            changes,
            execute,
            confirm_write,
        )

    async def async_get_weather_protection(
        self,
        *,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Return read-only weather/rain protection settings from app actions."""
        return await asyncio.to_thread(
            self._sync_get_weather_protection,
            include_raw,
        )

    async def async_get_work_log_totals(self) -> DreameLawnMowerWorkLogTotals:
        """Return mower-owned lifetime area, time, and session totals."""
        return await asyncio.to_thread(self._sync_get_work_log_totals)

    async def async_get_maintenance_status(
        self,
        *,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Return read-only CMS maintenance counter state from app actions."""
        return await asyncio.to_thread(
            self._sync_get_maintenance_status,
            include_raw,
        )

    async def async_plan_maintenance_reset(
        self,
        *,
        item: str,
        execute: bool = False,
        confirm_write: bool = False,
    ) -> dict[str, Any]:
        """Build or execute a guarded CMS maintenance counter reset."""
        return await asyncio.to_thread(
            self._sync_plan_maintenance_reset,
            item,
            execute,
            confirm_write,
        )

    async def async_get_voice_settings(
        self,
        *,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Return read-only voice and language settings from app actions."""
        return await asyncio.to_thread(
            self._sync_get_voice_settings,
            include_raw,
        )

    async def async_set_voice_language(self, voice_language: int) -> dict[str, Any]:
        """Set the mower voice language by app language-pack index."""
        return await asyncio.to_thread(
            self._sync_set_voice_language,
            int(voice_language),
        )

    async def async_set_voice_volume(self, volume: int) -> dict[str, Any]:
        """Set the mower voice volume from 0 to 100."""
        return await asyncio.to_thread(
            self._sync_set_voice_volume,
            int(volume),
        )

    async def async_set_voice_prompts(
        self,
        prompts: Sequence[int | bool],
    ) -> dict[str, Any]:
        """Set the four mower voice prompt toggles."""
        normalized = _normalize_voice_prompt_flags(prompts)
        return await asyncio.to_thread(
            self._sync_set_voice_prompts,
            normalized,
        )

    async def async_get_cloud_device_info(
        self,
        *,
        language: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch the raw cloud `device/info` payload used by the mobile app."""
        return await asyncio.to_thread(self._sync_get_cloud_device_info, language)

    async def async_get_cloud_user_features(
        self,
        *,
        language: str | None = None,
    ) -> Any:
        """Fetch raw cloud feature/permit data from the mobile app endpoint."""
        return await asyncio.to_thread(self._sync_get_cloud_user_features, language)

    async def async_get_cloud_device_otc_info(
        self,
        *,
        language: str | None = None,
    ) -> Any:
        """Fetch read-only cloud OTC metadata from the mobile app endpoint."""
        return await asyncio.to_thread(self._sync_get_cloud_device_otc_info, language)

    async def async_get_cloud_firmware_check(
        self,
        *,
        language: str | None = None,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Fetch the app-approved mower firmware check payload."""
        return await asyncio.to_thread(
            self._sync_get_cloud_firmware_check,
            language,
            include_raw,
        )

    async def async_approve_firmware_update(
        self,
        *,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Trigger the cloud firmware approval step used by the mobile app."""
        return await asyncio.to_thread(self._sync_approve_firmware_update, language)

    async def async_get_app_plugin_version(
        self,
        *,
        app_version_code: int = 2050300,
        os: int = 1,
    ) -> Any:
        """Fetch read-only mobile plugin metadata for this mower model."""
        return await asyncio.to_thread(
            self._sync_get_app_plugin_version,
            app_version_code,
            os,
        )

    async def async_get_app_maps(
        self,
        *,
        chunk_size: int = 400,
        include_payload: bool = False,
        include_objects: bool = True,
        include_object_urls: bool = False,
    ) -> dict[str, Any]:
        """Fetch mower-native app map payloads through read-only app commands."""
        return await asyncio.to_thread(
            self._sync_get_app_maps,
            chunk_size,
            include_payload,
            include_objects,
            include_object_urls,
        )

    async def async_get_current_app_map_index(self) -> int | None:
        """Read the active map index without downloading map payloads."""
        return await asyncio.to_thread(
            self._sync_get_current_app_map_index_readback,
        )

    async def async_get_batch_schedules(
        self,
        *,
        include_raw: bool = False,
        map_index_hint: int | None = None,
        discover_map_index: bool = True,
        timeout: float = _SCHEDULE_READ_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Fetch and decode schedule data from batch device data."""
        timeout = _validate_positive_number(timeout, "batch schedule timeout")
        return await asyncio.to_thread(
            self._sync_get_batch_schedules,
            include_raw,
            map_index_hint,
            discover_map_index,
            timeout,
        )

    async def async_get_batch_mowing_preferences(
        self,
        *,
        include_raw: bool = False,
        map_indices: Sequence[int] | None = None,
        map_index_hints: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """Fetch and decode mower preferences from batch device data."""
        return await asyncio.to_thread(
            self._sync_get_batch_mowing_preferences,
            include_raw,
            map_indices,
            map_index_hints,
        )

    async def async_get_batch_ota_info(
        self,
        *,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Fetch and decode OTA state from batch device data."""
        return await asyncio.to_thread(self._sync_get_batch_ota_info, include_raw)

    async def async_get_debug_ota_catalog(
        self,
        *,
        model_name: str | None = None,
        current_version: str | None = None,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Fetch the public debug/manual OTA catalog for the mower model."""
        return await asyncio.to_thread(
            self._sync_get_debug_ota_catalog,
            model_name,
            current_version,
            include_raw,
        )

    async def async_get_app_map_objects(
        self,
        *,
        include_urls: bool = False,
    ) -> dict[str, Any]:
        """Fetch read-only 3D map object metadata from the app command path."""
        return await asyncio.to_thread(
            self._sync_get_app_map_objects,
            include_urls,
        )

    async def async_download_app_map_point_cloud(
        self,
        *,
        map_index: int = 0,
        allow_stored: bool = False,
        timeout: float = 45.0,
        poll_interval: float = 2.0,
        download_timeout: float = 60.0,
        max_bytes: int = DEFAULT_POINT_CLOUD_MAX_BYTES,
    ) -> DreameLawnMowerPointCloudDownload:
        """Download a stored or freshly generated mower app-map point cloud."""
        timeout = _validate_positive_number(timeout, "generation timeout")
        preflight_timeout = (
            _POINT_CLOUD_STORED_PREFLIGHT_BUDGET_SECONDS
            if allow_stored
            else _POINT_CLOUD_GENERATION_PREFLIGHT_BUDGET_SECONDS
        )
        operation_timeout = (
            timeout
            + _POINT_CLOUD_CLOUD_SETUP_TIMEOUT_SECONDS
            + preflight_timeout
        )
        deadline = time.monotonic() + operation_timeout
        abandoned = _threading.Event()
        try:
            async with asyncio.timeout(operation_timeout):
                worker = asyncio.create_task(
                    asyncio.to_thread(
                        self._sync_download_app_map_point_cloud_singleflight,
                        map_index,
                        timeout,
                        poll_interval,
                        download_timeout,
                        max_bytes,
                        deadline,
                        allow_stored,
                        abandoned,
                    )
                )
                worker.add_done_callback(
                    lambda completed: (
                        completed.exception() if not completed.cancelled() else None
                    )
                )
                return await asyncio.shield(worker)
        except (TimeoutError, _RequestsTimeout) as err:
            abandoned.set()
            raise DreameLawnMowerPointCloudError(
                "Point-cloud generation timed out.",
                code="point_cloud_timeout",
                stage="generation",
                public_message=(
                    f"The mower did not finish the 3D map request within "
                    f"{timeout:g} seconds."
                ),
                timeout_seconds=timeout,
                retry_after_seconds=10,
            ) from err
        except asyncio.CancelledError:
            abandoned.set()
            raise

    def _sync_download_app_map_point_cloud_singleflight(
        self,
        map_index: int,
        timeout: float,
        poll_interval: float,
        download_timeout: float,
        max_bytes: int,
        deadline: float,
        allow_stored: bool,
        abandoned: _threading.Event,
    ) -> DreameLawnMowerPointCloudDownload:
        """Run one mower-wide generation while retaining ownership after cancel."""
        if abandoned.is_set() or time.monotonic() >= deadline:
            raise DreameLawnMowerPointCloudError(
                "Point-cloud request ended before generation started.",
                code="point_cloud_timeout",
                stage="queue",
                public_message="The mower point-cloud request timed out in the queue.",
                timeout_seconds=timeout,
                retry_after_seconds=2,
            )
        if not self._point_cloud_generation_lock.acquire(blocking=False):
            raise DreameLawnMowerPointCloudError(
                "Another point-cloud generation is already in progress.",
                code="point_cloud_generation_in_progress",
                stage="queue",
                public_message="A 3D map is already being generated for this mower.",
                retry_after_seconds=5,
            )
        try:
            if abandoned.is_set():
                raise DreameLawnMowerPointCloudError(
                    "Point-cloud request ended before generation started.",
                    code="point_cloud_timeout",
                    stage="queue",
                    public_message=(
                        "The mower point-cloud request timed out in the queue."
                    ),
                    timeout_seconds=timeout,
                    retry_after_seconds=2,
                )
            return self._sync_download_app_map_point_cloud(
                map_index,
                timeout,
                poll_interval,
                download_timeout,
                max_bytes,
                deadline,
                allow_stored,
            )
        finally:
            self._point_cloud_generation_lock.release()

    async def async_get_cloud_properties(
        self,
        keys: str | Sequence[str],
    ) -> Any:
        """Fetch raw cloud property values from the `iotstatus/props` endpoint."""
        return await asyncio.to_thread(self._sync_get_cloud_properties, keys)

    async def async_scan_cloud_properties(
        self,
        *,
        keys: str | Sequence[str] | None = None,
        siids: Sequence[int] | None = None,
        piid_start: int = 1,
        piid_end: int = 25,
        chunk_size: int = 50,
        language: str = "en",
        only_values: bool = True,
        include_key_definition: bool = True,
    ) -> dict[str, Any]:
        """Scan cloud properties in chunks and return normalized results."""
        return await asyncio.to_thread(
            self._sync_scan_cloud_properties,
            keys,
            siids,
            piid_start,
            piid_end,
            chunk_size,
            language,
            only_values,
            include_key_definition,
            None,
        )

    async def async_get_cloud_device_list_page(
        self,
        *,
        current: int = 1,
        size: int = 20,
        language: str | None = "en",
        master: bool | None = None,
        shared_status: int | None = None,
    ) -> dict[str, Any] | None:
        """Fetch the raw cloud `device/listV2` page used by the mobile app."""
        return await asyncio.to_thread(
            self._sync_get_cloud_device_list_page,
            current,
            size,
            language,
            master,
            shared_status,
        )

    async def async_get_cloud_key_definition(
        self,
        *,
        language: str | None = "en",
    ) -> dict[str, Any]:
        """Fetch the public device status translation JSON advertised by cloud."""
        return await asyncio.to_thread(
            self._sync_get_cloud_key_definition,
            language,
        )

    async def async_probe_map_sources(
        self,
        *,
        timeout: float = 6.0,
        interval: float = 0.5,
        language: str = "en",
    ) -> dict[str, Any]:
        """Probe known read-only map sources and return a JSON-safe payload."""
        return await asyncio.to_thread(
            self._sync_probe_map_sources,
            timeout,
            interval,
            language,
        )

    async def async_close(self) -> None:
        """Disconnect long-lived device resources."""
        with self._device_ownership_lock:
            self._closing = True
            device = self._device
            self._device = None
        if device is not None:
            try:
                device.listen(None)
                await self._async_disconnect_device(device)
            except asyncio.CancelledError:
                raise
            except Exception:
                with self._device_ownership_lock:
                    if self._device is None:
                        self._device = device
                raise

    async def _async_disconnect_device(self, device: Any) -> None:
        """Bound device disconnect without retaining HA's default executor."""
        loop = asyncio.get_running_loop()
        completed: asyncio.Future[None] = loop.create_future()

        def settle(error: Exception | None) -> None:
            if completed.done():
                return
            if error is None:
                completed.set_result(None)
            else:
                completed.set_exception(error)

        def disconnect() -> None:
            error: Exception | None = None
            try:
                device.disconnect()
            except Exception as err:  # noqa: BLE001 - return on the event loop
                error = err
            try:
                loop.call_soon_threadsafe(settle, error)
            except RuntimeError:
                pass

        worker = _threading.Thread(
            target=disconnect,
            name="dreame-device-disconnect",
            daemon=True,
        )
        worker.start()
        try:
            async with asyncio.timeout(_DEVICE_DISCONNECT_TIMEOUT_SECONDS):
                await completed
        except TimeoutError:
            completed.cancel()
            _LOGGER.warning(
                "Device disconnect did not finish within %.1f seconds; "
                "continuing shutdown while its daemon worker drains.",
                _DEVICE_DISCONNECT_TIMEOUT_SECONDS,
            )
