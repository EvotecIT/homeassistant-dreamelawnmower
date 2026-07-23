"""Camera and video operations owned by the reusable mower client."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from typing import Any

from .camera_probe import CAMERA_PROBE_PROPERTY_KEYS, build_camera_probe_payload
from .exceptions import (
    DeviceException,
    DreameLawnMowerConnectionError,
    InvalidActionException,
)
from .models import (
    DreameLawnMowerCameraFeatureSupport,
    DreameLawnMowerCameraStreamRuntimeInputs,
    camera_metadata_advertises_video,
    camera_stream_block_reason,
)
from .operation_diagnostics import build_operation_stage_diagnostics
from .payload_utils import (
    _as_optional_text,
    _find_text_by_key,
    _json_safe,
    _lower_enum_name,
    _optional_bool,
)
from .video_credentials import derive_tx_video_app_credentials


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
    return camera_metadata_advertises_video(
        camera_streaming=camera_streaming,
        camera_light=camera_light,
        ai_detection=ai_detection,
        obstacles=obstacles,
        permit=permit,
        feature=feature,
        live_key_define=live_key_define,
        video_status=video_status,
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
    if value not in {"app_action", "with_session", "no_session", "empty_session"}:
        raise ValueError(
            "payload_mode must be one of: app_action, with_session, no_session, "
            "empty_session"
        )
    return value


def _normalize_tx_rtc_info(
    value: Any,
) -> dict[str, Any]:
    """Normalize the app's getTxRtcInfo response for XP2P startup."""
    output = {
        "channel_id": _find_text_by_key(
            value,
            ("channelId", "channel_id", "deviceId", "device_id"),
        ),
        "product_id": _find_text_by_key(value, ("productId", "product_id")),
        "device_name": _find_text_by_key(value, ("deviceName", "device_name")),
        "raw": _json_safe(value, max_depth=4),
    }
    for output_key, source_keys in (
        ("secret_id", ("secretId", "secret_id")),
        ("secret_key", ("secretKey", "secret_key")),
        (
            "app_id",
            ("appId", "app_id", "appid", "appKey", "app_key", "appkey"),
        ),
        (
            "app_secret",
            (
                "appSecret",
                "app_secret",
                "appsecret",
                "xp2pSecretKey",
                "xp2p_secretKey",
                "xp2p_secret_key",
            ),
        ),
    ):
        text = _find_text_by_key(value, source_keys)
        if text:
            output[output_key] = text

    derived_app_id, derived_app_secret = derive_tx_video_app_credentials(
        output.get("secret_id"),
        output.get("secret_key"),
    )
    derived = False
    if not output.get("app_id") and derived_app_id:
        output["app_id"] = derived_app_id
        derived = True
    if not output.get("app_secret") and derived_app_secret:
        output["app_secret"] = derived_app_secret
        derived = True
    if derived:
        output["app_credentials_source"] = "dreame_identity_decrypted"
    return output


def _normalize_tx_p2p_info(value: Any) -> dict[str, Any]:
    """Normalize the app's getP2PInfo response."""
    p2p_text = _find_text_by_key(
        value,
        (
            "p2pInfo",
            "p2p_info",
            "xp2pInfo",
            "xp2p_info",
            "initStringApp",
            "apiLicense",
        ),
    )
    output = {
        "available": value not in (None, "", {}, []),
        "p2p_info": p2p_text,
        "raw": _json_safe(value, max_depth=5),
    }
    for output_key, source_keys in (
        (
            "app_id",
            (
                "appId",
                "app_id",
                "appid",
                "appKey",
                "app_key",
                "appkey",
                "xp2pKey",
                "xp2p_key",
            ),
        ),
        (
            "app_secret",
            (
                "appSecret",
                "app_secret",
                "appsecret",
                "xp2pSecretKey",
                "xp2p_secretKey",
                "xp2p_secret_key",
            ),
        ),
        ("secret_id", ("secretId", "secret_id")),
        ("secret_key", ("secretKey", "secret_key")),
    ):
        text = _find_text_by_key(value, source_keys)
        if text:
            output[output_key] = text
    return output


