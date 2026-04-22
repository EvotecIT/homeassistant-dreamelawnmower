"""Async-friendly reusable mower client."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from .app_protocol import (
    MOWER_BLUETOOTH_PROPERTY_KEY,
    MOWER_ERROR_PROPERTY_KEY,
    MOWER_PROPERTY_HINTS,
    MOWER_RAW_STATUS_PROPERTY_KEY,
    MOWER_RUNTIME_STATUS_PROPERTY_KEY,
    MOWER_STATE_PROPERTY_KEY,
    MOWER_TASK_PROPERTY_KEY,
    decode_mower_status_blob,
    decode_mower_task_status,
    key_definition_label,
    mower_error_label,
    mower_property_hint,
    mower_realtime_property_name,
    mower_state_key,
    mower_state_label,
)
from .batch_device_data import (
    decode_batch_mowing_preferences,
    decode_batch_ota_info,
    decode_batch_schedule_payload,
)
from .camera_probe import CAMERA_PROBE_PROPERTY_KEYS, build_camera_probe_payload
from .debug_ota_catalog import (
    build_debug_ota_catalog_url,
    normalize_debug_ota_catalog_payload,
)
from .exceptions import DeviceException, InvalidActionException
from .map_probe import (
    MAP_HISTORY_PROPERTY_KEYS,
    MAP_PROBE_PROPERTY_KEYS,
    build_cloud_property_summary,
    build_map_probe_payload,
)
from .models import (
    SUPPORTED_ACCOUNT_TYPES,
    DreameLawnMowerCameraFeatureSupport,
    DreameLawnMowerDescriptor,
    DreameLawnMowerFirmwareUpdateSupport,
    DreameLawnMowerMapSummary,
    DreameLawnMowerMapView,
    DreameLawnMowerRemoteControlSupport,
    DreameLawnMowerSnapshot,
    DreameLawnMowerStatusBlob,
    descriptor_from_cloud_record,
    display_name_for_model,
    firmware_update_support_from_device,
    map_diagnostics_from_device,
    map_summary_from_map_data,
    remote_control_block_reason,
    remote_control_state_safe,
    snapshot_from_device,
)
from .mowing_preferences import (
    MOWING_PREFERENCE_MODE_FIELD,
    MOWING_PREFERENCE_PROPERTY_KEY,
    apply_mowing_preference_changes,
    decode_mowing_preference_payload,
    encode_mowing_preference_payload,
    mowing_preference_mode_name,
    normalize_mowing_preference_mode,
    summarize_mowing_preference_info,
)
from .schedule import (
    EMPTY_SCHEDULE_VERSION,
    SCHEDULE_CHUNK_SIZE,
    build_schedule_enable_status_request,
    build_schedule_upload_requests,
    decode_schedule_payload_text,
    encode_schedule_payload_text,
    schedule_task_summary,
)
from .vector_map import (
    parse_batch_vector_map,
    render_vector_map_png,
    vector_map_to_details,
    vector_map_to_summary,
)

REMOTE_CONTROL_MAX_ROTATION = 1000
REMOTE_CONTROL_MAX_VELOCITY = 1000
VOICE_LANGUAGE_CODES = (
    "en",
    "cn",
    "de",
    "fr",
    "it",
    "es",
    "pt",
    "no",
    "sv",
    "da",
    "fi",
    "nl",
    "tr",
    "pl",
    "ru",
    "lt",
)
VOICE_LANGUAGE_LABELS = (
    "English",
    "Chinese",
    "German",
    "French",
    "Italian",
    "Spanish",
    "Portuguese",
    "Norwegian",
    "Swedish",
    "Danish",
    "Finnish",
    "Dutch",
    "Turkish",
    "Polish",
    "Russian",
    "Lithuanian",
)
VOICE_LANGUAGE_INDEX_TO_LABEL = {
    index: label for index, label in enumerate(VOICE_LANGUAGE_LABELS)
}
VOICE_LANGUAGE_INDEX_TO_CODE = {
    index: code for index, code in enumerate(VOICE_LANGUAGE_CODES)
}
VOICE_LANGUAGE_LABEL_TO_INDEX = {
    label: index for index, label in enumerate(VOICE_LANGUAGE_LABELS)
}
VOICE_PROMPT_FIELDS = (
    "general_prompt_voice_enabled",
    "working_voice_enabled",
    "special_status_voice_enabled",
    "fault_voice_enabled",
)


class DreameLawnMowerError(Exception):
    """Base reusable client exception."""


class DreameLawnMowerAuthError(DreameLawnMowerError):
    """Login or credential failure."""


class DreameLawnMowerConnectionError(DreameLawnMowerError):
    """Connection or update failure."""


class DreameLawnMowerTwoFactorRequiredError(DreameLawnMowerAuthError):
    """Two-factor authentication is required."""

    def __init__(self, url: str) -> None:
        super().__init__("Two-factor authentication is required.")
        self.url = url


class DreameLawnMowerClient:
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
        self._latest_runtime_status_blob: DreameLawnMowerStatusBlob | None = None
        self._runtime_live_track_segments: tuple[
            tuple[tuple[int, int], ...],
            ...,
        ] = ()
        self._last_runtime_track_blob_hex: str | None = None

    @property
    def descriptor(self) -> DreameLawnMowerDescriptor:
        """Return the selected mower descriptor."""
        return self._descriptor

    @property
    def device(self) -> Any | None:
        """Return the currently connected upstream device instance."""
        return self._device

    def update_runtime_live_tracking(
        self,
        status_blob: DreameLawnMowerStatusBlob | None,
        *,
        active: bool,
    ) -> None:
        """Cache active-session runtime track history for live map overlays."""
        self._latest_runtime_status_blob = status_blob
        if not active:
            self._runtime_live_track_segments = ()
            self._last_runtime_track_blob_hex = None
            return

        if status_blob is None:
            return

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
        return snapshot_from_device(self._descriptor, device)

    async def async_start_mowing(self) -> None:
        """Start or resume mowing."""
        await self._async_call_device_method("start_mowing")

    async def async_pause(self) -> None:
        """Pause mowing."""
        await self._async_call_device_method("pause")

    async def async_dock(self) -> None:
        """Return the mower to base."""
        await self._async_call_device_method("dock")

    async def async_clean_segments(self, segment_ids: Sequence[int]) -> Any:
        """Start segment or zone mowing for explicit map area ids."""
        return await asyncio.to_thread(self._sync_clean_segments, list(segment_ids))

    async def async_start_edge_mowing(
        self,
        contour_ids: Sequence[Sequence[int]],
    ) -> Any:
        """Start edge mowing for one or more contour id pairs."""
        normalized = [
            [int(contour_id[0]), int(contour_id[1])]
            for contour_id in contour_ids
            if len(contour_id) >= 2
        ]
        return await asyncio.to_thread(self._sync_start_edge_mowing, normalized)

    async def async_clean_spots(
        self,
        points: Sequence[tuple[int, int] | list[int]],
    ) -> Any:
        """Start spot mowing for one or more center points."""
        normalized = [
            [int(point[0]), int(point[1])]
            for point in points
            if len(point) >= 2
        ]
        return await asyncio.to_thread(self._sync_clean_spots, normalized)

    async def async_switch_current_map(self, map_index: int) -> Any:
        """Switch the active mower map through the app task path."""
        return await asyncio.to_thread(self._sync_switch_current_map, int(map_index))

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

    async def async_get_camera_feature_support(
        self,
        *,
        refresh: bool = False,
        include_cloud: bool = True,
        language: str | None = "en",
    ) -> DreameLawnMowerCameraFeatureSupport:
        """Return read-only camera/photo feature discovery details.

        This only inspects cached protocol mappings, device metadata, and safe
        cloud feature endpoints. It does not start a stream or take a photo.
        """
        return await asyncio.to_thread(
            self._sync_get_camera_feature_support,
            refresh,
            include_cloud,
            language,
        )

    async def async_get_firmware_update_support(
        self,
        *,
        refresh: bool = False,
        include_cloud: bool = True,
        language: str | None = "en",
    ) -> DreameLawnMowerFirmwareUpdateSupport:
        """Return firmware/update evidence without guessing availability."""
        return await asyncio.to_thread(
            self._sync_get_firmware_update_support,
            refresh,
            include_cloud,
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

    async def async_request_photo_info(
        self,
        parameters: Any = None,
    ) -> Any:
        """Request mower photo metadata through the app protocol.

        This is an active cloud/device action, but it does not start video
        streaming, audio, remote control, or mowing.
        """
        return await asyncio.to_thread(self._sync_request_photo_info, parameters)

    async def async_probe_camera_sources(
        self,
        *,
        language: str = "en",
        request_device_properties: bool = True,
    ) -> dict[str, Any]:
        """Probe known camera/photo sources without starting stream actions."""
        return await asyncio.to_thread(
            self._sync_probe_camera_sources,
            language,
            request_device_properties,
        )

    async def async_probe_camera_stream_handshake(
        self,
        *,
        timeout: float = 6.0,
        interval: float = 0.75,
        operation: str = "monitor",
        payload_mode: str = "with_session",
    ) -> dict[str, Any]:
        """Try the camera stream start/end handshake and return debug details.

        This can start a short camera streaming session. It does not start
        audio, remote control, or mowing, and it always attempts an end call.
        """
        return await asyncio.to_thread(
            self._sync_probe_camera_stream_handshake,
            timeout,
            interval,
            operation,
            payload_mode,
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
    ) -> bytes | None:
        """Try to refresh the current mower map and return a rendered PNG."""
        view = await self.async_refresh_map_view(timeout=timeout, interval=interval)
        return view.image_png

    async def async_refresh_map_view(
        self,
        *,
        timeout: float = 8.0,
        interval: float = 0.5,
    ) -> DreameLawnMowerMapView:
        """Try to refresh map data and return metadata plus rendered image bytes."""
        return await asyncio.to_thread(
            self._sync_refresh_map_view,
            timeout,
            interval,
        )

    async def async_refresh_vector_map_view(self) -> DreameLawnMowerMapView:
        """Refresh the batch/vector map path used for live mowing overlays."""
        return await asyncio.to_thread(self._sync_refresh_vector_map_view)

    async def async_get_app_schedules(
        self,
        *,
        include_raw: bool = False,
        map_indices: Sequence[int] | None = None,
        chunk_size: int = SCHEDULE_CHUNK_SIZE,
    ) -> dict[str, Any]:
        """Return read-only mower schedules from the app action protocol."""
        return await asyncio.to_thread(
            self._sync_get_app_schedules,
            include_raw,
            map_indices,
            chunk_size,
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

    async def async_get_batch_schedules(
        self,
        *,
        include_raw: bool = False,
        map_index_hint: int | None = None,
    ) -> dict[str, Any]:
        """Fetch and decode schedule data from batch device data."""
        return await asyncio.to_thread(
            self._sync_get_batch_schedules,
            include_raw,
            map_index_hint,
        )

    async def async_get_batch_mowing_preferences(
        self,
        *,
        include_raw: bool = False,
        map_indices: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """Fetch and decode mower preferences from batch device data."""
        return await asyncio.to_thread(
            self._sync_get_batch_mowing_preferences,
            include_raw,
            map_indices,
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
        if self._device is not None:
            await asyncio.to_thread(self._device.disconnect)
            self._device = None

    async def _async_call_device_method(self, method_name: str) -> Any:
        device = await asyncio.to_thread(self._ensure_device)
        method = getattr(device, method_name)
        try:
            return await asyncio.to_thread(method)
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_update_device(self):
        device = self._ensure_device()
        try:
            device.update()
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err
        return device

    def _sync_get_remote_control_support(
        self,
        refresh: bool = False,
    ) -> DreameLawnMowerRemoteControlSupport:
        if refresh:
            device = self._sync_update_device()
        else:
            device = self._ensure_device()

        try:
            from .types import DreameMowerProperty, DreameMowerStatus
        except ImportError:
            return DreameLawnMowerRemoteControlSupport(
                supported=False,
                reason="Remote-control protocol types are unavailable.",
            )

        mapping = getattr(device, "property_mapping", {}).get(
            DreameMowerProperty.REMOTE_CONTROL
        )
        state = _lower_enum_name(
            getattr(getattr(device, "status", None), "state", None)
        )
        status_obj = getattr(getattr(device, "status", None), "status", None)
        status = _lower_enum_name(status_obj)
        active = bool(
            getattr(device, "_remote_control", False)
            or status_obj is DreameMowerStatus.REMOTE_CONTROL
            or status == "remote_control"
            or state == "remote_control"
        )
        state_safe: bool | None = None
        state_block_reason: str | None = None
        if mapping:
            snapshot = snapshot_from_device(self._descriptor, device)
            state_block_reason = remote_control_block_reason(snapshot)
            state_safe = state_block_reason is None

        if not mapping:
            return DreameLawnMowerRemoteControlSupport(
                supported=False,
                active=active,
                state=state,
                status=status,
                reason="Remote-control property mapping is not available.",
            )

        if bool(getattr(getattr(device, "status", None), "fast_mapping", False)):
            state_safe = False
            state_block_reason = "Remote control is blocked while fast mapping."
            return DreameLawnMowerRemoteControlSupport(
                supported=False,
                active=active,
                state_safe=state_safe,
                state_block_reason=state_block_reason,
                siid=mapping.get("siid"),
                piid=mapping.get("piid"),
                state=state,
                status=status,
                reason=state_block_reason,
            )

        return DreameLawnMowerRemoteControlSupport(
            supported=True,
            active=active,
            state_safe=state_safe,
            state_block_reason=state_block_reason,
            siid=mapping.get("siid"),
            piid=mapping.get("piid"),
            state=state,
            status=status,
        )

    def _sync_remote_control_move_step(
        self,
        rotation: int,
        velocity: int,
        prompt: bool | None,
    ) -> Any:
        _validate_remote_control_step(rotation=rotation, velocity=velocity)
        support = self._sync_get_remote_control_support(refresh=False)
        if not support.supported:
            reason = support.reason or "Remote control is not supported."
            raise DreameLawnMowerConnectionError(reason)
        if (rotation or velocity) and support.state_block_reason:
            raise DreameLawnMowerConnectionError(support.state_block_reason)

        device = self._ensure_device()
        try:
            return device.remote_control_move_step(
                rotation=rotation,
                velocity=velocity,
                prompt=prompt,
            )
        except (DeviceException, InvalidActionException) as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_get_camera_feature_support(
        self,
        refresh: bool = False,
        include_cloud: bool = True,
        language: str | None = "en",
    ) -> DreameLawnMowerCameraFeatureSupport:
        if refresh:
            device = self._sync_update_device()
        else:
            device = self._ensure_device()

        try:
            from .types import DreameMowerAction, DreameMowerProperty
        except ImportError:
            return DreameLawnMowerCameraFeatureSupport(
                supported=False,
                advertised=False,
                reason="Camera protocol types are unavailable.",
            )

        property_mappings = _protocol_mapping_summary(
            getattr(device, "property_mapping", {}),
            {
                "stream_status": DreameMowerProperty.STREAM_STATUS,
                "stream_audio": DreameMowerProperty.STREAM_AUDIO,
                "stream_record": DreameMowerProperty.STREAM_RECORD,
                "take_photo": DreameMowerProperty.TAKE_PHOTO,
                "stream_keep_alive": DreameMowerProperty.STREAM_KEEP_ALIVE,
                "stream_fault": DreameMowerProperty.STREAM_FAULT,
                "stream_property": DreameMowerProperty.STREAM_PROPERTY,
                "stream_task": DreameMowerProperty.STREAM_TASK,
                "stream_upload": DreameMowerProperty.STREAM_UPLOAD,
                "stream_code": DreameMowerProperty.STREAM_CODE,
            },
        )
        action_mappings = _protocol_mapping_summary(
            getattr(device, "action_mapping", {}),
            {
                "get_photo_info": DreameMowerAction.GET_PHOTO_INFO,
                "stream_video": DreameMowerAction.STREAM_VIDEO,
                "stream_audio": DreameMowerAction.STREAM_AUDIO,
                "stream_property": DreameMowerAction.STREAM_PROPERTY,
                "stream_code": DreameMowerAction.STREAM_CODE,
            },
        )

        info_raw = getattr(getattr(device, "info", None), "raw", {}) or {}
        device_info = info_raw.get("deviceInfo", {}) or {}
        permit = _as_optional_text(device_info.get("permit") or info_raw.get("permit"))
        feature = _as_optional_text(
            device_info.get("feature") or info_raw.get("feature")
        )
        live_key_define = device_info.get("liveKeyDefine") or {}
        capability = getattr(device, "capability", None)
        status = getattr(device, "status", None)
        camera_streaming = bool(getattr(capability, "camera_streaming", False))
        camera_light = _optional_bool(
            getattr(capability, "fill_light", None)
            if hasattr(capability, "fill_light")
            else getattr(capability, "camera_light", None)
        )
        ai_detection = bool(getattr(capability, "ai_detection", False))
        obstacles = bool(getattr(capability, "obstacles", False))
        stream_status_raw = _safe_get_device_property(
            device,
            DreameMowerProperty.STREAM_STATUS,
        )
        stream_status = _lower_enum_name(getattr(status, "stream_status", None))
        stream_session_present = bool(getattr(status, "stream_session", None))
        advertised = _camera_feature_advertised(
            camera_streaming=camera_streaming,
            camera_light=camera_light,
            ai_detection=ai_detection,
            obstacles=obstacles,
            permit=permit,
            feature=feature,
            live_key_define=live_key_define,
            video_status=device_info.get("videoStatus") or info_raw.get("videoStatus"),
        )
        protocol_mappings_available = bool(
            action_mappings.get("get_photo_info")
            or action_mappings.get("stream_video")
            or property_mappings.get("stream_status")
            or property_mappings.get("take_photo")
        )
        cloud_user_features = None
        cloud_user_features_error = None
        if include_cloud:
            try:
                cloud_user_features = _cloud_user_feature_summary(
                    self._sync_get_cloud_user_features(language)
                )
            except DreameLawnMowerConnectionError as err:
                cloud_user_features_error = str(err)

        reason = None
        if not protocol_mappings_available:
            reason = "Camera/photo protocol mappings are not available."
        elif not advertised:
            reason = "Cloud/device metadata does not advertise camera or photo support."

        return DreameLawnMowerCameraFeatureSupport(
            supported=protocol_mappings_available and advertised,
            advertised=advertised,
            camera_streaming=camera_streaming,
            camera_light=camera_light,
            ai_detection=ai_detection,
            obstacles=obstacles,
            permit=permit,
            feature=feature,
            extend_sc_type=tuple(
                str(item) for item in device_info.get("extendScType", []) or []
            ),
            video_status=_json_safe(
                device_info.get("videoStatus") or info_raw.get("videoStatus")
            ),
            video_dynamic_vendor=_optional_bool(
                device_info.get("videoDynamicVendor")
                if "videoDynamicVendor" in device_info
                else info_raw.get("videoDynamicVendor")
            ),
            live_key_count=(
                len(live_key_define) if isinstance(live_key_define, Mapping) else 0
            ),
            stream_session_present=stream_session_present,
            stream_status=stream_status,
            stream_status_raw=_json_safe(stream_status_raw),
            property_mappings=property_mappings,
            action_mappings=action_mappings,
            cloud_user_features=cloud_user_features,
            cloud_user_features_error=cloud_user_features_error,
            reason=reason,
        )

    def _sync_get_firmware_update_support(
        self,
        refresh: bool = False,
        include_cloud: bool = True,
        language: str | None = "en",
    ) -> DreameLawnMowerFirmwareUpdateSupport:
        if refresh:
            device = self._sync_update_device()
        else:
            device = self._ensure_device()

        cloud_device_info = None
        cloud_device_list_page = None
        cloud_firmware_check = None
        batch_ota_info = None
        debug_ota_catalog = None
        cloud_error = None
        if include_cloud:
            try:
                cloud_device_info = self._sync_get_cloud_device_info(language)
                cloud_device_list_page = self._sync_get_cloud_device_list_page(
                    current=1,
                    size=20,
                    language=language,
                    master=None,
                    shared_status=None,
                )
                cloud_firmware_check = self._sync_get_cloud_firmware_check(language)
            except DreameLawnMowerConnectionError as err:
                cloud_error = str(err)
        try:
            batch_ota_info = self._sync_get_batch_ota_info()
        except DreameLawnMowerConnectionError as err:
            if cloud_error is None:
                cloud_error = str(err)
        if include_cloud:
            try:
                debug_ota_catalog = self._sync_get_debug_ota_catalog(
                    current_version=_as_optional_text(
                        getattr(getattr(device, "info", None), "firmware_version", None)
                    )
                )
            except DreameLawnMowerConnectionError as err:
                debug_ota_catalog = {
                    "source": "debug_ota_catalog",
                    "available": False,
                    "errors": [{"stage": "fetch", "error": str(err)}],
                }

        return firmware_update_support_from_device(
            device,
            cloud_device_info=cloud_device_info,
            cloud_device_list_page=cloud_device_list_page,
            cloud_firmware_check=cloud_firmware_check,
            batch_ota_info=batch_ota_info,
            debug_ota_catalog=debug_ota_catalog,
            cloud_error=cloud_error,
        )

    def _sync_get_status_blob(
        self,
        refresh: bool = False,
        include_cloud: bool = True,
    ) -> DreameLawnMowerStatusBlob | None:
        return self._sync_get_decoded_status_blob(
            MOWER_RAW_STATUS_PROPERTY_KEY,
            refresh=refresh,
            include_cloud=include_cloud,
        )

    def _sync_get_runtime_status_blob(
        self,
        refresh: bool = False,
        include_cloud: bool = True,
    ) -> DreameLawnMowerStatusBlob | None:
        blob = self._sync_get_decoded_status_blob(
            MOWER_RUNTIME_STATUS_PROPERTY_KEY,
            refresh=refresh,
            include_cloud=include_cloud,
        )
        self._latest_runtime_status_blob = blob
        return blob

    def _sync_get_bluetooth_connected(
        self,
        refresh: bool = False,
        include_cloud: bool = True,
    ) -> bool | None:
        if refresh:
            device = self._sync_update_device()
        else:
            device = self._ensure_device()

        realtime_entry = (getattr(device, "realtime_properties", {}) or {}).get(
            MOWER_BLUETOOTH_PROPERTY_KEY
        )
        if isinstance(realtime_entry, Mapping):
            parsed = self._coerce_property_bool(realtime_entry.get("value"))
            if parsed is not None:
                return parsed

        if not include_cloud:
            return None

        response = self._sync_get_cloud_properties(MOWER_BLUETOOTH_PROPERTY_KEY)
        for entry in self._normalize_cloud_property_entries(response):
            if str(entry.get("key", "")) != MOWER_BLUETOOTH_PROPERTY_KEY:
                continue
            parsed = self._coerce_property_bool(entry.get("value"))
            if parsed is not None:
                return parsed
        return None

    def _sync_get_decoded_status_blob(
        self,
        property_key: str,
        *,
        refresh: bool,
        include_cloud: bool,
    ) -> DreameLawnMowerStatusBlob | None:
        if refresh:
            device = self._sync_update_device()
        else:
            device = self._ensure_device()

        realtime_entry = (getattr(device, "realtime_properties", {}) or {}).get(
            property_key
        )
        if isinstance(realtime_entry, Mapping):
            decoded = decode_mower_status_blob(
                realtime_entry.get("value"),
                source="realtime",
            )
            if decoded is not None:
                return decoded

        if not include_cloud:
            return None

        response = self._sync_get_cloud_properties(property_key)
        for entry in self._normalize_cloud_property_entries(response):
            if str(entry.get("key", "")) == property_key:
                decoded = decode_mower_status_blob(
                    entry.get("value"),
                    source="cloud",
                )
                if decoded is not None:
                    return decoded
        return None

    def _sync_capture_operation_snapshot(
        self,
        label: str | None,
        include_status_blob: bool,
        include_cloud_status_blob: bool,
        include_remote_control: bool,
        include_map_view: bool,
        include_firmware: bool,
        map_timeout: float,
        map_interval: float,
        language: str | None,
    ) -> dict[str, Any]:
        device = self._sync_update_device()
        snapshot = snapshot_from_device(self._descriptor, device)
        errors: list[dict[str, str]] = []
        payload: dict[str, Any] = {
            "label": label,
            "captured_at": datetime.now(UTC).isoformat(),
            "snapshot": _operation_snapshot_summary(snapshot),
            "unknown_property_summary": _operation_property_summary(
                getattr(device, "unknown_properties", {}) or {}
            ),
            "realtime_summary": _operation_property_summary(
                getattr(device, "realtime_properties", {}) or {},
                unknown_prefix="UNKNOWN_REALTIME_",
            ),
            "errors": errors,
        }

        if include_status_blob:
            try:
                status_blob = self._sync_get_status_blob(
                    refresh=False,
                    include_cloud=include_cloud_status_blob,
                )
                payload["status_blob"] = (
                    status_blob.as_dict() if status_blob is not None else None
                )
            except Exception as err:  # noqa: BLE001 - probe snapshots keep evidence
                payload["status_blob"] = None
                errors.append({"section": "status_blob", "error": str(err)})

        if include_remote_control:
            try:
                payload["remote_control_support"] = (
                    self._sync_get_remote_control_support(refresh=False).as_dict()
                )
            except Exception as err:  # noqa: BLE001 - probe snapshots keep evidence
                payload["remote_control_support"] = None
                errors.append({"section": "remote_control_support", "error": str(err)})

        if include_map_view:
            try:
                payload["map_view"] = self._sync_refresh_map_view(
                    timeout=map_timeout,
                    interval=map_interval,
                ).as_dict()
            except Exception as err:  # noqa: BLE001 - probe snapshots keep evidence
                payload["map_view"] = None
                errors.append({"section": "map_view", "error": str(err)})

        if include_firmware:
            try:
                payload["firmware_update"] = self._sync_get_firmware_update_support(
                    refresh=False,
                    include_cloud=True,
                    language=language,
                ).as_dict()
            except Exception as err:  # noqa: BLE001 - probe snapshots keep evidence
                payload["firmware_update"] = None
                errors.append({"section": "firmware_update", "error": str(err)})

        return payload

    def _sync_clean_segments(self, segment_ids: Sequence[int]) -> Any:
        """Start segment or zone mowing for the provided area ids."""
        if not segment_ids:
            raise ValueError("At least one segment id is required.")
        device = self._ensure_device()
        try:
            return device.clean_segment([int(segment_id) for segment_id in segment_ids])
        except (DeviceException, InvalidActionException, ValueError) as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_start_edge_mowing(self, contour_ids: Sequence[Sequence[int]]) -> Any:
        """Start edge mowing for the provided contour id pairs."""
        normalized = _normalize_contour_ids(contour_ids)
        if not normalized:
            raise ValueError("At least one contour id pair is required.")
        try:
            return self._sync_call_app_action(
                {
                    "m": "a",
                    "p": 0,
                    "o": 101,
                    "d": {"edge": normalized},
                }
            )
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_clean_spots(self, points: Sequence[Sequence[int]]) -> Any:
        """Start spot mowing for the provided center points."""
        if not points:
            raise ValueError("At least one spot point is required.")
        device = self._ensure_device()
        try:
            return device.clean_spot(
                [[int(point[0]), int(point[1])] for point in points],
                1,
            )
        except (DeviceException, InvalidActionException, ValueError) as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_switch_current_map(self, map_index: int) -> Any:
        """Switch the active mower map by app map index."""
        if map_index < 0:
            raise ValueError("map_index must be zero or greater.")
        try:
            return self._sync_call_app_action(
                {
                    "m": "a",
                    "p": 0,
                    "o": 200,
                    "d": {"idx": int(map_index)},
                }
            )
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_get_vector_map_details(self) -> dict[str, Any]:
        """Return parsed batch vector-map details without rendering an image."""
        try:
            batch_data = self._sync_get_vector_map_batch_data()
        except DreameLawnMowerConnectionError as err:
            return {
                "available": False,
                "source": "batch_vector_map",
                "error": str(err),
            }

        vector_map = parse_batch_vector_map(batch_data)
        if vector_map is None:
            return {
                "available": False,
                "source": "batch_vector_map",
                "error": "No vector map data returned by the batch map path.",
            }

        details = vector_map_to_details(vector_map)
        details["available"] = True
        details["source"] = "batch_vector_map"
        return details

    def _sync_request_photo_info(self, parameters: Any = None) -> Any:
        support = self._sync_get_camera_feature_support(
            refresh=False,
            include_cloud=False,
        )
        if not support.supported:
            reason = support.reason or "Camera/photo support is not available."
            raise DreameLawnMowerConnectionError(reason)

        device = self._ensure_device()
        try:
            from .types import DreameMowerAction

            result = device.call_action(DreameMowerAction.GET_PHOTO_INFO, parameters)
        except (DeviceException, InvalidActionException) as err:
            raise DreameLawnMowerConnectionError(str(err)) from err
        if result is None:
            raise DreameLawnMowerConnectionError(
                "GET_PHOTO_INFO returned no response."
            )
        return result

    def _sync_probe_camera_sources(
        self,
        language: str,
        request_device_properties: bool,
    ) -> dict[str, Any]:
        self._sync_update_device()
        support = self._sync_get_camera_feature_support(
            refresh=False,
            include_cloud=True,
            language=language,
        )
        cloud_properties = self._sync_scan_cloud_properties(
            keys=CAMERA_PROBE_PROPERTY_KEYS,
            siids=None,
            piid_start=1,
            piid_end=1,
            chunk_size=50,
            language=language,
            only_values=False,
        )
        device_properties = (
            self._sync_probe_camera_device_properties()
            if request_device_properties
            else {"skipped": True}
        )
        return build_camera_probe_payload(
            descriptor=self._descriptor,
            support=support,
            cloud_properties=cloud_properties,
            device_properties=device_properties,
        )

    def _sync_probe_camera_stream_handshake(
        self,
        timeout: float,
        interval: float,
        operation: str,
        payload_mode: str,
    ) -> dict[str, Any]:
        operation = _validate_stream_operation(operation)
        payload_mode = _validate_stream_payload_mode(payload_mode)
        before = self._sync_update_device()
        self._guard_camera_stream_probe_idle(before)
        support = self._sync_get_camera_feature_support(
            refresh=False,
            include_cloud=True,
            language="en",
        )
        if not support.supported:
            reason = support.reason or "Camera/photo support is not available."
            raise DreameLawnMowerConnectionError(reason)

        try:
            from .types import DreameMowerProperty
        except ImportError as err:
            raise DreameLawnMowerConnectionError(
                "Camera protocol types are unavailable."
            ) from err

        output: dict[str, Any] = {
            "operation": operation,
            "payload_mode": payload_mode,
            "before": self._stream_status_payload(before),
            "start_result": None,
            "polls": [],
            "end_result": None,
            "after": None,
            "cleanup_error": None,
        }

        device = self._ensure_device()
        try:
            output["start_result"] = _json_safe(
                self._call_stream_video_status(
                    device,
                    DreameMowerProperty,
                    operation=operation,
                    oper_type="start",
                    payload_mode=payload_mode,
                )
            )
            deadline = time.monotonic() + max(timeout, 0)
            while True:
                refreshed = self._sync_update_device()
                poll = self._stream_status_payload(refreshed)
                output["polls"].append(poll)
                if poll["stream_session_present"] or poll["stream_status"]:
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(max(interval, 0.1))
        finally:
            try:
                output["end_result"] = _json_safe(
                    self._call_stream_video_status(
                        device,
                        DreameMowerProperty,
                        operation=operation,
                        oper_type="end",
                        payload_mode=payload_mode,
                    )
                )
            except Exception as err:
                output["cleanup_error"] = str(err)
            try:
                output["after"] = self._stream_status_payload(
                    self._sync_update_device()
                )
            except Exception as err:
                if output["cleanup_error"] is None:
                    output["cleanup_error"] = str(err)

        return output

    def _call_stream_video_status(
        self,
        device: Any,
        property_enum: Any,
        *,
        operation: str,
        oper_type: str,
        payload_mode: str,
    ) -> Any:
        if payload_mode == "with_session":
            return device.call_stream_video_action(
                property_enum.STREAM_STATUS,
                {"operType": oper_type, "operation": operation},
            )

        payload: dict[str, Any] = {"operType": oper_type, "operation": operation}
        if payload_mode == "empty_session":
            payload["session"] = ""

        from .types import PIID, DreameMowerAction

        return device.call_action(
            DreameMowerAction.STREAM_VIDEO,
            [
                {
                    "piid": PIID(property_enum.STREAM_STATUS),
                    "value": str(json.dumps(payload, separators=(",", ":"))).replace(
                        " ",
                        "",
                    ),
                }
            ],
        )

    def _guard_camera_stream_probe_idle(self, device: Any) -> None:
        status = getattr(device, "status", None)
        snapshot = snapshot_from_device(self._descriptor, device)
        raw_running = bool(
            snapshot.raw_attributes.get("running")
            or getattr(status, "running", False)
        )
        if snapshot.mowing or snapshot.returning or raw_running:
            raise DreameLawnMowerConnectionError(
                "Camera stream handshake probe is blocked while the mower is active."
            )
        if bool(getattr(status, "fast_mapping", False)):
            raise DreameLawnMowerConnectionError(
                "Camera stream handshake probe is blocked while mapping."
            )

    def _stream_status_payload(self, device: Any) -> dict[str, Any]:
        try:
            from .types import DreameMowerProperty
        except ImportError:
            stream_status_raw = None
        else:
            stream_status_raw = _safe_get_device_property(
                device,
                DreameMowerProperty.STREAM_STATUS,
            )
        status = getattr(device, "status", None)
        return {
            "state": _lower_enum_name(getattr(status, "state", None)),
            "status": _lower_enum_name(getattr(status, "status", None)),
            "stream_status": _lower_enum_name(getattr(status, "stream_status", None)),
            "stream_session_present": bool(getattr(status, "stream_session", None)),
            "stream_status_raw": _json_safe(stream_status_raw),
        }

    def _sync_probe_camera_device_properties(self) -> dict[str, Any]:
        device = self._ensure_device()
        try:
            from .types import DreameMowerProperty
        except ImportError:
            return {"error": "Camera protocol types are unavailable."}

        properties = (
            DreameMowerProperty.STREAM_STATUS,
            DreameMowerProperty.STREAM_AUDIO,
            DreameMowerProperty.STREAM_RECORD,
            DreameMowerProperty.TAKE_PHOTO,
            DreameMowerProperty.STREAM_KEEP_ALIVE,
            DreameMowerProperty.STREAM_FAULT,
            DreameMowerProperty.STREAM_PROPERTY,
            DreameMowerProperty.STREAM_TASK,
            DreameMowerProperty.STREAM_UPLOAD,
            DreameMowerProperty.STREAM_CODE,
        )
        requested = []
        for prop in properties:
            mapping = getattr(device, "property_mapping", {}).get(prop)
            if mapping and "aiid" not in mapping:
                requested.append({"did": str(prop.value), **mapping})

        protocol = getattr(device, "_protocol", None)
        if protocol is None:
            return {
                "requested_property_count": len(requested),
                "requested_properties": requested,
                "error": "Device protocol is unavailable.",
            }

        raw_response = None
        handled = False
        error = None
        try:
            raw_response = protocol.get_properties(requested)
            if raw_response is None:
                error = "Device protocol returned no property response."
            else:
                handled = bool(device._handle_properties(raw_response))
        except Exception as err:
            error = str(err)

        values = {}
        for prop in properties:
            values[prop.name.lower()] = _json_safe(
                _safe_get_device_property(device, prop)
            )

        status = getattr(device, "status", None)
        return {
            "requested_property_count": len(requested),
            "requested_properties": requested,
            "raw_response": _json_safe(raw_response),
            "handled": handled,
            "error": error,
            "values": values,
            "stream_session_present": bool(getattr(status, "stream_session", None)),
            "stream_status": _lower_enum_name(getattr(status, "stream_status", None)),
        }

    def _sync_refresh_map_summary(
        self,
        timeout: float,
        interval: float,
    ) -> DreameLawnMowerMapSummary | None:
        return self._sync_refresh_map_view(timeout, interval).summary

    def _sync_get_map_png(
        self,
        timeout: float,
        interval: float,
    ) -> bytes | None:
        return self._sync_refresh_map_view(timeout, interval).image_png

    def _sync_refresh_map_view(
        self,
        timeout: float,
        interval: float,
    ) -> DreameLawnMowerMapView:
        app_view = self._sync_refresh_app_map_view(
            legacy_error=None,
            legacy_reason="app_action_map_primary",
        )

        vector_view = self._with_fallback_app_maps(
            self._sync_refresh_vector_map_view(),
            app_view,
        )
        if _map_view_has_live_path(vector_view):
            return vector_view

        if app_view.available and app_view.image_png is not None:
            return app_view

        if vector_view.available and vector_view.image_png is not None:
            return vector_view

        legacy_view = self._sync_refresh_legacy_map_view(timeout, interval)
        legacy_view = self._with_fallback_app_maps(legacy_view, app_view)
        if legacy_view.available or legacy_view.image_png is not None:
            return legacy_view

        return app_view

    def _sync_refresh_vector_map_view(self) -> DreameLawnMowerMapView:
        source = "batch_vector_map"
        try:
            batch_data = self._sync_get_vector_map_batch_data()
        except DreameLawnMowerConnectionError as err:
            error = str(err)
            return DreameLawnMowerMapView(
                source=source,
                error=error,
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason=error,
                ),
            )

        vector_map = parse_batch_vector_map(batch_data)
        if vector_map is None:
            return DreameLawnMowerMapView(
                source=source,
                error="No vector map data returned by the batch map path.",
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason="batch_vector_map_empty",
                ),
            )

        summary = vector_map_to_summary(vector_map)
        details = vector_map_to_details(vector_map)
        runtime_blob = self._latest_runtime_status_blob
        runtime_track_segments = self._runtime_live_track_segments
        runtime_track_point_count = sum(
            len(segment) for segment in runtime_track_segments
        )
        if runtime_track_point_count:
            details["runtime_track_segment_count"] = len(runtime_track_segments)
            details["runtime_track_point_count"] = runtime_track_point_count
            details["runtime_track_length_m"] = round(
                sum(_coordinate_path_length_m(segment) for segment in runtime_track_segments),
                2,
            )
            details["runtime_pose_x"] = getattr(runtime_blob, "candidate_runtime_pose_x", None)
            details["runtime_pose_y"] = getattr(runtime_blob, "candidate_runtime_pose_y", None)
            details["runtime_heading_deg"] = getattr(
                runtime_blob,
                "candidate_runtime_heading_deg",
                None,
            )
            details["has_live_path"] = True
            if summary is not None:
                summary = replace(
                    summary,
                    path_point_count=summary.path_point_count + runtime_track_point_count,
                )
        try:
            image_png = render_vector_map_png(
                vector_map,
                runtime_track_segments=runtime_track_segments,
                runtime_position=_runtime_blob_position(runtime_blob),
            )
        except Exception as err:  # noqa: BLE001 - diagnostics path
            return DreameLawnMowerMapView(
                source=source,
                summary=summary,
                details=details,
                error=f"Failed to render vector map data: {err}",
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason="batch_vector_map_render_failed",
                ),
            )

        if image_png is None:
            return DreameLawnMowerMapView(
                source=source,
                summary=summary,
                details=details,
                error="Vector map renderer did not produce an image.",
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason="batch_vector_map_render_empty",
                ),
            )

        return DreameLawnMowerMapView(
            source=source,
            summary=summary,
            image_png=image_png,
            details=details,
            diagnostics=self._safe_map_diagnostics(
                source=source,
                reason="batch_vector_map_rendered",
            ),
        )

    def _sync_refresh_legacy_map_view(
        self,
        timeout: float,
        interval: float,
    ) -> DreameLawnMowerMapView:
        source = "legacy_current_map"
        try:
            map_data = self._sync_wait_for_map(timeout, interval)
        except DreameLawnMowerConnectionError as err:
            error = str(err)
            return DreameLawnMowerMapView(
                source=source,
                error=error,
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason=error,
                ),
            )

        if map_data is None:
            error = "No map data returned by the legacy current-map path."
            return DreameLawnMowerMapView(
                source=source,
                error=error,
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason="legacy_current_map_empty",
                ),
            )

        summary = map_summary_from_map_data(map_data)
        device = self._ensure_device()
        render_map_data = device.get_map_for_render(map_data) or map_data

        from .map import DreameMowerMapDataJsonRenderer

        try:
            renderer = DreameMowerMapDataJsonRenderer()
            image_png = renderer.render_map(render_map_data)
        except Exception as err:
            error = f"Failed to render map data: {err}"
            return DreameLawnMowerMapView(
                source=source,
                summary=summary,
                error=error,
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason="legacy_current_map_render_failed",
                ),
            )

        return DreameLawnMowerMapView(
            source=source,
            summary=summary,
            image_png=image_png,
            diagnostics=self._safe_map_diagnostics(
                source=source,
                reason="legacy_current_map_rendered",
            ),
        )

    def _sync_refresh_app_map_view(
        self,
        *,
        legacy_error: str | None,
        legacy_reason: str,
    ) -> DreameLawnMowerMapView:
        source = "app_action_map"
        try:
            app_maps = self._sync_get_app_maps(
                chunk_size=400,
                include_payload=True,
                include_objects=True,
                include_object_urls=False,
            )
            selected = _select_app_map_payload(app_maps)
            if selected is None:
                error = legacy_error or "No app-map payload was returned."
                return DreameLawnMowerMapView(
                    source=source,
                    error=error,
                    app_maps=_app_maps_view_metadata(app_maps),
                    diagnostics=self._safe_map_diagnostics(
                        source=source,
                        reason=legacy_reason,
                    ),
                )
            payload = selected.get("payload")
            image_png, width, height = _render_app_map_payload_png(payload)
            return DreameLawnMowerMapView(
                source=source,
                summary=_app_map_view_summary(selected, payload, width, height),
                image_png=image_png,
                details=_app_map_view_details(selected, payload),
                app_maps=_app_maps_view_metadata(app_maps),
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason="app_action_map_rendered",
                ),
            )
        except Exception as err:  # noqa: BLE001 - map view keeps diagnostics visible
            error = f"{legacy_error or 'Legacy map unavailable'}; app map failed: {err}"
            return DreameLawnMowerMapView(
                source=source,
                error=error,
                diagnostics=self._safe_map_diagnostics(
                    source=source,
                    reason="app_action_map_failed",
                ),
            )

    def _sync_get_vector_map_batch_data(self) -> Mapping[str, Any] | None:
        return self._sync_get_batch_device_data(_vector_map_batch_keys())

    def _sync_get_batch_device_data(
        self,
        keys: Sequence[str] | None = None,
    ) -> Mapping[str, Any] | None:
        cloud = self._sync_get_cloud_protocol()
        requested = list(keys or [])
        try:
            response = cloud.get_batch_device_datas(requested)
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err
        return response if isinstance(response, Mapping) else None

    def _sync_get_cloud_device_info(
        self,
        language: str | None = None,
    ) -> dict[str, Any] | None:
        cloud = self._sync_get_cloud_protocol()

        try:
            if hasattr(cloud, "get_device_info_v2"):
                return cloud.get_device_info_v2(language)
            return cloud.get_device_info()
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    @staticmethod
    def _with_fallback_app_maps(
        map_view: DreameLawnMowerMapView,
        app_view: DreameLawnMowerMapView,
    ) -> DreameLawnMowerMapView:
        if map_view.app_maps is not None or app_view.app_maps is None:
            return map_view
        return replace(map_view, app_maps=app_view.app_maps)

    def _sync_get_cloud_user_features(
        self,
        language: str | None = None,
    ) -> Any:
        cloud = self._sync_get_cloud_protocol()

        try:
            return cloud.get_user_features(language)
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_get_cloud_device_otc_info(
        self,
        language: str | None = None,
    ) -> Any:
        cloud = self._sync_get_cloud_protocol()

        try:
            if hasattr(cloud, "get_device_otc_info"):
                return cloud.get_device_otc_info(language)
            return None
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_get_cloud_firmware_check(
        self,
        language: str | None = None,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        cloud = self._sync_get_cloud_protocol()

        try:
            raw = cloud.check_device_version(language)
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

        result = _normalize_cloud_firmware_check(
            raw,
            current_version=_as_optional_text(
                getattr(getattr(self._ensure_device(), "info", None), "firmware_version", None)
            ),
        )
        if include_raw:
            result["raw"] = _json_safe(raw, max_depth=4)
        return result

    def _sync_approve_firmware_update(
        self,
        language: str | None = None,
    ) -> dict[str, Any]:
        cloud = self._sync_get_cloud_protocol()

        try:
            raw = cloud.manual_firmware_update(language)
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

        result: dict[str, Any] = {
            "source": "cloud_manual_firmware_update",
            "available": isinstance(raw, Mapping),
            "accepted": False,
            "success": False,
        }
        if isinstance(raw, Mapping):
            code = raw.get("code")
            success = raw.get("success")
            data = raw.get("data")
            inner_code = data.get("code") if isinstance(data, Mapping) else None
            inner_success = (
                data.get("success") if isinstance(data, Mapping) else None
            )
            accepted = bool(success) if isinstance(success, bool) else code == 0
            result.update(
                {
                    "code": code,
                    "accepted": accepted,
                    "success": accepted,
                    "msg": _as_optional_text(raw.get("msg")),
                    "data": _json_safe(data, max_depth=3),
                    "wrapper_success": success if isinstance(success, bool) else None,
                    "inner_code": inner_code,
                    "inner_success": (
                        inner_success if isinstance(inner_success, bool) else None
                    ),
                }
            )
        else:
            result["errors"] = [{"stage": "response", "error": "invalid_response"}]
        return result

    def _sync_get_app_plugin_version(
        self,
        app_version_code: int = 2050300,
        os: int = 1,
    ) -> Any:
        cloud = self._sync_get_cloud_protocol()
        try:
            if hasattr(cloud, "get_app_plugin_version"):
                return cloud.get_app_plugin_version(
                    self._descriptor.model,
                    app_version_code,
                    os,
                )
            return None
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_get_app_schedules(
        self,
        include_raw: bool = False,
        map_indices: Sequence[int] | None = None,
        chunk_size: int = SCHEDULE_CHUNK_SIZE,
    ) -> dict[str, Any]:
        """Fetch and decode mower schedules through read-only app actions."""
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero.")

        result: dict[str, Any] = {
            "source": "app_action_schedule",
            "available": False,
            "current_task": None,
            "schedules": [],
            "errors": [],
        }

        try:
            task_result = self._sync_call_app_action(
                {"m": "g", "t": "SCHDT", "d": {"t": 0}}
            )
            result["raw_current_task"] = _json_safe(task_result, max_depth=4)
            task_data = _app_action_data(task_result)
            result["current_task"] = schedule_task_summary(task_data)
        except Exception as err:  # noqa: BLE001 - schedule task is diagnostic
            result["errors"].append({"stage": "current_task", "error": str(err)})

        for map_index in self._app_schedule_map_indices(map_indices):
            schedule_result: dict[str, Any] = {
                "idx": map_index,
                "label": "default" if map_index == -1 else f"map_{map_index}",
                "available": False,
            }
            try:
                info_result = self._sync_call_app_action(
                    {"m": "g", "t": "SCHDIV2", "d": {"i": map_index}}
                )
                schedule_result["raw_info"] = _json_safe(info_result, max_depth=4)
                info = _app_action_data(info_result)
                if not isinstance(info, Mapping):
                    raise DreameLawnMowerConnectionError(
                        "SCHDIV2 returned invalid schedule metadata."
                    )
                size = _positive_int(info.get("l"))
                version = _positive_int(info.get("v"))
                schedule_result["size"] = size
                schedule_result["version"] = version
                if not size or version is None or version == EMPTY_SCHEDULE_VERSION:
                    schedule_result["plans"] = []
                    result["schedules"].append(schedule_result)
                    continue

                payload_text, chunk_count, offset = self._sync_get_app_schedule_text(
                    size=size,
                    version=version,
                    chunk_size=chunk_size,
                )
                plans = decode_schedule_payload_text(payload_text)
                schedule_result.update(
                    {
                        "available": bool(plans),
                        "chunk_count": chunk_count,
                        "downloaded_size": offset,
                        "plan_count": len(plans),
                        "enabled_plan_count": sum(
                            1 for plan in plans if plan.get("enabled")
                        ),
                        "plans": plans,
                    }
                )
                if include_raw:
                    schedule_result["raw_text"] = payload_text
                if plans:
                    result["available"] = True
            except Exception as err:  # noqa: BLE001 - keep probing other maps
                schedule_result["error"] = str(err)
                result["errors"].append(
                    {"idx": map_index, "stage": "schedule", "error": str(err)}
                )
            result["schedules"].append(schedule_result)

        return result

    def _sync_set_app_schedule_plan_enabled(
        self,
        map_index: int,
        plan_id: int,
        enabled: bool,
        execute: bool = False,
        confirm_write: bool = False,
    ) -> dict[str, Any]:
        """Build or execute a schedule enable-status app action request."""
        if execute and not confirm_write:
            raise ValueError(
                "Schedule writes require confirm_write=True when execute=True."
            )

        schedules = self._sync_get_app_schedules(map_indices=[map_index])
        if not schedules.get("schedules"):
            raise DreameLawnMowerConnectionError(
                f"No schedule metadata returned for map index {map_index}."
            )
        schedule = schedules["schedules"][0]
        version = _positive_int(schedule.get("version"))
        if version is None or version == EMPTY_SCHEDULE_VERSION:
            raise DreameLawnMowerConnectionError(
                f"No writable schedule version returned for map index {map_index}."
            )
        plans = schedule.get("plans")
        if not isinstance(plans, list):
            raise DreameLawnMowerConnectionError(
                f"No decoded schedule plans returned for map index {map_index}."
            )

        updated_plans: list[dict[str, Any]] = []
        previous_enabled: bool | None = None
        found = False
        for plan in plans:
            if not isinstance(plan, Mapping):
                continue
            updated_plan = dict(plan)
            if _positive_int(updated_plan.get("plan_id")) == plan_id:
                previous_enabled = bool(updated_plan.get("enabled"))
                updated_plan["enabled"] = bool(enabled)
                found = True
            updated_plans.append(updated_plan)
        if not found:
            raise ValueError(
                f"Schedule plan {plan_id} was not found for map index {map_index}."
            )

        request = build_schedule_enable_status_request(
            map_index=map_index,
            version=version,
            plans=updated_plans,
        )
        target_enabled = bool(enabled)
        result: dict[str, Any] = {
            "source": "app_action_schedule_write",
            "action": "set_schedule_plan_enabled",
            "dry_run": not execute,
            "executed": False,
            "map_index": map_index,
            "plan_id": plan_id,
            "previous_enabled": previous_enabled,
            "enabled": target_enabled,
            "changed": (
                previous_enabled is not None and previous_enabled != target_enabled
            ),
            "schedule": _schedule_entry_overview(schedule),
            "target_plan": _schedule_plan_overview(
                updated_plans,
                plan_id=plan_id,
                previous_enabled=previous_enabled,
                enabled=target_enabled,
            ),
            "version": version,
            "request": request,
        }
        if execute:
            response = self._sync_call_app_action(request)
            response_data = _app_action_data(response)
            if isinstance(response_data, Mapping) and response_data.get("r") not in (
                None,
                0,
            ):
                raise DreameLawnMowerConnectionError(
                    f"Schedule write failed: {response}"
                )
            result["executed"] = True
            result["response"] = _json_safe(response, max_depth=4)
            result["response_data"] = _json_safe(response_data, max_depth=4)
        return result

    def _sync_plan_app_schedule_upload(
        self,
        map_index: int,
        plans: Sequence[Mapping[str, Any]],
        execute: bool = False,
        confirm_write: bool = False,
        chunk_size: int = SCHEDULE_CHUNK_SIZE,
    ) -> dict[str, Any]:
        """Build or execute a full schedule upload request sequence."""
        if execute and not confirm_write:
            raise ValueError(
                "Schedule writes require confirm_write=True when execute=True."
            )
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero.")
        if not isinstance(plans, Sequence) or isinstance(plans, str | bytes):
            raise ValueError("plans must be a sequence of schedule plan mappings.")

        schedules = self._sync_get_app_schedules(map_indices=[map_index])
        if not schedules.get("schedules"):
            raise DreameLawnMowerConnectionError(
                f"No schedule metadata returned for map index {map_index}."
            )
        schedule = schedules["schedules"][0]
        version = _positive_int(schedule.get("version"))
        if version is None or version == EMPTY_SCHEDULE_VERSION:
            raise DreameLawnMowerConnectionError(
                f"No writable schedule version returned for map index {map_index}."
            )
        current_plans = schedule.get("plans")
        if not isinstance(current_plans, list):
            raise DreameLawnMowerConnectionError(
                f"No decoded schedule plans returned for map index {map_index}."
            )

        try:
            payload_text = encode_schedule_payload_text(list(plans))
            normalized_plans = decode_schedule_payload_text(payload_text)
        except Exception as err:  # noqa: BLE001 - caller gets readable validator text
            raise ValueError(f"Invalid schedule plans: {err}") from err

        current_payload_text = encode_schedule_payload_text(current_plans)
        requests = build_schedule_upload_requests(
            map_index=map_index,
            payload_text=payload_text,
            version=version,
            chunk_size=chunk_size,
        )
        request_candidate: dict[str, Any] | None = (
            requests[0]
            if len(requests) == 1
            else {"sequence": requests}
            if requests
            else None
        )
        result: dict[str, Any] = {
            "source": "app_action_schedule_write",
            "action": "upload_schedule_plans",
            "dry_run": not execute,
            "executed": False,
            "map_index": map_index,
            "changed": current_payload_text != payload_text,
            "version": version,
            "chunk_size": chunk_size,
            "chunk_count": max(len(requests) - 1, 0),
            "payload_size": len(payload_text.encode("utf-8")),
            "schedule": _schedule_entry_overview(schedule),
            "target_schedule": _schedule_upload_overview(normalized_plans),
            "request": request_candidate,
        }
        if execute:
            responses: list[Any] = []
            response_data_items: list[Any] = []
            for request in requests:
                response = self._sync_call_app_action(request)
                response_data = _app_action_data(response)
                if isinstance(response_data, Mapping) and response_data.get("r") not in (
                    None,
                    0,
                ):
                    raise DreameLawnMowerConnectionError(
                        f"Schedule upload failed: {response}"
                    )
                responses.append(_json_safe(response, max_depth=4))
                response_data_items.append(_json_safe(response_data, max_depth=4))
            result["executed"] = True
            result["response"] = responses
            result["response_data"] = response_data_items
        return result

    def _sync_plan_app_mowing_preference_update(
        self,
        map_index: int,
        area_id: int | None,
        changes: Mapping[str, Any],
        execute: bool = False,
        confirm_write: bool = False,
    ) -> dict[str, Any]:
        """Build or execute an app-action payload for mower preference changes."""
        if execute and not confirm_write:
            raise ValueError(
                "Preference writes require confirm_write=True when execute=True."
            )
        if not isinstance(changes, Mapping) or not changes:
            raise ValueError("At least one mowing preference change is required.")

        preferences = self._sync_get_mowing_preferences(map_indices=[map_index])
        maps = preferences.get("maps")
        if not isinstance(maps, list) or not maps:
            raise DreameLawnMowerConnectionError(
                f"No mowing preference metadata returned for map index {map_index}."
            )

        preference_map = maps[0]
        raw_preferences = preference_map.get("preferences")
        if not isinstance(raw_preferences, list):
            raise DreameLawnMowerConnectionError(
                f"No decoded mowing preferences returned for map index {map_index}."
            )

        mode = _positive_int(preference_map.get("mode"))
        requested_mode = None
        if MOWING_PREFERENCE_MODE_FIELD in changes:
            requested_mode = normalize_mowing_preference_mode(
                changes[MOWING_PREFERENCE_MODE_FIELD]
            )
        mode_changed = requested_mode is not None and requested_mode != mode

        setting_changes = {
            key: value
            for key, value in changes.items()
            if key != MOWING_PREFERENCE_MODE_FIELD
        }
        if (
            requested_mode is not None
            and requested_mode == 0
            and setting_changes
            and mode_changed
        ):
            raise ValueError(
                "preference_mode=global cannot be combined with per-area setting "
                "changes in the same request."
            )

        current_preference: Mapping[str, Any] | None = None
        updated_preference: Mapping[str, Any] | None = None
        changed_fields: list[str] = []
        payload: list[int] | None = None
        settings_request: dict[str, Any] | None = None

        if setting_changes:
            if not isinstance(area_id, int):
                raise ValueError(
                    "area_id is required when planning per-area mowing preference "
                    "setting changes."
                )
            for item in raw_preferences:
                if not isinstance(item, Mapping):
                    continue
                if _positive_int(item.get("area_id")) == area_id:
                    current_preference = item
                    break
            if current_preference is None:
                available_area_ids = [
                    _positive_int(item.get("area_id"))
                    for item in raw_preferences
                    if isinstance(item, Mapping)
                ]
                raise ValueError(
                    f"Mowing preference area {area_id} was not found for map index "
                    f"{map_index}. Available areas: {available_area_ids}"
                )

            updated_preference, changed_fields = apply_mowing_preference_changes(
                current_preference,
                setting_changes,
            )
            payload = encode_mowing_preference_payload(updated_preference)
            settings_request = {
                "m": "s",
                "t": "PRE",
                "d": payload,
            }

        mode_request = None
        if requested_mode is not None and (mode_changed or not setting_changes):
            mode_request = {
                "m": "s",
                "t": "PREP",
                "d": {
                    "idx": map_index,
                    "value": requested_mode,
                },
            }

        request_sequence = [
            request
            for request in [mode_request, settings_request]
            if isinstance(request, dict)
        ]
        if not request_sequence:
            request_sequence = [settings_request] if settings_request else []

        combined_changed_fields = (
            [MOWING_PREFERENCE_MODE_FIELD] if mode_changed else []
        ) + changed_fields
        primary_request = (
            request_sequence[0]
            if len(request_sequence) == 1
            else {"sequence": request_sequence}
            if request_sequence
            else None
        )
        result: dict[str, Any] = {
            "source": "app_action_mowing_preference_write",
            "action": "plan_mowing_preference_update",
            "dry_run": not execute,
            "executed": False,
            "execute_supported": True,
            "request_verified": False,
            "write_commands": {
                "settings": "PRE",
                "mode": "PREP",
            },
            "map_index": map_index,
            "area_id": area_id,
            "mode": mode,
            "mode_name": preference_map.get("mode_name"),
            "target_mode": requested_mode,
            "target_mode_name": mowing_preference_mode_name(requested_mode),
            "mode_changed": mode_changed,
            "changed": bool(combined_changed_fields),
            "changed_fields": combined_changed_fields,
            "changes": {
                key: mowing_preference_mode_name(requested_mode)
                if key == MOWING_PREFERENCE_MODE_FIELD
                else updated_preference.get("obstacle_avoidance_ai_classes")
                if key == "obstacle_avoidance_ai_classes"
                else updated_preference.get(key)
                if updated_preference is not None
                else None
                for key in changes
            },
            "map": _mowing_preference_map_overview(preference_map),
            "previous_preference": _mowing_preference_overview(current_preference)
            if current_preference is not None
            else None,
            "updated_preference": _mowing_preference_overview(updated_preference)
            if updated_preference is not None
            else None,
            "payload": payload,
            "request_candidate": primary_request,
            "request_candidates": request_sequence,
            "notes": (
                [
                    "Preference write prepared but not executed.",
                    "Send the candidate PRE/PREP request only with execute=true and an "
                    "explicit confirmation gate.",
                ]
                if not execute
                else [
                    "Preference write executed through the PRE/PREP request sequence after "
                    "explicit confirmation.",
                ]
            ),
        }
        if execute:
            responses: list[Any] = []
            response_payloads: list[Any] = []
            for request in request_sequence:
                response = self._sync_call_app_action(request)
                response_data = _app_action_data(response)
                if isinstance(response_data, Mapping) and response_data.get("r") not in (
                    None,
                    0,
                ):
                    raise DreameLawnMowerConnectionError(
                        f"Preference write failed: {response}"
                    )
                responses.append(_json_safe(response, max_depth=4))
                response_payloads.append(_json_safe(response_data, max_depth=4))
            result["executed"] = True
            result["request_verified"] = True
            if len(responses) == 1:
                result["response"] = responses[0]
                result["response_data"] = response_payloads[0]
            else:
                result["responses"] = responses
                result["response_data"] = response_payloads
        return result

    def _sync_get_batch_schedules(
        self,
        include_raw: bool = False,
        map_index_hint: int | None = None,
    ) -> dict[str, Any]:
        """Fetch and decode schedule data from batch device data."""
        if map_index_hint is None:
            map_index_hint = self._sync_get_current_app_map_index()
        batch_data = self._sync_get_batch_device_data(_batch_schedule_keys())
        if batch_data is None:
            return {
                "source": "batch_device_data_schedule",
                "available": False,
                "current_task": None,
                "schedules": [],
                "errors": [
                    {
                        "stage": "schedule",
                        "error": "Batch device data returned no schedule payload.",
                    }
                ],
            }
        return decode_batch_schedule_payload(
            batch_data,
            include_raw=include_raw,
            map_index_hint=map_index_hint,
        )

    def _sync_get_current_app_map_index(self) -> int | None:
        try:
            app_maps = self._sync_get_app_maps(
                chunk_size=400,
                include_payload=False,
                include_objects=False,
                include_object_urls=False,
            )
        except Exception:  # noqa: BLE001 - best-effort hint only
            return None
        return _positive_int(app_maps.get("current_map_index"))

    def _sync_get_mowing_preferences(
        self,
        include_raw: bool = False,
        map_indices: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """Fetch and decode read-only mower preference settings."""
        result: dict[str, Any] = {
            "source": "app_action_mowing_preferences",
            "available": False,
            "property_hint": MOWING_PREFERENCE_PROPERTY_KEY,
            "maps": [],
            "errors": [],
        }

        for map_index in self._app_map_indices(map_indices):
            entry: dict[str, Any] = {
                "idx": map_index,
                "label": f"map_{map_index}",
                "available": False,
                "preferences": [],
            }
            try:
                info_result = self._sync_call_app_action(
                    {"m": "g", "t": "PREI", "d": {"idx": map_index}}
                )
                if include_raw:
                    entry["raw_info"] = _json_safe(info_result, max_depth=4)
                info = _app_action_data(info_result)
                info_summary = summarize_mowing_preference_info(info)
                entry["mode"] = info_summary.get("mode")
                entry["mode_name"] = info_summary.get("mode_name")
                entry["area_count"] = info_summary.get("area_count")

                areas = info_summary.get("areas")
                if not isinstance(areas, Sequence) or isinstance(
                    areas,
                    str | bytes | bytearray,
                ):
                    areas = []

                preferences: list[dict[str, Any]] = []
                for area in areas:
                    if not isinstance(area, Mapping):
                        continue
                    area_id = _positive_int(area.get("area_id"))
                    if area_id is None:
                        continue
                    preference_result = self._sync_call_app_action(
                        {
                            "m": "g",
                            "t": "PRE",
                            "d": {"idx": map_index, "region": area_id},
                        }
                    )
                    preference_data = _app_action_data(preference_result)
                    if not isinstance(preference_data, Sequence) or isinstance(
                        preference_data,
                        str | bytes | bytearray,
                    ):
                        raise DreameLawnMowerConnectionError(
                            f"PRE returned invalid preference data for map {map_index} "
                            f"area {area_id}."
                        )
                    preference = decode_mowing_preference_payload(preference_data)
                    preference["area_id"] = area_id
                    preference["reported_version"] = area.get("version")
                    if include_raw:
                        preference["raw_response"] = _json_safe(
                            preference_result,
                            max_depth=4,
                        )
                        preference["raw_payload"] = _json_safe(
                            list(preference_data),
                            max_depth=2,
                        )
                    preferences.append(preference)

                entry["preferences"] = preferences
                entry["available"] = bool(preferences)
                if preferences:
                    result["available"] = True
            except Exception as err:  # noqa: BLE001 - keep probing other maps
                entry["error"] = str(err)
                result["errors"].append(
                    {"idx": map_index, "stage": "preferences", "error": str(err)}
                )
            result["maps"].append(entry)

        return result

    def _sync_get_batch_mowing_preferences(
        self,
        include_raw: bool = False,
        map_indices: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """Fetch and decode mower preferences from batch device data."""
        batch_data = self._sync_get_batch_device_data(_batch_settings_keys())
        if batch_data is None:
            return {
                "source": "batch_device_data_mowing_preferences",
                "available": False,
                "property_hint": MOWING_PREFERENCE_PROPERTY_KEY,
                "maps": [],
                "errors": [
                    {
                        "stage": "settings",
                        "error": "Batch device data returned no settings payload.",
                    }
                ],
            }
        return decode_batch_mowing_preferences(
            batch_data,
            include_raw=include_raw,
            map_indices=map_indices,
        )

    def _sync_get_batch_ota_info(
        self,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Fetch and decode OTA state from batch device data."""
        batch_data = self._sync_get_batch_device_data(_batch_ota_keys())
        if batch_data is None:
            return {
                "source": "batch_device_data_ota_info",
                "available": False,
                "ota_info": None,
                "update_available": None,
                "auto_upgrade_enabled": None,
                "errors": [
                    {
                        "stage": "ota",
                        "error": "Batch device data returned no OTA payload.",
                    }
                ],
            }
        return decode_batch_ota_info(batch_data, include_raw=include_raw)

    def _sync_get_debug_ota_catalog(
        self,
        model_name: str | None = None,
        current_version: str | None = None,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Fetch the public debug/manual OTA catalog for the mower model."""
        short_model = _debug_ota_model_name(model_name or self._descriptor.model)
        if not short_model:
            raise DreameLawnMowerConnectionError(
                "Could not determine a short model name for the debug OTA catalog."
            )

        resolved_current_version = current_version
        if resolved_current_version is None:
            try:
                device = self._sync_update_device()
            except DreameLawnMowerConnectionError:
                device = None
            if device is not None:
                resolved_current_version = _as_optional_text(
                    getattr(getattr(device, "info", None), "firmware_version", None)
                )

        url = build_debug_ota_catalog_url(short_model)
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                payload = json.load(response)
        except Exception as err:  # noqa: BLE001 - network/protocol errors vary here
            raise DreameLawnMowerConnectionError(
                f"Debug OTA catalog fetch failed: {err}"
            ) from err

        result = normalize_debug_ota_catalog_payload(
            payload,
            model_name=short_model,
            current_version=resolved_current_version,
            include_raw=include_raw,
        )
        result["url"] = url
        return result

    def _sync_get_weather_protection(
        self,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Fetch read-only weather and rain-protection settings."""
        result: dict[str, Any] = {
            "source": "app_action_weather_protection",
            "available": False,
            "fault_hint": "INFO_BAD_WEATHER_PROTECTING",
            "config_keys": ["WRF", "WRP"],
            "rain_end_time_command": "RPET",
            "errors": [],
            "warnings": [],
        }

        try:
            config_result = self._sync_call_app_action({"m": "g", "t": "CFG"})
            if include_raw:
                result["raw_config"] = _json_safe(config_result, max_depth=4)
            config = _app_action_data(config_result)
            if not isinstance(config, Mapping):
                raise DreameLawnMowerConnectionError(
                    f"CFG returned invalid weather config: {config_result}"
                )
            result["present_config_keys"] = [
                key for key in result["config_keys"] if key in config
            ]
            result.update(_weather_protection_summary(config))
            result["available"] = True
        except Exception as err:  # noqa: BLE001 - diagnostic probe should return evidence
            result["errors"].append({"stage": "config", "error": str(err)})

        try:
            rain_end_result = self._sync_call_app_action({"m": "g", "t": "RPET"})
            if include_raw:
                result["raw_rain_end_time"] = _json_safe(
                    rain_end_result,
                    max_depth=4,
                )
            rain_end_data = _app_action_data(rain_end_result)
            if isinstance(rain_end_data, Mapping):
                end_time = rain_end_data.get("endTime")
                if end_time is None:
                    end_time = rain_end_data.get("end_time")
                if end_time is not None:
                    result["rain_protect_end_time"] = end_time
                    result["rain_protect_end_time_present"] = True
                    result["available"] = True
                else:
                    result["rain_protect_end_time_present"] = False
            elif rain_end_data is None:
                result["rain_protect_end_time_present"] = False
            elif rain_end_data is not None:
                result["warnings"].append(
                    {
                        "stage": "rain_end_time",
                        "warning": f"RPET returned unexpected data: {rain_end_data}",
                    }
                )
        except Exception as err:  # noqa: BLE001 - RPET may only answer while protection is active
            result["warnings"].append({"stage": "rain_end_time", "warning": str(err)})

        result.update(_weather_protection_active_summary(result))
        return result

    def _sync_get_voice_settings(
        self,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Fetch read-only voice and language settings from CFG."""
        result: dict[str, Any] = {
            "source": "app_action_voice_settings",
            "available": False,
            "config_keys": ["LANG", "VOL", "VOICE"],
            "errors": [],
            "warnings": [],
        }

        try:
            config_result = self._sync_call_app_action({"m": "g", "t": "CFG"})
            config = _app_action_data(config_result)
            if not isinstance(config, Mapping):
                weather_result = self._sync_get_weather_protection(include_raw=True)
                weather_raw_config = (
                    weather_result.get("raw_config")
                    if isinstance(weather_result, Mapping)
                    else None
                )
                weather_data = _app_action_data(weather_raw_config)
                if isinstance(weather_data, Mapping):
                    config_result = weather_raw_config
                    config = weather_data
            if include_raw:
                result["raw_config"] = _json_safe(config_result, max_depth=4)
            if not isinstance(config, Mapping):
                raise DreameLawnMowerConnectionError(
                    f"CFG returned invalid voice config: {config_result}"
                )
            result["present_config_keys"] = [
                key for key in result["config_keys"] if key in config
            ]
            result.update(_voice_settings_summary(config))
            result["available"] = True
        except Exception as err:  # noqa: BLE001 - diagnostic probe should return evidence
            result["errors"].append({"stage": "config", "error": str(err)})

        return result

    def _sync_set_voice_language(self, voice_language: int) -> dict[str, Any]:
        """Set the mower voice language and return the confirmed response."""
        request = {
            "m": "s",
            "t": "LANG",
            "d": {
                "type": "voice",
                "value": int(voice_language),
            },
        }
        response = self._sync_call_app_action(request)
        data = _app_action_data(response)
        if not isinstance(data, Mapping):
            raise DreameLawnMowerConnectionError(
                f"LANG voice write returned invalid data: {response}"
            )
        confirmed_voice_language = _as_optional_int(data.get("voice"))
        confirmed_text_language = _as_optional_int(data.get("text"))
        return {
            "source": "app_action_voice_settings_write",
            "action": "set_voice_language",
            "request": _json_safe(request, max_depth=4),
            "response_data": _json_safe(response, max_depth=4),
            "text_language_index": confirmed_text_language,
            "voice_language_index": confirmed_voice_language,
            "voice_language_name": VOICE_LANGUAGE_INDEX_TO_LABEL.get(
                confirmed_voice_language
            ),
            "voice_language_code": VOICE_LANGUAGE_INDEX_TO_CODE.get(
                confirmed_voice_language
            ),
        }

    def _sync_set_voice_volume(self, volume: int) -> dict[str, Any]:
        """Set the mower voice volume and return the confirmed response."""
        if volume < 0 or volume > 100:
            raise ValueError("volume must be between 0 and 100")
        request = {
            "m": "s",
            "t": "VOL",
            "d": {
                "value": int(volume),
            },
        }
        response = self._sync_call_app_action(request)
        data = _app_action_data(response)
        if not isinstance(data, Mapping):
            raise DreameLawnMowerConnectionError(
                f"VOL write returned invalid data: {response}"
            )
        return {
            "source": "app_action_voice_settings_write",
            "action": "set_voice_volume",
            "request": _json_safe(request, max_depth=4),
            "response_data": _json_safe(response, max_depth=4),
            "volume": _as_optional_int(data.get("value")),
        }

    def _sync_set_voice_prompts(self, prompts: Sequence[int]) -> dict[str, Any]:
        """Set the mower voice prompt flags and return the confirmed response."""
        normalized = _normalize_voice_prompt_flags(prompts)
        request = {
            "m": "s",
            "t": "VOICE",
            "d": {
                "value": normalized,
            },
        }
        response = self._sync_call_app_action(request)
        data = _app_action_data(response)
        if not isinstance(data, Mapping):
            raise DreameLawnMowerConnectionError(
                f"VOICE write returned invalid data: {response}"
            )
        confirmed = _normalize_voice_prompt_flags(data.get("value"))
        result = {
            "source": "app_action_voice_settings_write",
            "action": "set_voice_prompts",
            "request": _json_safe(request, max_depth=4),
            "response_data": _json_safe(response, max_depth=4),
            "voice_prompts": confirmed,
        }
        for field_name, enabled in zip(VOICE_PROMPT_FIELDS, confirmed, strict=True):
            result[field_name] = bool(enabled)
        return result

    def _sync_get_app_schedule_text(
        self,
        *,
        size: int,
        version: int,
        chunk_size: int = SCHEDULE_CHUNK_SIZE,
    ) -> tuple[str, int, int]:
        chunks = bytearray()
        offset = 0
        chunk_count = 0
        while offset < size:
            request_size = min(chunk_size, size - offset)
            chunk_result = self._sync_call_app_action(
                {
                    "m": "g",
                    "t": "SCHDDV2",
                    "d": {"s": offset, "l": request_size, "v": version},
                }
            )
            data = _app_action_data(chunk_result)
            if not isinstance(data, Mapping) or "d" not in data:
                raise DreameLawnMowerConnectionError(
                    f"SCHDDV2 returned invalid chunk at offset {offset}."
                )
            text = str(data.get("d") or "")
            encoded = text.encode("utf-8")
            returned_size = _positive_int(data.get("l"))
            if not encoded:
                raise DreameLawnMowerConnectionError(
                    f"SCHDDV2 returned empty data at offset {offset}."
                )
            if len(chunks) + len(encoded) > size:
                raise DreameLawnMowerConnectionError(
                    f"SCHDDV2 returned too much data at offset {offset}."
                )
            chunks.extend(encoded)
            offset += returned_size if returned_size else len(encoded)
            chunk_count += 1
        return chunks.decode("utf-8"), chunk_count, offset

    def _app_schedule_map_indices(
        self,
        map_indices: Sequence[int] | None,
    ) -> list[int]:
        if map_indices is not None:
            return _dedupe_ints(map_indices)
        return _dedupe_ints([-1, *self._app_map_indices(None)])

    def _app_map_indices(
        self,
        map_indices: Sequence[int] | None,
    ) -> list[int]:
        if map_indices is not None:
            return [idx for idx in _dedupe_ints(map_indices) if idx >= 0]
        try:
            map_list_result = self._sync_call_app_action({"m": "g", "t": "MAPL"})
            detected = [
                entry["idx"]
                for entry in _normalize_app_map_entries(map_list_result)
            ]
        except Exception:  # noqa: BLE001 - fall back to the two likely map slots
            detected = [0, 1]
        return _dedupe_ints(detected)

    def _sync_get_app_maps(
        self,
        chunk_size: int = 400,
        include_payload: bool = False,
        include_objects: bool = True,
        include_object_urls: bool = False,
    ) -> dict[str, Any]:
        chunk_size = _validate_app_map_chunk_size(chunk_size)
        map_list_result = self._sync_call_app_action({"m": "g", "t": "MAPL"})
        map_entries = _normalize_app_map_entries(map_list_result)
        result: dict[str, Any] = {
            "source": "app_action_map",
            "available": False,
            "map_count": len(map_entries),
            "current_map_index": None,
            "raw_map_list": _json_safe(map_list_result, max_depth=5),
            "maps": [],
            "errors": [],
        }
        if include_objects:
            try:
                result["objects"] = self._sync_get_app_map_objects(
                    include_urls=include_object_urls,
                )
            except Exception as err:  # noqa: BLE001 - object metadata is diagnostic
                result["objects"] = {"error": str(err)}

        for entry in map_entries:
            if entry.get("current"):
                result["current_map_index"] = entry["idx"]
            if not entry.get("created"):
                result["maps"].append(entry)
                continue

            map_result = dict(entry)
            try:
                info_result = self._sync_call_app_action(
                    {"m": "g", "t": "MAPI", "d": {"idx": entry["idx"]}}
                )
                info = _app_action_data(info_result)
                map_result["info"] = _json_safe(info, max_depth=4)
                size = info.get("size") if isinstance(info, Mapping) else None
                expected_hash = info.get("hash") if isinstance(info, Mapping) else None
                if isinstance(size, int) and size > 0:
                    payload_text, chunk_count, received_size = (
                        self._sync_get_app_map_text(
                            size=size,
                            chunk_size=chunk_size,
                        )
                    )
                    payload_hash = hashlib.md5(
                        payload_text.encode("utf-8")
                    ).hexdigest()
                    parsed_payload = json.loads(payload_text)
                    hash_match = (
                        expected_hash == payload_hash
                        if isinstance(expected_hash, str)
                        else None
                    )
                    if hash_match is False:
                        raise DreameLawnMowerConnectionError(
                            "App map payload hash mismatch."
                        )
                    map_result.update(
                        {
                            "available": True,
                            "reported_size": size,
                            "received_size": received_size,
                            "decoded_size": len(payload_text.encode("utf-8")),
                            "chunk_count": chunk_count,
                            "md5": payload_hash,
                            "hash_match": hash_match,
                            "payload_keys": (
                                sorted(str(key) for key in parsed_payload)
                                if isinstance(parsed_payload, Mapping)
                                else []
                            ),
                            "summary": _app_map_payload_summary(parsed_payload),
                        }
                    )
                    if include_payload:
                        map_result["payload"] = _json_safe(
                            parsed_payload,
                            max_depth=12,
                        )
                else:
                    map_result["available"] = False
                    map_result["error"] = "map_info_missing_size"
            except Exception as err:  # noqa: BLE001 - probes keep per-map evidence
                map_result["available"] = False
                map_result["error"] = str(err)
                result["errors"].append({"idx": entry.get("idx"), "error": str(err)})

            result["maps"].append(map_result)

        result["available"] = any(
            isinstance(item, Mapping) and bool(item.get("available"))
            for item in result["maps"]
        )
        return result

    def _sync_get_app_map_objects(
        self,
        include_urls: bool = False,
    ) -> dict[str, Any]:
        object_result = self._sync_call_app_action(
            {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}}
        )
        data = _app_action_data(object_result)
        names = data.get("name") if isinstance(data, Mapping) else None
        if not isinstance(names, Sequence) or isinstance(
            names,
            str | bytes | bytearray,
        ):
            names = []

        objects: list[dict[str, Any]] = []
        cloud = self._sync_get_cloud_protocol()
        for raw_name in names:
            name = str(raw_name)
            item: dict[str, Any] = {
                "name": name,
                "extension": _app_object_extension(name),
                "url_present": False,
            }
            if include_urls:
                try:
                    url = (
                        cloud.get_interim_file_url(name)
                        if hasattr(cloud, "get_interim_file_url")
                        else None
                    )
                    item["url_present"] = bool(url)
                    item["url"] = url
                except Exception as err:  # noqa: BLE001 - preserve per-object evidence
                    item["error"] = str(err)
            objects.append(item)

        return {
            "source": "app_action_obj_3dmap",
            "object_count": len(objects),
            "objects": objects,
            "raw": _json_safe(object_result, max_depth=4),
            "urls_included": bool(include_urls),
        }

    def _sync_get_app_map_text(
        self,
        *,
        size: int,
        chunk_size: int,
    ) -> tuple[str, int, int]:
        chunks = bytearray()
        offset = 0
        chunk_count = 0
        while offset < size:
            requested_size = min(size - offset, chunk_size)
            chunk_result = self._sync_call_app_action(
                {
                    "m": "g",
                    "t": "MAPD",
                    "d": {"start": offset, "size": requested_size},
                }
            )
            data = _app_action_data(chunk_result)
            if not isinstance(data, Mapping):
                raise DreameLawnMowerConnectionError(
                    f"MAPD returned invalid chunk at offset {offset}."
                )
            text = data.get("data")
            returned_size = data.get("size")
            if not isinstance(text, str) or text == "":
                raise DreameLawnMowerConnectionError(
                    f"MAPD returned empty data at offset {offset}."
                )
            chunk_bytes = text.encode("utf-8")
            actual_size = len(chunk_bytes)
            if actual_size > requested_size:
                raise DreameLawnMowerConnectionError(
                    f"MAPD returned too much data at offset {offset}."
                )
            chunks.extend(chunk_bytes)
            offset += (
                returned_size
                if isinstance(returned_size, int) and returned_size > 0
                else actual_size
            )
            chunk_count += 1
        return chunks.decode("utf-8"), chunk_count, offset

    def _sync_call_app_action(
        self,
        payload: Mapping[str, Any],
        *,
        siid: int = 2,
        aiid: int = 50,
    ) -> Any:
        cloud = self._sync_get_cloud_protocol()
        if not getattr(cloud, "_host", None):
            try:
                if hasattr(cloud, "get_device_info_v2"):
                    cloud.get_device_info_v2("en")
                elif hasattr(cloud, "get_device_info"):
                    cloud.get_device_info()
            except DeviceException as err:
                raise DreameLawnMowerConnectionError(str(err)) from err
        try:
            if hasattr(cloud, "call_app_action"):
                response = cloud.call_app_action(payload, siid=siid, aiid=aiid)
            else:
                response = cloud.send(
                    "action",
                    {
                        "did": str(cloud.device_id),
                        "siid": siid,
                        "aiid": aiid,
                        "in": [payload],
                    },
                )
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

        out = response.get("out") if isinstance(response, Mapping) else None
        if isinstance(out, Sequence) and not isinstance(out, str | bytes | bytearray):
            return out[0] if out else None
        return response

    def _sync_get_cloud_properties(
        self,
        keys: str | Sequence[str],
    ) -> Any:
        cloud = self._sync_get_cloud_protocol()
        normalized_keys = self._normalize_cloud_property_keys(keys)
        try:
            return cloud.get_properties(normalized_keys)
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_get_cloud_property_history(
        self,
        key: str,
        *,
        limit: int = 3,
        time_start: int = 0,
    ) -> Any:
        cloud = self._sync_get_cloud_protocol()
        try:
            return cloud.get_device_property(
                key,
                limit=limit,
                time_start=time_start,
            )
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_scan_cloud_properties(
        self,
        keys: str | Sequence[str] | None,
        siids: Sequence[int] | None,
        piid_start: int,
        piid_end: int,
        chunk_size: int,
        language: str,
        only_values: bool,
        include_key_definition: bool = True,
        key_definition: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_keys = self._build_cloud_property_keys(
            keys=keys,
            siids=siids,
            piid_start=piid_start,
            piid_end=piid_end,
        )
        if not normalized_keys:
            result = {
                "requested_key_count": 0,
                "returned_entry_count": 0,
                "displayed_entry_count": 0,
                "entries": [],
            }
            result["summary"] = build_cloud_property_summary(result)
            return result

        all_entries: list[dict[str, Any]] = []
        for offset in range(0, len(normalized_keys), max(chunk_size, 1)):
            chunk = normalized_keys[offset: offset + max(chunk_size, 1)]
            response = self._sync_get_cloud_properties(chunk)
            all_entries.extend(self._normalize_cloud_property_entries(response))

        cloud_key_definition = key_definition
        if include_key_definition and cloud_key_definition is None:
            try:
                cloud_key_definition = self._sync_get_cloud_key_definition(language)
            except DreameLawnMowerConnectionError:
                cloud_key_definition = None

        rendered = all_entries
        if only_values:
            rendered = [
                entry for entry in rendered if self._entry_has_meaningful_value(entry)
            ]

        rendered = [
            self._annotate_cloud_property_entry(
                entry,
                language=language,
                key_definition=cloud_key_definition,
            )
            for entry in sorted(
                rendered,
                key=lambda item: str(item.get("key", "")),
            )
        ]
        result = {
            "requested_key_count": len(normalized_keys),
            "returned_entry_count": len(all_entries),
            "displayed_entry_count": len(rendered),
            "entries": rendered,
        }
        result["summary"] = build_cloud_property_summary(result)
        return result

    def _sync_get_cloud_device_list_page(
        self,
        current: int,
        size: int,
        language: str | None,
        master: bool | None,
        shared_status: int | None,
    ) -> dict[str, Any] | None:
        cloud = self._sync_get_cloud_protocol()
        try:
            return cloud.get_device_list_v2(
                current=current,
                size=size,
                lang=language,
                master=master,
                shared_status=shared_status,
            )
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

    def _sync_get_cloud_key_definition(
        self,
        language: str | None = None,
        device_info: Mapping[str, Any] | None = None,
        device_list_page: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        cloud = self._sync_get_cloud_protocol()
        device_info = device_info or self._sync_get_cloud_device_info(language) or {}
        key_define = _key_define_from_mapping(device_info)
        source = "device_info"
        if not key_define.get("url"):
            if device_list_page is None:
                try:
                    device_list_page = self._sync_get_cloud_device_list_page(
                        current=1,
                        size=20,
                        language=language,
                        master=None,
                        shared_status=None,
                    )
                except DreameLawnMowerConnectionError:
                    device_list_page = None
            list_key_define = _key_define_from_device_list_page(
                self._descriptor.did,
                device_list_page,
            )
            if list_key_define.get("url"):
                key_define = list_key_define
                source = "device_list_v2"
        url = key_define.get("url") if isinstance(key_define, Mapping) else None
        result: dict[str, Any] = {
            "url": url,
            "url_present": bool(url),
            "ver": key_define.get("ver") if isinstance(key_define, Mapping) else None,
            "source": source if url else None,
            "fetched": False,
            "payload": None,
            "error": None,
        }
        if not url:
            result["error"] = "key_define_url_missing"
            return result

        try:
            content = cloud.get_file(str(url), retry_count=1)
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err
        except Exception as err:
            result["error"] = str(err)
            return result

        if not content:
            result["error"] = "key_definition_fetch_failed"
            return result

        try:
            text = (
                content.decode("utf-8")
                if isinstance(content, bytes)
                else str(content)
            )
            result["payload"] = json.loads(text)
            result["fetched"] = True
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            result["error"] = f"key_definition_parse_failed: {err}"
        return result

    def _sync_probe_map_sources(
        self,
        timeout: float,
        interval: float,
        language: str,
    ) -> dict[str, Any]:
        selected_map_view = self._sync_refresh_map_view(timeout, interval)
        cloud_device_info = self._sync_get_cloud_device_info(language)
        cloud_device_list_page = self._sync_get_cloud_device_list_page(
            current=1,
            size=20,
            language=language,
            master=None,
            shared_status=None,
        )
        try:
            cloud_key_definition = self._sync_get_cloud_key_definition(
                language,
                cloud_device_info,
                cloud_device_list_page,
            )
        except DreameLawnMowerConnectionError as err:
            cloud_key_definition = {"error": str(err)}
        cloud_properties = self._sync_scan_cloud_properties(
            keys=MAP_PROBE_PROPERTY_KEYS,
            siids=None,
            piid_start=1,
            piid_end=1,
            chunk_size=50,
            language=language,
            only_values=False,
            include_key_definition=False,
            key_definition=(
                cloud_key_definition
                if isinstance(cloud_key_definition, Mapping)
                else None
            ),
        )
        cloud_property_history: dict[str, Any] = {}
        for key in MAP_HISTORY_PROPERTY_KEYS:
            try:
                cloud_property_history[key] = self._sync_get_cloud_property_history(
                    key,
                    limit=3,
                    time_start=0,
                )
            except DreameLawnMowerConnectionError as err:
                cloud_property_history[key] = {"error": str(err)}
        try:
            cloud_user_features = self._sync_get_cloud_user_features(language)
        except DreameLawnMowerConnectionError as err:
            cloud_user_features = {"error": str(err)}
        try:
            cloud_device_otc_info = self._sync_get_cloud_device_otc_info(language)
        except DreameLawnMowerConnectionError as err:
            cloud_device_otc_info = {"error": str(err)}
        try:
            app_maps = self._sync_get_app_maps(
                chunk_size=400,
                include_payload=False,
                include_objects=True,
                include_object_urls=False,
            )
        except DreameLawnMowerConnectionError as err:
            app_maps = {"error": str(err)}
        legacy_map_view = self._sync_refresh_legacy_map_view(timeout, interval)
        vector_map_view = self._sync_refresh_vector_map_view()

        return build_map_probe_payload(
            descriptor=self._descriptor,
            map_view=self._map_view_with_cloud_summary(
                selected_map_view, cloud_properties
            ),
            legacy_map_view=self._map_view_with_cloud_summary(
                legacy_map_view, cloud_properties
            ),
            vector_map_view=self._map_view_with_cloud_summary(
                vector_map_view, cloud_properties
            ),
            cloud_properties=cloud_properties,
            cloud_device_info=cloud_device_info,
            cloud_device_list_page=cloud_device_list_page,
            cloud_property_history=cloud_property_history,
            cloud_user_features=cloud_user_features,
            cloud_device_otc_info=cloud_device_otc_info,
            cloud_key_definition=cloud_key_definition,
            app_maps=app_maps,
        )

    def _safe_map_diagnostics(
        self,
        *,
        source: str,
        reason: str | None = None,
        cloud_property_summary: Mapping[str, Any] | None = None,
    ):
        try:
            device = self._ensure_device()
            return map_diagnostics_from_device(
                device,
                source=source,
                reason=reason,
                cloud_property_summary=cloud_property_summary,
            )
        except Exception:
            return None

    def _map_view_with_cloud_summary(
        self,
        map_view: DreameLawnMowerMapView,
        cloud_properties: Mapping[str, Any] | None,
    ) -> DreameLawnMowerMapView:
        from .map_probe import build_cloud_property_summary

        diagnostics = self._safe_map_diagnostics(
            source=map_view.source,
            reason=(
                map_view.diagnostics.reason
                if map_view.diagnostics is not None
                else map_view.error
            ),
            cloud_property_summary=build_cloud_property_summary(cloud_properties),
        )
        return DreameLawnMowerMapView(
            source=map_view.source,
            summary=map_view.summary,
            image_png=map_view.image_png,
            error=map_view.error,
            diagnostics=diagnostics or map_view.diagnostics,
            app_maps=map_view.app_maps,
        )

    def _sync_wait_for_map(self, timeout: float, interval: float):
        device = self._sync_update_device()
        if getattr(device, "current_map", None) is not None:
            return device.current_map

        if getattr(device, "_map_manager", None) is None:
            return None

        try:
            device.update_map()
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

        deadline = time.monotonic() + max(timeout, 0)
        while time.monotonic() <= deadline:
            current_map = getattr(device, "current_map", None)
            if current_map is not None:
                return current_map
            time.sleep(max(interval, 0.1))

        return getattr(device, "current_map", None)

    @staticmethod
    def _normalize_cloud_property_keys(keys: str | Sequence[str]) -> str:
        if isinstance(keys, str):
            return keys
        return ",".join(str(key).strip() for key in keys if str(key).strip())

    @staticmethod
    def _build_cloud_property_keys(
        *,
        keys: str | Sequence[str] | None,
        siids: Sequence[int] | None,
        piid_start: int,
        piid_end: int,
    ) -> list[str]:
        if keys is not None:
            if isinstance(keys, str):
                return [item.strip() for item in keys.split(",") if item.strip()]
            return [str(item).strip() for item in keys if str(item).strip()]

        if piid_end < piid_start:
            raise ValueError("piid_end must be greater than or equal to piid_start")

        normalized_siids = list(siids) if siids is not None else list(range(1, 9))
        return [
            f"{siid}.{piid}"
            for siid in normalized_siids
            for piid in range(piid_start, piid_end + 1)
        ]

    @staticmethod
    def _normalize_cloud_property_entries(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        if isinstance(payload, dict):
            for key in ("data", "result", "records", "list"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _coerce_property_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "on", "yes"}:
                return True
            if normalized in {"false", "0", "off", "no"}:
                return False
        return None

    @staticmethod
    def _entry_has_meaningful_value(entry: dict[str, Any]) -> bool:
        value = entry.get("value")
        if value not in (None, "", [], {}):
            return True

        for nested_key in ("values", "data", "raw", "content"):
            nested = entry.get(nested_key)
            if nested not in (None, "", [], {}):
                return True
        return False

    @staticmethod
    def _property_value_blob_preview(value: Any) -> tuple[int, str] | None:
        raw = value
        if isinstance(raw, str):
            text = raw.strip()
            if not (text.startswith("[") and text.endswith("]")):
                return None
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                return None

        if not isinstance(raw, list) or not raw:
            return None
        if not all(isinstance(item, int) and 0 <= item <= 255 for item in raw):
            return None

        blob = bytes(raw)
        return len(blob), blob.hex()

    @classmethod
    def _annotate_cloud_property_entry(
        cls,
        entry: dict[str, Any],
        *,
        language: str,
        key_definition: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        rendered = dict(entry)
        key = str(rendered.get("key", ""))
        value = rendered.get("value")
        property_hint = MOWER_PROPERTY_HINTS.get(key)
        if property_hint:
            rendered["property_hint"] = property_hint

        label = key_definition_label(
            key_definition,
            key,
            value,
            language=language,
        )
        if label:
            rendered["decoded_label"] = label
            rendered["decoded_label_source"] = "cloud_key_definition"

        if key == MOWER_STATE_PROPERTY_KEY:
            state_key = mower_state_key(value)
            if state_key:
                rendered["state_key"] = state_key
            if not rendered.get("decoded_label"):
                label = mower_state_label(value, language=language)
                if label:
                    rendered["decoded_label"] = label
                    rendered["decoded_label_source"] = "bundled_mower_protocol"
        elif key == MOWER_ERROR_PROPERTY_KEY and not rendered.get("decoded_label"):
            label = mower_error_label(value)
            if label:
                rendered["decoded_label"] = label
                rendered["decoded_label_source"] = "bundled_mower_errors"
        elif key in {MOWER_RAW_STATUS_PROPERTY_KEY, MOWER_RUNTIME_STATUS_PROPERTY_KEY}:
            status_blob = decode_mower_status_blob(value)
            if status_blob is not None:
                rendered["status_blob"] = status_blob.as_dict()
        elif key == MOWER_TASK_PROPERTY_KEY:
            task_status = decode_mower_task_status(value)
            if task_status is not None:
                rendered["task_status"] = task_status

        blob_preview = cls._property_value_blob_preview(value)
        if blob_preview is not None:
            blob_len, blob_hex = blob_preview
            rendered["value_bytes_len"] = blob_len
            rendered["value_bytes_hex"] = blob_hex

        return rendered

    def _sync_get_cloud_protocol(self):
        device = self._ensure_device()
        protocol = getattr(device, "_protocol", None)
        cloud = getattr(protocol, "cloud", None)
        if cloud is None:
            raise DreameLawnMowerConnectionError("Cloud connection is unavailable.")
        if not getattr(cloud, "logged_in", False) and not cloud.login():
            raise DreameLawnMowerConnectionError(
                "Unable to log in to the mower cloud API."
            )
        return cloud

    def _ensure_device(self):
        if self._device is not None:
            return self._device

        from .device import DreameMowerDevice

        self._device = DreameMowerDevice(
            self._descriptor.name,
            self._descriptor.host,
            self._descriptor.token or " ",
            self._descriptor.mac,
            self._username,
            self._password,
            self._country,
            True,
            self._account_type,
            self._descriptor.did,
        )
        return self._device


def _sync_discover_devices(
    username: str,
    password: str,
    country: str,
    account_type: str,
) -> list[DreameLawnMowerDescriptor]:
    if account_type not in SUPPORTED_ACCOUNT_TYPES:
        raise DreameLawnMowerAuthError(f"Unsupported account type: {account_type}")

    from .protocol import DreameMowerProtocol

    protocol = DreameMowerProtocol(
        username=username,
        password=password,
        country=country,
        prefer_cloud=True,
        account_type=account_type,
    )

    try:
        protocol.cloud.login()
    except DeviceException as err:
        raise DreameLawnMowerAuthError(str(err)) from err

    if protocol.cloud.two_factor_url:
        raise DreameLawnMowerTwoFactorRequiredError(protocol.cloud.two_factor_url)

    if not protocol.cloud.logged_in:
        raise DreameLawnMowerAuthError("Unable to log into the Dreame or MOVA cloud.")

    records = protocol.cloud.get_devices()
    if not records:
        return []

    if isinstance(records, dict):
        items = records.get("page", {}).get("records", records)
    else:
        items = records

    found: list[DreameLawnMowerDescriptor] = []
    for record in items:
        descriptor = descriptor_from_cloud_record(
            record,
            account_type=account_type,
            country=country,
        )
        if descriptor is not None:
            found.append(descriptor)

    found.sort(key=lambda item: item.title.lower())
    return found


def _lower_enum_name(value: Any) -> str | None:
    name = getattr(value, "name", None)
    if name:
        return str(name).lower()
    if value is None:
        return None
    text = str(value).strip()
    return text.lower() or None


def _as_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_firmware_description(
    value: Any,
) -> tuple[str | None, bool, Mapping[str, Any] | None]:
    text = _as_optional_text(value)
    if text is None:
        return None, False, None

    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return text, True, None

    if isinstance(parsed, Mapping):
        code = parsed.get("code")
        success = parsed.get("success")
        msg = _as_optional_text(parsed.get("msg"))
        if (isinstance(success, bool) and not success) or (
            isinstance(code, int) and code != 0
        ):
            return None, False, {
                "code": code,
                "success": success,
                "msg": msg,
            }

    return text, True, None


def _normalize_cloud_firmware_check(
    value: Any,
    *,
    current_version: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source": "cloud_check_device_version",
        "available": False,
        "update_available": None,
        "current_version": current_version,
        "latest_version": None,
        "firmware_type": None,
        "force_update": None,
        "status": None,
        "changelog": None,
        "changelog_available": False,
    }
    if not isinstance(value, Mapping):
        result["errors"] = [{"stage": "response", "error": "invalid_response"}]
        return result

    result["available"] = True
    current_version_value = _as_optional_text(value.get("curVersion"))
    if current_version_value is not None:
        result["current_version"] = current_version_value

    latest_version = _as_optional_text(value.get("newVersion"))
    if latest_version is not None:
        result["latest_version"] = latest_version

    firmware_type = _as_optional_text(value.get("firmwareType"))
    if firmware_type is not None:
        result["firmware_type"] = firmware_type

    force_update = value.get("force")
    if isinstance(force_update, bool):
        result["force_update"] = force_update

    result["status"] = value.get("status")

    has_new_firmware = value.get("hasNewFirmware")
    if isinstance(has_new_firmware, bool):
        result["update_available"] = has_new_firmware
    elif (
        latest_version is not None
        and result["current_version"] is not None
        and latest_version != result["current_version"]
    ):
        result["update_available"] = True

    changelog, changelog_available, changelog_error = _parse_firmware_description(
        value.get("description")
    )
    result["changelog"] = changelog
    result["changelog_available"] = changelog_available
    if changelog_error is not None:
        result["changelog_error"] = dict(changelog_error)

    return result


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _setting_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return value != 0
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return bool(value)


def _validate_app_map_chunk_size(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("chunk_size must be an integer")
    if value <= 0:
        raise ValueError("chunk_size must be greater than zero")
    return value


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _epoch_to_iso(value: Any) -> str | None:
    parsed = _positive_int(value)
    if parsed is None:
        return None
    timestamp = parsed / 1000 if parsed > 10_000_000_000 else parsed
    try:
        return datetime.fromtimestamp(timestamp, UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _dedupe_ints(values: Sequence[int]) -> list[int]:
    result: list[int] = []
    for value in values:
        parsed = _positive_int(value)
        if parsed is None and value != -1:
            continue
        parsed = -1 if value == -1 else parsed
        if parsed not in result:
            result.append(parsed)
    return result


def _schedule_entry_overview(schedule: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "idx": schedule.get("idx"),
        "label": schedule.get("label"),
        "available": schedule.get("available"),
        "version": schedule.get("version"),
        "plan_count": schedule.get("plan_count"),
        "enabled_plan_count": schedule.get("enabled_plan_count"),
    }


def _schedule_plan_overview(
    plans: Sequence[Mapping[str, Any]],
    *,
    plan_id: int,
    previous_enabled: bool | None,
    enabled: bool,
) -> dict[str, Any]:
    for plan in plans:
        if _positive_int(plan.get("plan_id")) != plan_id:
            continue

        weeks = plan.get("weeks")
        week_items: list[Mapping[str, Any]] = []
        if isinstance(weeks, Sequence) and not isinstance(
            weeks,
            str | bytes | bytearray,
        ):
            week_items = [week for week in weeks if isinstance(week, Mapping)]
        tasks = [
            task
            for week in week_items
            for task in _schedule_week_tasks(week)
        ]
        type_names = sorted(
            {
                str(task["type_name"])
                for task in tasks
                if task.get("type_name") is not None
            }
        )
        first_task = tasks[0] if tasks else {}
        return {
            "plan_id": plan_id,
            "name": plan.get("name"),
            "previous_enabled": previous_enabled,
            "enabled": enabled,
            "week_count": len(week_items),
            "task_count": len(tasks),
            "first_start_time": first_task.get("start_time"),
            "first_end_time": first_task.get("end_time"),
            "type_names": type_names,
        }

    return {
        "plan_id": plan_id,
        "previous_enabled": previous_enabled,
        "enabled": enabled,
    }


def _schedule_upload_overview(
    plans: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    plan_ids: list[int] = []
    week_count = 0
    task_count = 0
    enabled_plan_count = 0
    for plan in plans:
        plan_id = _positive_int(plan.get("plan_id"))
        if plan_id is not None:
            plan_ids.append(plan_id)
        if plan.get("enabled"):
            enabled_plan_count += 1
        weeks = plan.get("weeks")
        if not isinstance(weeks, Sequence) or isinstance(
            weeks,
            str | bytes | bytearray,
        ):
            continue
        week_count += len(weeks)
        for week in weeks:
            if not isinstance(week, Mapping):
                continue
            tasks = week.get("tasks")
            if isinstance(tasks, Sequence) and not isinstance(
                tasks,
                str | bytes | bytearray,
            ):
                task_count += len(tasks)
    return {
        "plan_count": len(plans),
        "enabled_plan_count": enabled_plan_count,
        "week_count": week_count,
        "task_count": task_count,
        "plan_ids": plan_ids,
    }


def _mowing_preference_map_overview(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "idx": entry.get("idx"),
        "label": entry.get("label"),
        "available": entry.get("available"),
        "mode": entry.get("mode"),
        "mode_name": entry.get("mode_name"),
        "area_count": entry.get("area_count"),
        "preference_count": len(
            [
                item
                for item in entry.get("preferences", [])
                if isinstance(item, Mapping)
            ]
        ),
    }


def _mowing_preference_overview(preference: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "reported_version": preference.get("reported_version"),
        "version": preference.get("version"),
        "map_index": preference.get("map_index"),
        "area_id": preference.get("area_id"),
        "efficient_mode": preference.get("efficient_mode"),
        "efficient_mode_name": preference.get("efficient_mode_name"),
        "mowing_height_cm": preference.get("mowing_height_cm"),
        "mowing_direction_mode": preference.get("mowing_direction_mode"),
        "mowing_direction_mode_name": preference.get("mowing_direction_mode_name"),
        "mowing_direction_degrees": preference.get("mowing_direction_degrees"),
        "edge_mowing_auto": preference.get("edge_mowing_auto"),
        "edge_mowing_walk_mode": preference.get("edge_mowing_walk_mode"),
        "edge_mowing_walk_mode_name": preference.get("edge_mowing_walk_mode_name"),
        "edge_mowing_obstacle_avoidance": preference.get(
            "edge_mowing_obstacle_avoidance"
        ),
        "cutter_position": preference.get("cutter_position"),
        "cutter_position_name": preference.get("cutter_position_name"),
        "edge_mowing_num": preference.get("edge_mowing_num"),
        "obstacle_avoidance_enabled": preference.get("obstacle_avoidance_enabled"),
        "obstacle_avoidance_height_cm": preference.get(
            "obstacle_avoidance_height_cm"
        ),
        "obstacle_avoidance_distance_cm": preference.get(
            "obstacle_avoidance_distance_cm"
        ),
        "obstacle_avoidance_ai": preference.get("obstacle_avoidance_ai"),
        "obstacle_avoidance_ai_classes": preference.get(
            "obstacle_avoidance_ai_classes"
        ),
        "edge_mowing_safe": preference.get("edge_mowing_safe"),
    }


def _schedule_week_tasks(week: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    tasks = week.get("tasks")
    if not isinstance(tasks, Sequence) or isinstance(
        tasks,
        str | bytes | bytearray,
    ):
        return []
    return [task for task in tasks if isinstance(task, Mapping)]


def _app_action_data(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return None
    if value.get("r") not in (None, 0):
        raise DreameLawnMowerConnectionError(f"App action failed: {value}")
    return value.get("d")


def _app_object_extension(value: str) -> str | None:
    name = value.rsplit("/", 1)[-1]
    if "." not in name:
        return None
    extension = name.rsplit(".", 1)[-1].strip()
    return extension or None


def _normalize_app_map_entries(value: Any) -> list[dict[str, Any]]:
    entries = _app_action_data(value)
    if not isinstance(entries, Sequence) or isinstance(
        entries,
        str | bytes | bytearray,
    ):
        return []

    result: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, Sequence) or isinstance(item, str | bytes | bytearray):
            continue
        values = list(item)
        if len(values) < 4:
            continue
        result.append(
            {
                "idx": values[0],
                "current": bool(values[1]),
                "created": bool(values[2]),
                "has_backup": bool(values[3]),
                "force_load": bool(values[4]) if len(values) > 4 else False,
            }
        )
    return result


def _app_map_payload_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"payload_type": _operation_value_type(value)}

    maps = value.get("map") if isinstance(value.get("map"), list) else []
    spots = value.get("spot") if isinstance(value.get("spot"), list) else []
    point_entries = value.get("point") if isinstance(value.get("point"), list) else []
    semantic = value.get("semantic") if isinstance(value.get("semantic"), list) else []
    trajectories = (
        value.get("trajectory") if isinstance(value.get("trajectory"), list) else []
    )
    cut_relation = (
        value.get("cut_relation") if isinstance(value.get("cut_relation"), list) else []
    )

    boundary_point_count = 0
    spot_boundary_point_count = 0
    semantic_boundary_point_count = 0
    trajectory_point_count = 0
    trajectory_length_m = 0.0
    semantic_key_counts: dict[str, int] = {}
    total_area = value.get("total_area")
    map_area_total = 0.0
    for item in maps:
        if not isinstance(item, Mapping):
            continue
        coordinates = item.get("data")
        if isinstance(coordinates, Sequence) and not isinstance(
            coordinates,
            str | bytes | bytearray,
        ):
            boundary_point_count += len(coordinates)
        area = item.get("area")
        if isinstance(area, int | float):
            map_area_total += float(area)
    for item in spots:
        if not isinstance(item, Mapping):
            continue
        coordinates = item.get("data")
        if isinstance(coordinates, Sequence) and not isinstance(
            coordinates,
            str | bytes | bytearray,
        ):
            spot_boundary_point_count += len(coordinates)
    for item in semantic:
        if not isinstance(item, Mapping):
            continue
        for key in item:
            semantic_key_counts[str(key)] = semantic_key_counts.get(str(key), 0) + 1
        coordinates = item.get("data")
        if isinstance(coordinates, Sequence) and not isinstance(
            coordinates,
            str | bytes | bytearray,
        ):
            semantic_boundary_point_count += len(coordinates)
    for item in trajectories:
        if not isinstance(item, Mapping):
            continue
        coordinates = item.get("data")
        if isinstance(coordinates, Sequence) and not isinstance(
            coordinates,
            str | bytes | bytearray,
        ):
            trajectory_points = _app_map_points(coordinates)
            trajectory_point_count += len(trajectory_points)
            trajectory_length_m += _coordinate_path_length_m(trajectory_points)

    return {
        "name": value.get("name"),
        "total_area": total_area,
        "map_area_total": round(map_area_total, 2),
        "map_area_count": len(maps),
        "boundary_point_count": boundary_point_count,
        "spot_count": len(spots),
        "spot_boundary_point_count": spot_boundary_point_count,
        "point_count": len(point_entries),
        "semantic_count": len(semantic),
        "semantic_boundary_point_count": semantic_boundary_point_count,
        "semantic_key_counts": dict(sorted(semantic_key_counts.items())),
        "trajectory_count": len(trajectories),
        "trajectory_point_count": trajectory_point_count,
        "trajectory_length_m": round(trajectory_length_m, 2),
        "cut_relation_count": len(cut_relation),
    }


def _select_app_map_payload(app_maps: Mapping[str, Any]) -> Mapping[str, Any] | None:
    maps = app_maps.get("maps") if isinstance(app_maps, Mapping) else None
    if not isinstance(maps, Sequence) or isinstance(maps, str | bytes | bytearray):
        return None
    current_idx = app_maps.get("current_map_index")
    available_maps = [
        item
        for item in maps
        if isinstance(item, Mapping)
        and bool(item.get("available"))
        and isinstance(item.get("payload"), Mapping)
    ]
    for item in available_maps:
        if item.get("idx") == current_idx:
            return item
    return available_maps[0] if available_maps else None


def _vector_map_batch_keys() -> list[str]:
    keys = [*(f"MAP.{index}" for index in range(40)), "MAP.info"]
    keys.extend(f"M_PATH.{index}" for index in range(10))
    keys.append("M_PATH.info")
    return keys


def _batch_schedule_keys() -> list[str]:
    return [*(f"SCHEDULE.{index}" for index in range(10)), "SCHEDULE.info"]


def _batch_settings_keys() -> list[str]:
    return [*(f"SETTINGS.{index}" for index in range(10)), "SETTINGS.info"]


def _batch_ota_keys() -> list[str]:
    return [
        *(f"OTA_INFO.{index}" for index in range(4)),
        "OTA_INFO.info",
        "prop.s_auto_upgrade",
    ]


def _debug_ota_model_name(model_name: Any) -> str | None:
    text = _as_optional_text(model_name)
    if not text:
        return None
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.lower()


def _weather_protection_summary(config: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    weather_switch = _setting_bool(config.get("WRF"))
    if weather_switch is not None:
        summary["weather_switch_enabled"] = weather_switch

    wrp = config.get("WRP")
    if isinstance(wrp, Sequence) and not isinstance(wrp, str | bytes | bytearray):
        values = list(wrp)
        if len(values) == 2:
            values.append(0)
        summary["rain_protection_raw"] = _json_safe(values, max_depth=2)
        if values:
            summary["rain_protection_enabled"] = _setting_bool(values[0])
        if len(values) > 1:
            summary["rain_protection_duration_hours"] = _positive_int(values[1])
        if len(values) > 2:
            summary["rain_sensor_sensitivity"] = _positive_int(values[2])

    end_time = config.get("rainProtectEndTime")
    if end_time is not None:
        summary["rain_protect_end_time"] = end_time
        summary["rain_protect_end_time_present"] = True
    return {
        key: value
        for key, value in summary.items()
        if value is not None
    }


def _weather_protection_active_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    end_time = result.get("rain_protect_end_time")
    end_time_present = bool(result.get("rain_protect_end_time_present"))

    if end_time_present:
        summary["rain_protection_active"] = True
        end_time_iso = _epoch_to_iso(end_time)
        if end_time_iso is not None:
            summary["rain_protect_end_time_iso"] = end_time_iso
    elif result.get("available"):
        summary["rain_protection_active"] = False

    return summary


def _voice_settings_summary(config: Mapping[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    language = config.get("LANG")
    if isinstance(language, Sequence) and not isinstance(
        language,
        str | bytes | bytearray,
    ):
        values = list(language)
        text_language_index = (
            _as_optional_int(values[0]) if len(values) > 0 else None
        )
        voice_language_index = (
            _as_optional_int(values[1]) if len(values) > 1 else None
        )
        summary["text_language_index"] = text_language_index
        summary["voice_language_index"] = voice_language_index
        summary["voice_language_name"] = VOICE_LANGUAGE_INDEX_TO_LABEL.get(
            voice_language_index
        )
        summary["voice_language_code"] = VOICE_LANGUAGE_INDEX_TO_CODE.get(
            voice_language_index
        )

    volume = _as_optional_int(config.get("VOL"))
    if volume is not None:
        summary["volume"] = volume

    voice_prompts = _normalize_voice_prompt_flags(config.get("VOICE"))
    summary["voice_prompts"] = voice_prompts
    for field_name, enabled in zip(VOICE_PROMPT_FIELDS, voice_prompts, strict=True):
        summary[field_name] = bool(enabled)

    return summary


def _app_maps_view_metadata(app_maps: Mapping[str, Any]) -> dict[str, Any]:
    maps = app_maps.get("maps") if isinstance(app_maps, Mapping) else None
    if not isinstance(maps, Sequence) or isinstance(maps, str | bytes | bytearray):
        maps = []

    entries = [
        _app_map_entry_view_metadata(item)
        for item in maps
        if isinstance(item, Mapping)
    ]
    objects = _app_map_objects_view_metadata(app_maps.get("objects"))
    return {
        "source": app_maps.get("source"),
        "available": bool(app_maps.get("available")),
        "map_count": app_maps.get("map_count", len(entries)),
        "current_map_index": app_maps.get("current_map_index"),
        "available_map_count": sum(1 for item in entries if item.get("available")),
        "created_map_count": sum(1 for item in entries if item.get("created")),
        "maps": entries,
        "objects": objects.get("objects"),
        "object_count": objects.get("object_count"),
        "object_error": objects.get("error"),
        "error_count": len(app_maps.get("errors", []) or []),
    }


def _app_map_objects_view_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"objects": None, "object_count": None, "error": None}
    objects = value.get("objects")
    if not isinstance(objects, Sequence) or isinstance(
        objects,
        str | bytes | bytearray,
    ):
        objects = []
    entries = [
        {
            key: item.get(key)
            for key in ("name", "extension", "url_present", "error")
            if item.get(key) is not None
        }
        for item in objects
        if isinstance(item, Mapping)
    ]
    return {
        "objects": entries,
        "object_count": value.get("object_count", len(entries)),
        "error": value.get("error"),
    }


def _app_map_entry_view_metadata(entry: Mapping[str, Any]) -> dict[str, Any]:
    summary = entry.get("summary") if isinstance(entry.get("summary"), Mapping) else {}
    info = entry.get("info") if isinstance(entry.get("info"), Mapping) else {}
    result = {
        "idx": entry.get("idx"),
        "current": bool(entry.get("current")),
        "created": bool(entry.get("created")),
        "available": bool(entry.get("available")),
        "has_backup": bool(entry.get("has_backup")),
        "force_load": bool(entry.get("force_load")),
        "reported_size": entry.get("reported_size") or info.get("size"),
        "received_size": entry.get("received_size"),
        "chunk_count": entry.get("chunk_count"),
        "hash_match": entry.get("hash_match"),
        "payload_keys": entry.get("payload_keys"),
        "name": summary.get("name"),
        "total_area": summary.get("total_area"),
        "map_area_count": summary.get("map_area_count"),
        "map_area_total": summary.get("map_area_total"),
        "boundary_point_count": summary.get("boundary_point_count"),
        "spot_count": summary.get("spot_count"),
        "point_count": summary.get("point_count"),
        "trajectory_count": summary.get("trajectory_count"),
        "trajectory_point_count": summary.get("trajectory_point_count"),
        "trajectory_length_m": summary.get("trajectory_length_m"),
        "semantic_count": summary.get("semantic_count"),
        "cut_relation_count": summary.get("cut_relation_count"),
        "error": entry.get("error"),
    }
    return {
        key: value
        for key, value in result.items()
        if value not in (None, [], {})
    }


def _app_map_view_summary(
    selected: Mapping[str, Any],
    payload: Any,
    width: int,
    height: int,
) -> DreameLawnMowerMapSummary:
    payload_summary = _app_map_payload_summary(payload)
    map_id = selected.get("idx")
    return DreameLawnMowerMapSummary(
        available=True,
        map_id=map_id if isinstance(map_id, int) else None,
        width=width,
        height=height,
        saved_map=bool(selected.get("created")),
        segment_count=int(payload_summary.get("map_area_count") or 0),
        active_area_count=int(payload_summary.get("map_area_count") or 0),
        active_point_count=int(payload_summary.get("point_count") or 0),
        path_point_count=int(payload_summary.get("trajectory_point_count") or 0),
        spot_area_count=int(payload_summary.get("spot_count") or 0),
    )


def _app_map_view_details(
    selected: Mapping[str, Any],
    payload: Any,
) -> dict[str, Any]:
    payload_summary = _app_map_payload_summary(payload)
    return {
        "map_name": payload_summary.get("name"),
        "map_index": selected.get("idx"),
        "total_area": payload_summary.get("total_area"),
        "map_area_total": payload_summary.get("map_area_total"),
        "zone_count": payload_summary.get("map_area_count"),
        "spot_area_count": payload_summary.get("spot_count"),
        "clean_point_count": payload_summary.get("point_count"),
        "trajectory_count": payload_summary.get("trajectory_count"),
        "trajectory_point_count": payload_summary.get("trajectory_point_count"),
        "trajectory_length_m": payload_summary.get("trajectory_length_m"),
        "cut_relation_count": payload_summary.get("cut_relation_count"),
        "has_live_path": bool(payload_summary.get("trajectory_point_count")),
        "current": bool(selected.get("current")),
        "created": bool(selected.get("created")),
    }


def render_app_map_payload_png(payload: Any) -> tuple[bytes, int, int]:
    """Render a mower-native app map payload to PNG bytes."""
    return _render_app_map_payload_png(payload)


def _render_app_map_payload_png(payload: Any) -> tuple[bytes, int, int]:
    if not isinstance(payload, Mapping):
        raise ValueError("App map payload is missing.")

    map_polygons = _app_map_coordinate_sets(payload.get("map"))
    spot_polygons = _app_map_coordinate_sets(payload.get("spot"))
    trajectories = _app_map_coordinate_sets(payload.get("trajectory"))
    points = _app_map_points(payload.get("point"))
    all_points = [
        point
        for group in [*map_polygons, *spot_polygons, *trajectories, points]
        for point in group
    ]
    if not all_points:
        raise ValueError("App map payload does not contain drawable coordinates.")

    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_y = max(point[1] for point in all_points)
    span_x = max(max_x - min_x, 1)
    span_y = max(max_y - min_y, 1)
    padding = 48
    canvas = 900
    scale = min((canvas - padding * 2) / span_x, (canvas - padding * 2) / span_y)
    width = max(int(span_x * scale) + padding * 2, 320)
    height = max(int(span_y * scale) + padding * 2, 320)

    def project(point: tuple[float, float]) -> tuple[int, int]:
        x, y = point
        return (
            int(round((x - min_x) * scale + padding)),
            int(round((max_y - y) * scale + padding)),
        )

    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (width, height), (248, 250, 252, 255))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for polygon in sorted(map_polygons, key=len, reverse=True):
        projected = [project(point) for point in polygon]
        if len(projected) >= 3:
            draw.polygon(
                projected,
                fill=(187, 230, 197, 150),
                outline=(44, 125, 83, 255),
            )
            draw.line(projected + [projected[0]], fill=(44, 125, 83, 255), width=4)

    for polygon in spot_polygons:
        projected = [project(point) for point in polygon]
        if len(projected) >= 3:
            draw.polygon(
                projected,
                fill=(250, 204, 21, 95),
                outline=(161, 98, 7, 255),
            )
            draw.line(projected + [projected[0]], fill=(161, 98, 7, 255), width=3)

    for trajectory in trajectories:
        projected = [project(point) for point in trajectory]
        if len(projected) >= 2:
            draw.line(projected, fill=(37, 99, 235, 255), width=4, joint="curve")

    for point in points:
        x, y = project(point)
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(15, 23, 42, 255))

    image = Image.alpha_composite(image, overlay).convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), width, height


def _app_map_coordinate_sets(value: Any) -> list[list[tuple[float, float]]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    result: list[list[tuple[float, float]]] = []
    for item in value:
        data = item.get("data") if isinstance(item, Mapping) else item
        points = _app_map_points(data)
        if points:
            result.append(points)
    return result


def _app_map_points(value: Any) -> list[tuple[float, float]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    points: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, Sequence) or isinstance(item, str | bytes | bytearray):
            continue
        if len(item) < 2:
            continue
        x, y = item[0], item[1]
        if isinstance(x, int | float) and isinstance(y, int | float):
            points.append((float(x), float(y)))
    return points


def _coordinate_path_length_m(points: Sequence[tuple[float, float]]) -> float:
    """Return an approximate path length in meters for centimeter coordinates."""
    if len(points) < 2:
        return 0.0

    total = 0.0
    previous = points[0]
    for current in points[1:]:
        total += math.hypot(current[0] - previous[0], current[1] - previous[1])
        previous = current
    return total / 100.0


def _runtime_blob_position(
    blob: DreameLawnMowerStatusBlob | None,
) -> tuple[int, int] | None:
    if blob is None:
        return None
    x = getattr(blob, "candidate_runtime_pose_x", None)
    y = getattr(blob, "candidate_runtime_pose_y", None)
    if isinstance(x, int) and not isinstance(x, bool) and isinstance(y, int) and not isinstance(y, bool):
        return (x, y)
    return None


def _key_define_from_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    key_define = value.get("keyDefine")
    return key_define if isinstance(key_define, Mapping) else {}


def _device_list_records(value: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    result = value.get("result", value)
    page = result.get("page", result) if isinstance(result, Mapping) else {}
    records = page.get("records", []) if isinstance(page, Mapping) else []
    return [record for record in records if isinstance(record, Mapping)]


def _key_define_from_device_list_page(
    did: str,
    device_list_page: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    for record in _device_list_records(device_list_page):
        if record.get("did") == did:
            return _key_define_from_mapping(record)
    return {}


def _protocol_mapping_summary(
    mapping: Mapping[Any, Any],
    members: Mapping[str, Any],
) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for name, member in members.items():
        value = mapping.get(member)
        if isinstance(value, Mapping):
            summary[name] = {
                str(key): int(item)
                for key, item in value.items()
                if isinstance(item, int)
            }
    return summary


def _safe_get_device_property(device: Any, prop: Any) -> Any:
    get_property = getattr(device, "get_property", None)
    if get_property is None:
        return None
    try:
        return get_property(prop)
    except Exception:
        return None


def _camera_feature_advertised(
    *,
    camera_streaming: bool,
    camera_light: bool | None,
    ai_detection: bool,
    obstacles: bool,
    permit: str | None,
    feature: str | None,
    live_key_define: Any,
    video_status: Any,
) -> bool:
    permit_tokens = {
        item.strip().casefold()
        for item in (permit or "").split(",")
        if item.strip()
    }
    return bool(
        camera_streaming
        or camera_light is not None
        or ai_detection
        or obstacles
        or "video" in permit_tokens
        or "aiobs" in permit_tokens
        or "video" in (feature or "").casefold()
        or (isinstance(live_key_define, Mapping) and bool(live_key_define))
        or video_status is not None
    )


def _validate_stream_operation(operation: str) -> str:
    if not isinstance(operation, str):
        raise ValueError("operation must be a string")
    value = operation.strip()
    if value not in {"monitor"}:
        raise ValueError("operation must be 'monitor'")
    return value


def _validate_stream_payload_mode(payload_mode: str) -> str:
    if not isinstance(payload_mode, str):
        raise ValueError("payload_mode must be a string")
    value = payload_mode.strip()
    if value not in {"with_session", "no_session", "empty_session"}:
        raise ValueError(
            "payload_mode must be one of: with_session, no_session, empty_session"
        )
    return value


def _cloud_user_feature_summary(value: Any) -> Mapping[str, Any]:
    safe = _json_safe(value, max_depth=3)
    if isinstance(safe, Mapping):
        interesting = {
            key: safe[key]
            for key in (
                "feature",
                "features",
                "permit",
                "permits",
                "video",
                "videoStatus",
            )
            if key in safe
        }
        return {
            "type": "dict",
            "keys": sorted(str(key) for key in safe.keys()),
            "interesting": interesting,
        }
    if isinstance(safe, list):
        return {"type": "list", "length": len(safe), "items": safe[:10]}
    return {"type": type(value).__name__, "value": safe}


def _operation_snapshot_summary(snapshot: DreameLawnMowerSnapshot) -> dict[str, Any]:
    """Return a compact, stable snapshot for field-test logs."""
    raw_attributes = snapshot.raw_attributes or {}
    return {
        "device": snapshot.descriptor.title,
        "descriptor": {
            "did": snapshot.descriptor.did,
            "name": snapshot.descriptor.name,
            "model": snapshot.descriptor.model,
            "display_model": snapshot.descriptor.display_model,
            "account_type": snapshot.descriptor.account_type,
            "country": snapshot.descriptor.country,
            "host_present": bool(snapshot.descriptor.host),
            "token_present": bool(snapshot.descriptor.token),
        },
        "available": snapshot.available,
        "online": snapshot.online,
        "state": snapshot.state,
        "state_name": snapshot.state_name,
        "activity": snapshot.activity,
        "task_status": snapshot.task_status,
        "task_status_name": snapshot.task_status_name,
        "battery_level": snapshot.battery_level,
        "charging": snapshot.charging,
        "raw_charging": snapshot.raw_charging,
        "docked": snapshot.docked,
        "raw_docked": snapshot.raw_docked,
        "started": snapshot.started,
        "raw_started": snapshot.raw_started,
        "mowing": snapshot.mowing,
        "paused": snapshot.paused,
        "returning": snapshot.returning,
        "raw_returning": snapshot.raw_returning,
        "scheduled_clean": snapshot.scheduled_clean,
        "shortcut_task": snapshot.shortcut_task,
        "mapping_available": snapshot.mapping_available,
        "error_code": snapshot.error_code,
        "error_name": snapshot.error_name,
        "error_text": snapshot.error_text,
        "error_display": snapshot.error_display,
        "error_source": snapshot.error_source,
        "raw_error_code": snapshot.raw_error_code,
        "realtime_error_code": snapshot.realtime_error_code,
        "child_lock": snapshot.child_lock,
        "cleaning_mode": snapshot.cleaning_mode,
        "cleaning_mode_name": snapshot.cleaning_mode_name,
        "capabilities": list(snapshot.capabilities),
        "firmware_version": snapshot.firmware_version,
        "hardware_version": snapshot.hardware_version,
        "serial_number": snapshot.serial_number,
        "cloud_update_time": snapshot.cloud_update_time,
        "unknown_property_count": snapshot.unknown_property_count,
        "realtime_property_count": snapshot.realtime_property_count,
        "last_realtime_method": snapshot.last_realtime_method,
        "manual_drive_safe": remote_control_state_safe(snapshot),
        "manual_drive_block_reason": remote_control_block_reason(snapshot),
        "raw_state_signals": _json_safe(
            {
                key: raw_attributes.get(key)
                for key in (
                    "mower_state",
                    "status",
                    "error",
                    "charging",
                    "docked",
                    "started",
                    "running",
                    "paused",
                    "returning",
                    "mapping",
                    "fast_mapping",
                    "has_saved_map",
                    "has_temporary_map",
                )
                if key in raw_attributes
            }
        ),
    }


def _operation_property_summary(
    properties: Mapping[Any, Any],
    *,
    unknown_prefix: str | None = None,
) -> dict[str, Any]:
    """Return compact live property evidence for operation snapshots."""
    entries: list[dict[str, Any]] = []
    known_keys: list[str] = []
    unknown_keys: list[str] = []
    value_type_counts: dict[str, int] = {}

    for key, value in properties.items():
        key_text = str(key)
        payload = value if isinstance(value, Mapping) else {}
        property_hint = mower_property_hint(key_text)
        property_name = mower_realtime_property_name(
            key_text, payload.get("property_name")
        )
        property_value = payload.get("value") if isinstance(value, Mapping) else value
        value_type = _operation_value_type(property_value)
        value_type_counts[value_type] = value_type_counts.get(value_type, 0) + 1

        if unknown_prefix is not None:
            if property_name.startswith(unknown_prefix):
                unknown_keys.append(key_text)
            else:
                known_keys.append(key_text)

        status_blob = None
        status_blob_keys = {
            MOWER_RAW_STATUS_PROPERTY_KEY,
            MOWER_RUNTIME_STATUS_PROPERTY_KEY,
        }
        if key_text in status_blob_keys:
            decoded = decode_mower_status_blob(property_value, source="operation")
            status_blob = decoded.as_dict() if decoded is not None else None
        task_status = None
        if key_text == MOWER_TASK_PROPERTY_KEY:
            task_status = decode_mower_task_status(property_value)

        entry = {
            "key": key_text,
            "property_name": property_name or None,
            "siid": _json_safe(payload.get("siid")),
            "piid": _json_safe(payload.get("piid")),
            "code": _json_safe(payload.get("code")),
            "value_type": value_type,
            "value_preview": _operation_short_preview(property_value),
            "status_blob": status_blob,
            "task_status": task_status,
        }
        if property_hint is not None:
            entry["property_hint"] = property_hint
        entries.append(entry)

    entries.sort(key=lambda item: item["key"])
    known_keys.sort()
    unknown_keys.sort()
    summary: dict[str, Any] = {
        "count": len(entries),
        "value_type_counts": value_type_counts,
        "entries": entries[:30],
    }
    if unknown_prefix is not None:
        summary["known_keys"] = known_keys
        summary["unknown_keys"] = unknown_keys
    return summary


def _operation_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return "array"
    return type(value).__name__


def _operation_short_preview(value: Any) -> Any:
    normalized = _json_safe(value, max_depth=3)
    if isinstance(normalized, str):
        return normalized if len(normalized) <= 120 else f"{normalized[:117]}..."
    if isinstance(normalized, list):
        preview = normalized[:10]
        if len(normalized) > 10:
            preview.append(f"... +{len(normalized) - 10} items")
        return preview
    if isinstance(normalized, Mapping):
        return {key: normalized[key] for key in list(normalized.keys())[:10]}
    return normalized


def _map_view_has_live_path(map_view: DreameLawnMowerMapView) -> bool:
    """Return whether a map view exposes an active live mowing trail."""
    if not map_view.available or map_view.image_png is None:
        return False

    details = map_view.details
    if not isinstance(details, Mapping):
        return False

    return bool(details.get("has_live_path"))


def _normalize_contour_ids(
    contour_ids: Sequence[Sequence[int]],
) -> list[list[int]]:
    result: list[list[int]] = []
    for contour_id in contour_ids:
        if len(contour_id) < 2:
            continue
        result.append([int(contour_id[0]), int(contour_id[1])])
    return result


def _json_safe(value: Any, *, max_depth: int = 4) -> Any:
    if max_depth < 0:
        return str(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, max_depth=max_depth - 1)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item, max_depth=max_depth - 1) for item in value]
    name = getattr(value, "name", None)
    if name is not None:
        return str(name).lower()
    return str(value)


def _as_optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_voice_prompt_flags(value: Any) -> list[int]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return [0, 0, 0, 0]
    normalized: list[int] = []
    for item in list(value)[:4]:
        normalized.append(1 if bool(item) else 0)
    if len(normalized) < 4:
        normalized.extend([0] * (4 - len(normalized)))
    return normalized


def _validate_remote_control_step(*, rotation: int, velocity: int) -> None:
    if not isinstance(rotation, int) or isinstance(rotation, bool):
        raise ValueError("rotation must be an integer")
    if not isinstance(velocity, int) or isinstance(velocity, bool):
        raise ValueError("velocity must be an integer")
    if abs(rotation) > REMOTE_CONTROL_MAX_ROTATION:
        raise ValueError(
            f"rotation must be between {-REMOTE_CONTROL_MAX_ROTATION} and "
            f"{REMOTE_CONTROL_MAX_ROTATION}"
        )
    if abs(velocity) > REMOTE_CONTROL_MAX_VELOCITY:
        raise ValueError(
            f"velocity must be between {-REMOTE_CONTROL_MAX_VELOCITY} and "
            f"{REMOTE_CONTROL_MAX_VELOCITY}"
        )
