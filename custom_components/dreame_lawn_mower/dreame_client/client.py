"""Async-friendly reusable mower client facade."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

from .app_protocol import (
    MOWER_ERROR_PROPERTY_KEY,
    MOWER_PROPERTY_HINTS,
    MOWER_RAW_STATUS_PROPERTY_KEY,
    MOWER_STATE_PROPERTY_KEY,
    decode_mower_status_blob,
    key_definition_label,
    mower_error_label,
    mower_state_label,
)
from .camera_probe import CAMERA_PROBE_PROPERTY_KEYS, build_camera_probe_payload
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

REMOTE_CONTROL_MAX_ROTATION = 1000
REMOTE_CONTROL_MAX_VELOCITY = 1000


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

    @property
    def descriptor(self) -> DreameLawnMowerDescriptor:
        """Return the selected mower descriptor."""
        return self._descriptor

    @property
    def device(self) -> Any | None:
        """Return the currently connected upstream device instance."""
        return self._device

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
        include_object_urls: bool = False,
    ) -> dict[str, Any]:
        """Fetch mower-native app map payloads through read-only app commands."""
        return await asyncio.to_thread(
            self._sync_get_app_maps,
            chunk_size,
            include_payload,
            include_object_urls,
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
            except DreameLawnMowerConnectionError as err:
                cloud_error = str(err)

        return firmware_update_support_from_device(
            device,
            cloud_device_info=cloud_device_info,
            cloud_device_list_page=cloud_device_list_page,
            cloud_error=cloud_error,
        )

    def _sync_get_status_blob(
        self,
        refresh: bool = False,
        include_cloud: bool = True,
    ) -> DreameLawnMowerStatusBlob | None:
        if refresh:
            device = self._sync_update_device()
        else:
            device = self._ensure_device()

        realtime_entry = (getattr(device, "realtime_properties", {}) or {}).get(
            MOWER_RAW_STATUS_PROPERTY_KEY
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

        response = self._sync_get_cloud_properties(MOWER_RAW_STATUS_PROPERTY_KEY)
        for entry in self._normalize_cloud_property_entries(response):
            if str(entry.get("key", "")) == MOWER_RAW_STATUS_PROPERTY_KEY:
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
        map_data = self._sync_wait_for_map(timeout, interval)
        return map_summary_from_map_data(map_data)

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
        source = "legacy_current_map"
        try:
            map_data = self._sync_wait_for_map(timeout, interval)
        except DreameLawnMowerConnectionError as err:
            error = str(err)
            return self._sync_refresh_app_map_view(
                legacy_error=error,
                legacy_reason=error,
            )

        if map_data is None:
            error = "No map data returned by the legacy current-map path."
            return self._sync_refresh_app_map_view(
                legacy_error=error,
                legacy_reason="legacy_current_map_empty",
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
            )
            selected = _select_app_map_payload(app_maps)
            if selected is None:
                error = legacy_error or "No app-map payload was returned."
                return DreameLawnMowerMapView(
                    source=source,
                    error=error,
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

    def _sync_get_app_maps(
        self,
        chunk_size: int = 400,
        include_payload: bool = False,
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
                    map_result.update(
                        {
                            "available": True,
                            "reported_size": size,
                            "received_size": received_size,
                            "decoded_size": len(payload_text.encode("utf-8")),
                            "chunk_count": chunk_count,
                            "md5": payload_hash,
                            "hash_match": (
                                expected_hash == payload_hash
                                if isinstance(expected_hash, str)
                                else None
                            ),
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
        if not isinstance(names, Sequence) or isinstance(names, str | bytes | bytearray):
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
            chunks.extend(text.encode("utf-8"))
            increment = (
                returned_size
                if isinstance(returned_size, int) and returned_size > 0
                else len(text.encode("utf-8"))
            )
            offset += increment
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
        map_view = self._sync_refresh_map_view(timeout, interval)
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
                include_object_urls=False,
            )
        except DreameLawnMowerConnectionError as err:
            app_maps = {"error": str(err)}

        return build_map_probe_payload(
            descriptor=self._descriptor,
            map_view=self._map_view_with_cloud_summary(map_view, cloud_properties),
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

        if key == MOWER_STATE_PROPERTY_KEY and not rendered.get("decoded_label"):
            label = mower_state_label(value, language=language)
            if label:
                rendered["decoded_label"] = label
                rendered["decoded_label_source"] = "bundled_mower_protocol"
        elif key == MOWER_ERROR_PROPERTY_KEY and not rendered.get("decoded_label"):
            label = mower_error_label(value)
            if label:
                rendered["decoded_label"] = label
                rendered["decoded_label_source"] = "bundled_mower_errors"
        elif key == MOWER_RAW_STATUS_PROPERTY_KEY:
            status_blob = decode_mower_status_blob(value)
            if status_blob is not None:
                rendered["status_blob"] = status_blob.as_dict()

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


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _validate_app_map_chunk_size(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("chunk_size must be an integer")
    if value <= 0:
        raise ValueError("chunk_size must be greater than zero")
    return value


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
    points = value.get("point") if isinstance(value.get("point"), list) else []
    semantic = value.get("semantic") if isinstance(value.get("semantic"), list) else []
    trajectories = (
        value.get("trajectory") if isinstance(value.get("trajectory"), list) else []
    )
    cut_relation = (
        value.get("cut_relation") if isinstance(value.get("cut_relation"), list) else []
    )

    boundary_point_count = 0
    spot_boundary_point_count = 0
    trajectory_point_count = 0
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
    for item in trajectories:
        if not isinstance(item, Mapping):
            continue
        coordinates = item.get("data")
        if isinstance(coordinates, Sequence) and not isinstance(
            coordinates,
            str | bytes | bytearray,
        ):
            trajectory_point_count += len(coordinates)

    return {
        "name": value.get("name"),
        "total_area": total_area,
        "map_area_total": round(map_area_total, 2),
        "map_area_count": len(maps),
        "boundary_point_count": boundary_point_count,
        "spot_count": len(spots),
        "spot_boundary_point_count": spot_boundary_point_count,
        "point_count": len(points),
        "semantic_count": len(semantic),
        "trajectory_count": len(trajectories),
        "trajectory_point_count": trajectory_point_count,
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
        no_go_area_count=int(payload_summary.get("spot_count") or 0),
    )


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
                fill=(239, 68, 68, 90),
                outline=(185, 28, 28, 255),
            )
            draw.line(projected + [projected[0]], fill=(185, 28, 28, 255), width=3)

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
        property_name = str(payload.get("property_name") or "")
        property_value = payload.get("value") if isinstance(value, Mapping) else value
        value_type = _operation_value_type(property_value)
        value_type_counts[value_type] = value_type_counts.get(value_type, 0) + 1

        if unknown_prefix is not None:
            if property_name.startswith(unknown_prefix):
                unknown_keys.append(key_text)
            else:
                known_keys.append(key_text)

        status_blob = None
        if key_text == MOWER_RAW_STATUS_PROPERTY_KEY:
            decoded = decode_mower_status_blob(property_value, source="operation")
            status_blob = decoded.as_dict() if decoded is not None else None

        entries.append(
            {
                "key": key_text,
                "property_name": property_name or None,
                "siid": _json_safe(payload.get("siid")),
                "piid": _json_safe(payload.get("piid")),
                "code": _json_safe(payload.get("code")),
                "value_type": value_type,
                "value_preview": _operation_short_preview(property_value),
                "status_blob": status_blob,
            }
        )

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
