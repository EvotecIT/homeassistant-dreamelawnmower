"""Core reusable client runtime and cloud-device operations."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from .app_protocol import (
    MOWER_BLUETOOTH_PROPERTY_KEY,
    MOWER_RAW_STATUS_PROPERTY_KEY,
    MOWER_RUNTIME_STATUS_PROPERTY_KEY,
    decode_mower_status_blob,
)
from .client_constants import (
    CLOUD_PRESENCE_REFRESH_INTERVAL as _CLOUD_PRESENCE_REFRESH_INTERVAL,
)
from .client_core_helpers import (
    _merge_error_text,
    _normalize_cloud_firmware_check,
    _operation_property_summary,
    _operation_snapshot_summary,
    _validate_remote_control_step,
)
from .client_shared_helpers import (
    _property_entry_received_at,
)
from .exceptions import (
    DeviceCommandRejectedException,
    DeviceException,
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
    InvalidActionException,
)
from .exceptions import (
    DreameLawnMowerError as DreameLawnMowerError,
)
from .models import (
    DreameLawnMowerFirmwareUpdateSupport,
    DreameLawnMowerMapView,
    DreameLawnMowerRemoteControlSupport,
    DreameLawnMowerSnapshot,
    DreameLawnMowerStatusBlob,
    firmware_update_support_from_device,
    remote_control_block_reason,
    snapshot_from_device,
)
from .mowing_tasks import (
    MowingTaskResponseError,
    build_edge_mowing_request,
    build_maintenance_point_request,
    build_spot_mowing_request,
    build_zone_mowing_request,
    ensure_mowing_task_succeeded,
)
from .payload_utils import (
    _as_optional_text,
    _json_safe,
    _lower_enum_name,
)
from .runtime_state import (
    RESUME_MOWING_REQUEST,
    snapshot_with_heartbeat_task_state,
)

_MUTATION_CONFIRMATION_DELAYS_SECONDS = (0.5, 1.5, 3.0)


def _decoded_realtime_status_blob(
    device: Any,
    property_key: str,
) -> DreameLawnMowerStatusBlob | None:
    """Decode one cached realtime status property with its own timestamp."""
    realtime_entry = (getattr(device, "realtime_properties", {}) or {}).get(
        property_key
    )
    if not isinstance(realtime_entry, Mapping):
        return None
    decoded = decode_mower_status_blob(
        realtime_entry.get("value"),
        source="realtime",
        property_key=property_key,
    )
    if decoded is None:
        return None
    return replace(
        decoded,
        received_at=_property_entry_received_at(realtime_entry),
    )


def _raw_runtime_state_signature(
    snapshot: DreameLawnMowerSnapshot,
) -> tuple[Any, ...]:
    """Return the safety-relevant property state before heartbeat correction."""
    return (
        snapshot.state,
        snapshot.activity,
        snapshot.task_status,
        snapshot.mowing_session_active,
        snapshot.started,
        snapshot.raw_started,
        snapshot.paused,
        snapshot.mowing,
        snapshot.returning,
        snapshot.raw_returning,
        snapshot.docked,
        snapshot.raw_docked,
        snapshot.charging,
        snapshot.raw_charging,
    )


def _device_start_session_identity(device: Any) -> bool | None:
    """Return the cached start-versus-resume branch used by the device."""
    from .device_types import DreameMowerTaskStatus

    status = device.status
    resumes_special_task = bool(
        getattr(status, "fast_mapping_paused", False)
        or getattr(status, "returning_paused", False)
        or (
            getattr(getattr(device, "capability", None), "cruising", False)
            and getattr(status, "cruising_paused", False)
        )
    )
    task_status = getattr(status, "task_status", None)
    started = getattr(status, "started", None)
    if resumes_special_task:
        return False
    if task_status is None or task_status is DreameMowerTaskStatus.UNKNOWN:
        return None
    if started is not None:
        return not bool(started)
    return None


class _DreameLawnMowerClientCoreMixin:
    async def async_get_cached_snapshot(self) -> DreameLawnMowerSnapshot:
        """Return a snapshot from the latest in-memory device state."""
        device = await asyncio.to_thread(self._ensure_device)
        return await asyncio.to_thread(self._snapshot_from_device, device)

    async def _async_call_device_method(self, method_name: str) -> Any:
        device = await asyncio.to_thread(self._ensure_device)
        method = getattr(device, method_name)
        try:
            return await asyncio.to_thread(method)
        except DeviceCommandRejectedException as err:
            raise DreameLawnMowerCommandRejectedError(str(err)) from err
        except DeviceException as err:
            connection_error = DreameLawnMowerConnectionError(str(err))
            confirmation = {
                "start_mowing": (
                    "start mowing",
                    lambda snapshot: bool(
                        snapshot.started
                        or snapshot.mowing
                        or snapshot.mowing_session_active is True
                    ),
                ),
                "pause": (
                    "pause mowing",
                    lambda snapshot: bool(
                        snapshot.paused or snapshot.task_status == "paused"
                    ),
                ),
                "dock": (
                    "return to dock",
                    lambda snapshot: bool(
                        snapshot.returning
                        or snapshot.docked
                        or snapshot.state
                        in {"returning", "charging", "charging_completed"}
                    ),
                ),
                "stop": (
                    "stop mowing",
                    lambda snapshot: bool(
                        snapshot.mowing_session_active is False
                        or (
                            not snapshot.started
                            and not snapshot.mowing
                            and not snapshot.paused
                        )
                    ),
                ),
            }.get(method_name)
            if confirmation is None:
                raise connection_error from err
            label, predicate = confirmation
            return await self._async_reconcile_ambiguous_mutation(
                label,
                connection_error,
                predicate,
            )

    async def _async_get_cached_start_mowing_session_identity(self) -> bool | None:
        """Read the device's current start branch under its MQTT state lock."""
        device = await asyncio.to_thread(self._ensure_device)

        def read_session_identity() -> bool | None:
            state_lock = getattr(device, "_state_lock", None)
            state_context = state_lock if state_lock is not None else nullcontext()
            with state_context:
                return _device_start_session_identity(device)

        return await asyncio.to_thread(read_session_identity)

    async def _async_call_start_mowing_with_session_identity(self) -> bool | None:
        """Invoke the fallback start and capture its cached-state decision."""
        device = await asyncio.to_thread(self._ensure_device)
        new_session: bool | None = None

        def call_start_mowing() -> Any:
            nonlocal new_session

            state_lock = getattr(device, "_state_lock", None)
            state_context = state_lock if state_lock is not None else nullcontext()
            with state_context:
                # Keep the identity decision and the device's own branch under
                # the same lock used by MQTT state mutations.
                new_session = _device_start_session_identity(device)
                return device.start_mowing()

        try:
            await asyncio.to_thread(call_start_mowing)
        except DeviceCommandRejectedException as err:
            raise DreameLawnMowerCommandRejectedError(str(err)) from err
        except DeviceException as err:
            await self._async_reconcile_ambiguous_mutation(
                "start mowing",
                DreameLawnMowerConnectionError(str(err)),
                lambda snapshot: bool(
                    snapshot.started
                    or snapshot.mowing
                    or snapshot.mowing_session_active is True
                ),
            )
        return new_session

    async def _async_reconcile_ambiguous_mutation(
        self,
        label: str,
        original_error: DreameLawnMowerConnectionError,
        confirmed: Callable[[DreameLawnMowerSnapshot], bool],
    ) -> None:
        """Confirm a once-dispatched mutation without risking a duplicate send."""
        for delay in _MUTATION_CONFIRMATION_DELAYS_SECONDS:
            await asyncio.sleep(delay)
            try:
                snapshot = await self._async_refresh_authoritative_snapshot()
            except DreameLawnMowerConnectionError:
                continue
            if confirmed(snapshot):
                return
        raise DreameLawnMowerConnectionError(
            f"The mower may have received the {label} request, but its state "
            "could not be confirmed after the connection was interrupted. "
            "Refresh the mower state before trying again."
        ) from original_error

    async def _async_refresh_authoritative_snapshot(
        self,
        *,
        deadline: float | None = None,
    ) -> DreameLawnMowerSnapshot:
        """Force properties and apply heartbeat reconciliation before decisions."""
        if deadline is None:
            device = await asyncio.to_thread(self._sync_update_device, True)
        else:
            device = await asyncio.to_thread(
                self._sync_update_device,
                True,
                deadline=deadline,
            )
        return await asyncio.to_thread(self._snapshot_from_device, device)

    async def _async_cached_authoritative_snapshot(self) -> DreameLawnMowerSnapshot:
        """Apply heartbeat reconciliation to the current in-memory device state."""
        device = await asyncio.to_thread(self._ensure_device)
        return await asyncio.to_thread(self._snapshot_from_device, device)

    def _sync_update_device(
        self,
        force_request_properties: bool = False,
        *,
        deadline: float | None = None,
    ):
        device = self._ensure_device()
        try:
            if force_request_properties:
                if deadline is None:
                    device.update(force_request_properties=True)
                else:
                    device.update(
                        force_request_properties=True,
                        deadline=deadline,
                    )
            else:
                if deadline is None:
                    device.update()
                else:
                    device.update(deadline=deadline)
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err
        return device

    def _snapshot_from_device(self, device: Any) -> DreameLawnMowerSnapshot:
        """Normalize one coherent device state and retain recovery context."""
        state_lock = getattr(device, "_state_lock", None)
        state_context = state_lock if state_lock is not None else nullcontext()
        with state_context:
            snapshot = snapshot_from_device(
                self._descriptor,
                device,
                previous_snapshot=getattr(self, "_latest_snapshot", None),
            )
            observed_at = time.time()
            runtime_signature = _raw_runtime_state_signature(snapshot)
            if getattr(self, "_raw_runtime_state_signature", None) != runtime_signature:
                self._raw_runtime_state_signature = runtime_signature
                self._raw_runtime_state_observed_at = observed_at
            active_state_observed_at = getattr(
                self,
                "_raw_runtime_state_observed_at",
                observed_at,
            )
            status_blob = _decoded_realtime_status_blob(
                device,
                MOWER_RAW_STATUS_PROPERTY_KEY,
            )
            if status_blob is not None:
                snapshot = snapshot_with_heartbeat_task_state(
                    snapshot,
                    status_blob,
                    observed_at=observed_at,
                    active_state_observed_at=active_state_observed_at,
                )
        self._latest_snapshot = snapshot
        return snapshot

    def _sync_get_remote_control_support(
        self,
        refresh: bool = False,
    ) -> DreameLawnMowerRemoteControlSupport:
        if refresh:
            device = self._sync_update_device()
        else:
            device = self._ensure_device()

        try:
            from .device_types import DreameMowerProperty, DreameMowerStatus
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
            snapshot = self._snapshot_from_device(device)
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
        if rotation or velocity:
            # Movement must never be authorized by a cached snapshot. A stop
            # remains available even when the link or safety refresh fails.
            self._sync_update_device(force_request_properties=True)
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

    def _sync_get_firmware_update_support(
        self,
        refresh: bool = False,
        include_cloud: bool = True,
        include_debug_ota_catalog: bool = False,
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
            except DreameLawnMowerConnectionError as err:
                cloud_error = _merge_error_text(
                    cloud_error,
                    "cloud_device_info",
                    str(err),
                )
            try:
                cloud_device_list_page = self._sync_get_cloud_device_list_page(
                    current=1,
                    size=20,
                    language=language,
                    master=None,
                    shared_status=None,
                )
            except DreameLawnMowerConnectionError as err:
                cloud_error = _merge_error_text(
                    cloud_error,
                    "cloud_device_list_page",
                    str(err),
                )
            try:
                cloud_firmware_check = self._sync_get_cloud_firmware_check(language)
            except DreameLawnMowerConnectionError as err:
                cloud_error = _merge_error_text(
                    cloud_error,
                    "cloud_firmware_check",
                    str(err),
                )
        try:
            batch_ota_info = self._sync_get_batch_ota_info()
        except DreameLawnMowerConnectionError as err:
            cloud_error = _merge_error_text(
                cloud_error,
                "batch_ota_info",
                str(err),
            )
        if include_debug_ota_catalog:
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

        decoded = _decoded_realtime_status_blob(device, property_key)
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
                    property_key=property_key,
                )
                if decoded is not None:
                    return replace(
                        decoded,
                        received_at=_property_entry_received_at(entry),
                    )
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
        snapshot = self._snapshot_from_device(device)
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
                    include_debug_ota_catalog=True,
                    language=language,
                ).as_dict()
            except Exception as err:  # noqa: BLE001 - probe snapshots keep evidence
                payload["firmware_update"] = None
                errors.append({"section": "firmware_update", "error": str(err)})

        return payload

    def _sync_start_zone_mowing(self, zone_ids: Sequence[int]) -> Any:
        """Start mower-native zone mowing for the provided area ids."""
        try:
            response = self._sync_call_app_action(build_zone_mowing_request(zone_ids))
            return ensure_mowing_task_succeeded(response, task_name="zone mowing")
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err
        except MowingTaskResponseError as err:
            error_type = (
                DreameLawnMowerCommandRejectedError
                if isinstance(response, Mapping)
                else DreameLawnMowerConnectionError
            )
            raise error_type(str(err)) from err

    def _sync_start_edge_mowing(self, contour_ids: Sequence[Sequence[int]]) -> Any:
        """Start edge mowing for the provided contour id pairs."""
        try:
            response = self._sync_call_app_action(
                build_edge_mowing_request(contour_ids)
            )
            return ensure_mowing_task_succeeded(response, task_name="edge mowing")
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err
        except MowingTaskResponseError as err:
            error_type = (
                DreameLawnMowerCommandRejectedError
                if isinstance(response, Mapping)
                else DreameLawnMowerConnectionError
            )
            raise error_type(str(err)) from err

    def _sync_start_spot_mowing(self, spot_ids: Sequence[int]) -> Any:
        """Start mower-native spot mowing for the provided saved area ids."""
        try:
            response = self._sync_call_app_action(build_spot_mowing_request(spot_ids))
            return ensure_mowing_task_succeeded(response, task_name="spot mowing")
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err
        except MowingTaskResponseError as err:
            error_type = (
                DreameLawnMowerCommandRejectedError
                if isinstance(response, Mapping)
                else DreameLawnMowerConnectionError
            )
            raise error_type(str(err)) from err

    def _sync_go_to_maintenance_point(self, point_id: int) -> Any:
        """Drive to one mower-configured maintenance point."""
        try:
            response = self._sync_call_app_action(
                build_maintenance_point_request([point_id])
            )
            return ensure_mowing_task_succeeded(
                response,
                task_name="maintenance point",
            )
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err
        except MowingTaskResponseError as err:
            error_type = (
                DreameLawnMowerCommandRejectedError
                if isinstance(response, Mapping)
                else DreameLawnMowerConnectionError
            )
            raise error_type(str(err)) from err

    def _sync_get_batch_device_data(
        self,
        keys: Sequence[str] | None = None,
        *,
        deadline: float | None = None,
    ) -> Mapping[str, Any] | None:
        cloud = (
            self._sync_get_cloud_protocol(deadline=deadline)
            if deadline is not None
            else self._sync_get_cloud_protocol()
        )
        requested = list(keys or [])
        try:
            request_options: dict[str, Any] = {}
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DreameLawnMowerConnectionError(
                        "Batch device-data request timed out."
                    )
                request_options = {
                    "timeout": remaining,
                    "deadline": deadline,
                }
            response = cloud.get_batch_device_datas(requested, **request_options)
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

    def _sync_get_cached_cloud_device_info(self) -> Mapping[str, Any] | None:
        """Refresh cloud presence at a bounded rate and retain last-known state."""
        now = time.monotonic()
        if (
            self._cloud_device_info_refreshed_at > 0
            and now - self._cloud_device_info_refreshed_at
            < _CLOUD_PRESENCE_REFRESH_INTERVAL
        ):
            return self._latest_cloud_device_info

        self._cloud_device_info_refreshed_at = now
        info = self._sync_get_cloud_device_info("en")
        if isinstance(info, Mapping):
            self._latest_cloud_device_info = dict(info)
        return self._latest_cloud_device_info

    def _sync_resume_mowing(self) -> Any:
        """Resume a paused mower task through the app task-control protocol."""
        try:
            response = self._sync_call_app_action(RESUME_MOWING_REQUEST)
            return ensure_mowing_task_succeeded(
                response,
                task_name="resume mowing",
            )
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err
        except MowingTaskResponseError as err:
            error_type = (
                DreameLawnMowerCommandRejectedError
                if isinstance(response, Mapping)
                else DreameLawnMowerConnectionError
            )
            raise error_type(str(err)) from err

    @staticmethod
    def _with_fallback_app_maps(
        map_view: DreameLawnMowerMapView,
        app_view: DreameLawnMowerMapView,
    ) -> DreameLawnMowerMapView:
        if map_view.app_maps is not None or app_view.app_maps is None:
            return map_view
        return replace(map_view, app_maps=app_view.app_maps)

    @staticmethod
    def _with_runtime_position_details(
        map_view: DreameLawnMowerMapView,
        runtime_view: DreameLawnMowerMapView,
    ) -> DreameLawnMowerMapView:
        runtime_details = runtime_view.details
        if not isinstance(runtime_details, Mapping):
            return map_view
        if (
            runtime_details.get("runtime_pose_x") is None
            or runtime_details.get("runtime_pose_y") is None
        ):
            return map_view

        details = dict(map_view.details or {})
        for key in (
            "runtime_pose_x",
            "runtime_pose_y",
            "runtime_heading_deg",
            "runtime_region_id",
            "runtime_position_updated_at",
        ):
            value = runtime_details.get(key)
            if value is not None:
                details[key] = value
        return replace(map_view, details=details)

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
                getattr(
                    getattr(self._ensure_device(), "info", None),
                    "firmware_version",
                    None,
                )
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
            inner_success = data.get("success") if isinstance(data, Mapping) else None
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

    def _sync_get_cloud_protocol(self, *, deadline: float | None = None):
        device = self._ensure_device()
        protocol = getattr(device, "_protocol", None)
        cloud = getattr(protocol, "cloud", None)
        if cloud is None:
            raise DreameLawnMowerConnectionError("Cloud connection is unavailable.")
        if not getattr(cloud, "logged_in", False):
            login_options: dict[str, Any] = {}
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DreameLawnMowerConnectionError(
                        "Point-cloud cloud login timed out."
                    )
                login_options = {
                    "timeout": remaining,
                    "deadline": deadline,
                }
            if not cloud.login(**login_options):
                raise DreameLawnMowerConnectionError(
                    "Unable to log in to the mower cloud API."
                )
        return cloud

    def _ensure_device(self):
        with self._device_ownership_lock:
            if self._closing:
                raise DreameLawnMowerConnectionError(
                    "The mower client is shutting down."
                )
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
            if self._update_callback is not None:
                self._device.listen(self._update_callback)
            return self._device
