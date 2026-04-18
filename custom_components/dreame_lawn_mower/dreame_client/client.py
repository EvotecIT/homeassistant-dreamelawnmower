"""Async-friendly reusable mower client facade."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any

from .app_protocol import (
    MOWER_ERROR_PROPERTY_KEY,
    MOWER_PROPERTY_HINTS,
    MOWER_RAW_STATUS_PROPERTY_KEY,
    MOWER_STATE_PROPERTY_KEY,
    decode_mower_status_blob,
    mower_error_label,
    mower_state_label,
)
from .camera_probe import CAMERA_PROBE_PROPERTY_KEYS, build_camera_probe_payload
from .exceptions import DeviceException, InvalidActionException
from .map_probe import MAP_PROBE_PROPERTY_KEYS, build_map_probe_payload
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
        state = _lower_enum_name(getattr(getattr(device, "status", None), "state", None))
        status_obj = getattr(getattr(device, "status", None), "status", None)
        status = _lower_enum_name(status_obj)
        active = bool(
            getattr(device, "_remote_control", False)
            or status_obj is DreameMowerStatus.REMOTE_CONTROL
            or status == "remote_control"
            or state == "remote_control"
        )

        if not mapping:
            return DreameLawnMowerRemoteControlSupport(
                supported=False,
                active=active,
                state=state,
                status=status,
                reason="Remote-control property mapping is not available.",
            )

        if bool(getattr(getattr(device, "status", None), "fast_mapping", False)):
            return DreameLawnMowerRemoteControlSupport(
                supported=False,
                active=active,
                siid=mapping.get("siid"),
                piid=mapping.get("piid"),
                state=state,
                status=status,
                reason="Remote control is blocked while fast mapping.",
            )

        return DreameLawnMowerRemoteControlSupport(
            supported=True,
            active=active,
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
            extend_sc_type=tuple(str(item) for item in device_info.get("extendScType", []) or []),
            video_status=_json_safe(device_info.get("videoStatus") or info_raw.get("videoStatus")),
            video_dynamic_vendor=_optional_bool(
                device_info.get("videoDynamicVendor")
                if "videoDynamicVendor" in device_info
                else info_raw.get("videoDynamicVendor")
            ),
            live_key_count=len(live_key_define) if isinstance(live_key_define, Mapping) else 0,
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
                output["after"] = self._stream_status_payload(self._sync_update_device())
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

        from .types import DreameMowerAction, PIID

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
        raw_running = bool(snapshot.raw_attributes.get("running"))
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

    def _sync_scan_cloud_properties(
        self,
        keys: str | Sequence[str] | None,
        siids: Sequence[int] | None,
        piid_start: int,
        piid_end: int,
        chunk_size: int,
        language: str,
        only_values: bool,
    ) -> dict[str, Any]:
        normalized_keys = self._build_cloud_property_keys(
            keys=keys,
            siids=siids,
            piid_start=piid_start,
            piid_end=piid_end,
        )
        if not normalized_keys:
            return {
                "requested_key_count": 0,
                "returned_entry_count": 0,
                "displayed_entry_count": 0,
                "entries": [],
            }

        all_entries: list[dict[str, Any]] = []
        for offset in range(0, len(normalized_keys), max(chunk_size, 1)):
            chunk = normalized_keys[offset: offset + max(chunk_size, 1)]
            response = self._sync_get_cloud_properties(chunk)
            all_entries.extend(self._normalize_cloud_property_entries(response))

        rendered = all_entries
        if only_values:
            rendered = [entry for entry in rendered if self._entry_has_meaningful_value(entry)]

        rendered = [
            self._annotate_cloud_property_entry(entry, language=language)
            for entry in sorted(
                rendered,
                key=lambda item: str(item.get("key", "")),
            )
        ]
        return {
            "requested_key_count": len(normalized_keys),
            "returned_entry_count": len(all_entries),
            "displayed_entry_count": len(rendered),
            "entries": rendered,
        }

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

    def _sync_probe_map_sources(
        self,
        timeout: float,
        interval: float,
        language: str,
    ) -> dict[str, Any]:
        map_view = self._sync_refresh_map_view(timeout, interval)
        cloud_properties = self._sync_scan_cloud_properties(
            keys=MAP_PROBE_PROPERTY_KEYS,
            siids=None,
            piid_start=1,
            piid_end=1,
            chunk_size=50,
            language=language,
            only_values=False,
        )
        cloud_device_info = self._sync_get_cloud_device_info(language)
        cloud_device_list_page = self._sync_get_cloud_device_list_page(
            current=1,
            size=20,
            language=language,
            master=None,
            shared_status=None,
        )
        try:
            cloud_user_features = self._sync_get_cloud_user_features(language)
        except DreameLawnMowerConnectionError as err:
            cloud_user_features = {"error": str(err)}

        return build_map_probe_payload(
            descriptor=self._descriptor,
            map_view=self._map_view_with_cloud_summary(map_view, cloud_properties),
            cloud_properties=cloud_properties,
            cloud_device_info=cloud_device_info,
            cloud_device_list_page=cloud_device_list_page,
            cloud_user_features=cloud_user_features,
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
    ) -> dict[str, Any]:
        rendered = dict(entry)
        key = str(rendered.get("key", ""))
        value = rendered.get("value")
        property_hint = MOWER_PROPERTY_HINTS.get(key)
        if property_hint:
            rendered["property_hint"] = property_hint

        if key == MOWER_STATE_PROPERTY_KEY:
            label = mower_state_label(value, language=language)
            if label:
                rendered["decoded_label"] = label
        elif key == MOWER_ERROR_PROPERTY_KEY:
            label = mower_error_label(value)
            if label:
                rendered["decoded_label"] = label
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
            raise DreameLawnMowerConnectionError("Unable to log in to the mower cloud API.")
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