def _camera_stream_runtime_inputs_from_cloud_payload(
    value: Mapping[str, Any],
) -> DreameLawnMowerCameraStreamRuntimeInputs:
    tx_rtc = value.get("tx_rtc_info")
    tx_rtc = tx_rtc if isinstance(tx_rtc, Mapping) else {}
    p2p = value.get("p2p_info")
    p2p = p2p if isinstance(p2p, Mapping) else {}
    raw = value.get("raw")
    raw = raw if isinstance(raw, Mapping) else {}
    return DreameLawnMowerCameraStreamRuntimeInputs(
        source=str(value.get("source") or "dreame_third_video_tx"),
        did=str(value.get("did") or ""),
        channel_id=_as_optional_text(tx_rtc.get("channel_id")),
        product_id=_as_optional_text(tx_rtc.get("product_id")),
        device_name=_as_optional_text(tx_rtc.get("device_name")),
        p2p_info=_as_optional_text(p2p.get("p2p_info")),
        secret_id=_as_optional_text(tx_rtc.get("secret_id"))
        or _as_optional_text(p2p.get("secret_id")),
        secret_key=_as_optional_text(tx_rtc.get("secret_key"))
        or _as_optional_text(p2p.get("secret_key")),
        app_id=_as_optional_text(tx_rtc.get("app_id"))
        or _as_optional_text(p2p.get("app_id")),
        app_secret=_as_optional_text(tx_rtc.get("app_secret"))
        or _as_optional_text(p2p.get("app_secret")),
        lan_client_token=_find_text_by_key(
            raw.get("access_token"),
            ("accessToken", "accesstoken", "token"),
        ),
        diagnostics=(
            value.get("diagnostics")
            if isinstance(value.get("diagnostics"), Mapping)
            else {}
        ),
        raw=_json_safe(value, max_depth=5),
    )


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


class _DreameLawnMowerCameraMixin:
    """Camera/video domain for :class:`DreameLawnMowerClient`.

    The host client supplies device lifecycle, cloud protocol, app action, and
    snapshot hooks. Keeping those hooks in the client preserves one connection
    owner while isolating the complete camera responsibility.
    """

    @property
    def last_camera_stream_diagnostics(self) -> Mapping[str, Any]:
        """Return the latest privacy-safe TX video cloud operation summary."""
        return self._last_camera_stream_diagnostics

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
        payload_mode: str = "app_action",
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

    async def async_set_camera_stream_enabled(self, enabled: bool) -> Any:
        """Call the stream toggle used by the Dreame app."""
        return await asyncio.to_thread(self._sync_set_camera_stream_enabled, enabled)

    async def async_get_camera_stream_inputs(self) -> dict[str, Any]:
        """Fetch the cloud TX/XP2P inputs needed by Dreame's video runtime."""
        return await asyncio.to_thread(self._sync_get_camera_stream_inputs)

    async def async_get_camera_stream_runtime_inputs(
        self,
    ) -> DreameLawnMowerCameraStreamRuntimeInputs:
        """Fetch the normalized XP2P runtime contract for live video."""
        return await asyncio.to_thread(self._sync_get_camera_stream_runtime_inputs)

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
            raise DreameLawnMowerConnectionError("GET_PHOTO_INFO returned no response.")
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

    def _sync_set_camera_stream_enabled(self, enabled: bool) -> Any:
        """Call the exact stream toggle used by the Dreame mower app."""
        if enabled:
            before = self._sync_update_device()
            self._guard_camera_stream_probe_idle(before)
        return self._sync_call_app_stream_video(bool(enabled))

    def _sync_call_app_stream_video(self, enabled: bool) -> Any:
        """Call Control.switchVideo(on) from the mower React Native bundle."""
        response = self._sync_call_app_action(
            {
                "m": "a",
                "p": 0,
                "o": 400,
                "d": {"on": bool(enabled)},
            }
        )
        if not isinstance(response, Mapping) or response.get("r") != 0:
            raise DreameLawnMowerConnectionError(
                "Dreame app video toggle returned an invalid response."
            )
        return response

    def _call_stream_video_status(
        self,
        device: Any,
        property_enum: Any,
        *,
        operation: str,
        oper_type: str,
        payload_mode: str,
    ) -> Any:
        if payload_mode == "app_action":
            return self._sync_call_app_stream_video(oper_type == "start")

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

    def _sync_get_camera_stream_inputs(self) -> dict[str, Any]:
        """Fetch and normalize the cloud data used by TXVideoSdk video startup."""
        diagnostics: dict[str, Any] = {
            "operation": "camera_stream_inputs",
            "version": 1,
            "stages": [],
        }
        self._last_camera_stream_diagnostics = diagnostics
        output: dict[str, Any] = {
            "source": "dreame_third_video_tx",
            "did": self._descriptor.did,
            "tx_rtc_info": {},
            "p2p_info": None,
            "raw": {},
            "diagnostics": diagnostics,
        }
        current_stage = "cloud_access_token"
        current_request: Mapping[str, Any] = {"os": 1}
        sensitive_values: list[object] = [self._descriptor.did]

        def record_stage(
            stage: str,
            *,
            request: Mapping[str, Any],
            response: Any = None,
            result: Mapping[str, Any] | None = None,
            error: BaseException | None = None,
            include_response: bool = True,
        ) -> None:
            arguments: dict[str, Any] = {
                "request_context": request,
                "result": result,
                "error": error,
                "sensitive_values": tuple(sensitive_values),
            }
            if include_response:
                arguments["response"] = response
            diagnostics["stages"].append(
                build_operation_stage_diagnostics(stage, **arguments)
            )

        setup_request = {"device_initialized": self._device is not None}
        try:
            cloud = self._sync_get_cloud_protocol()
        except Exception as err:
            record_stage(
                "cloud_setup",
                request=setup_request,
                error=err,
                include_response=False,
            )
            diagnostics["completed"] = False
            raise
        record_stage(
            "cloud_setup",
            request=setup_request,
            result={
                "available": True,
                "logged_in": bool(getattr(cloud, "logged_in", False)),
            },
            include_response=False,
        )

        try:
            access_token = None
            if hasattr(cloud, "get_tx_video_access_token"):
                current_stage = "cloud_access_token"
                current_request = {"os": 1}
                access = cloud.get_tx_video_access_token(os=1)
                output["raw"]["access_token"] = _json_safe(access, max_depth=4)
                access_token = _find_text_by_key(
                    access,
                    ("accessToken", "accesstoken", "token"),
                )
                if access_token:
                    sensitive_values.append(access_token)
                record_stage(
                    current_stage,
                    request=current_request,
                    response=access,
                    result={"access_token_present": bool(access_token)},
                )
            else:
                record_stage(
                    current_stage,
                    request=current_request,
                    result={"available": False},
                    include_response=False,
                )
            if hasattr(cloud, "get_tx_video_device_identity"):
                current_stage = "cloud_device_identity"
                current_request = {
                    "did_present": bool(self._descriptor.did),
                    "access_token_present": bool(access_token),
                    "os": 1,
                }
                identity = cloud.get_tx_video_device_identity(
                    access_token=access_token,
                    os=1,
                )
                output["raw"]["identity"] = _json_safe(identity, max_depth=5)
                output["tx_rtc_info"] = _normalize_tx_rtc_info(
                    identity,
                )
                for field in (
                    "channel_id",
                    "product_id",
                    "device_name",
                    "secret_id",
                    "secret_key",
                    "app_id",
                    "app_secret",
                ):
                    if sensitive_value := output["tx_rtc_info"].get(field):
                        sensitive_values.append(sensitive_value)
                record_stage(
                    current_stage,
                    request=current_request,
                    response=identity,
                    result={
                        f"{field}_present": bool(output["tx_rtc_info"].get(field))
                        for field in (
                            "channel_id",
                            "product_id",
                            "device_name",
                            "secret_id",
                            "secret_key",
                            "app_id",
                            "app_secret",
                        )
                    },
                )
            else:
                output["tx_rtc_info"] = _normalize_tx_rtc_info({})
                record_stage(
                    "cloud_device_identity",
                    request={
                        "did_present": bool(self._descriptor.did),
                        "access_token_present": bool(access_token),
                        "os": 1,
                    },
                    result={"available": False},
                    include_response=False,
                )
            if hasattr(cloud, "get_tx_video_p2p_info"):
                current_stage = "cloud_p2p_info"
                current_request = {
                    "did_present": bool(self._descriptor.did),
                    "access_token_present": bool(access_token),
                    "os": 1,
                }
                p2p_info = cloud.get_tx_video_p2p_info(
                    access_token=access_token,
                    os=1,
                )
                output["raw"]["p2p_info"] = _json_safe(p2p_info, max_depth=5)
                output["p2p_info"] = _normalize_tx_p2p_info(p2p_info)
                record_stage(
                    current_stage,
                    request=current_request,
                    response=p2p_info,
                    result={
                        "p2p_info_present": bool(
                            output["p2p_info"].get("p2p_info")
                        ),
                        "available": bool(output["p2p_info"].get("available")),
                    },
                )
            else:
                output["p2p_info"] = {"available": False}
                record_stage(
                    "cloud_p2p_info",
                    request={
                        "did_present": bool(self._descriptor.did),
                        "access_token_present": bool(access_token),
                        "os": 1,
                    },
                    result={"available": False},
                    include_response=False,
                )
        except DeviceException as err:
            record_stage(
                current_stage,
                request=current_request,
                error=err,
                include_response=False,
            )
            diagnostics["completed"] = False
            raise DreameLawnMowerConnectionError(str(err)) from err
        except Exception as err:
            record_stage(
                current_stage,
                request=current_request,
                error=err,
                include_response=False,
            )
            diagnostics["completed"] = False
            raise

        runtime_inputs = _camera_stream_runtime_inputs_from_cloud_payload(output)
        diagnostics["ready"] = runtime_inputs.ready
        diagnostics["missing_required"] = runtime_inputs.missing_required
        if runtime_inputs.missing_required and hasattr(
            cloud,
            "get_tx_video_user_eligibility",
        ):
            eligibility_request = {
                "did_present": bool(self._descriptor.did),
                "access_token_present": bool(access_token),
                "os": 1,
            }
            try:
                eligibility = cloud.get_tx_video_user_eligibility(
                    access_token=access_token,
                    os=1,
                )
            except Exception as err:
                record_stage(
                    "cloud_user_eligibility",
                    request=eligibility_request,
                    error=err,
                    include_response=False,
                )
            else:
                record_stage(
                    "cloud_user_eligibility",
                    request=eligibility_request,
                    response=eligibility,
                    result={
                        "is_device_user": _find_text_by_key(
                            eligibility,
                            ("isDevUser", "devUser", "isUser"),
                        )
                    },
                )
        diagnostics["completed"] = True
        diagnostics["provisioning_issue"] = runtime_inputs.provisioning_issue
        return output

    def _sync_get_camera_stream_runtime_inputs(
        self,
    ) -> DreameLawnMowerCameraStreamRuntimeInputs:
        """Return the normalized XP2P input set consumed by a video runner."""
        return _camera_stream_runtime_inputs_from_cloud_payload(
            self._sync_get_camera_stream_inputs()
        )

    def _guard_camera_stream_probe_idle(self, device: Any) -> None:
        snapshot = self._snapshot_from_device(device)
        if reason := camera_stream_block_reason(snapshot):
            raise DreameLawnMowerConnectionError(reason)

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
