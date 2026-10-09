"""Shared device ownership and cloud dispatch for cooperating client mixins."""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine, Mapping, Sequence
from threading import Event, Lock
from typing import TYPE_CHECKING, Any, TypedDict

from .exceptions import (
    DeviceException,
    DreameLawnMowerCloudAPIError,
    DreameLawnMowerConnectionError,
)

if TYPE_CHECKING:
    from .client_update_scheduler import NativeDeviceUpdates
    from .cloud_session import DreameCloudSession
    from .device import DreameMowerDevice
    from .models import DreameLawnMowerDescriptor
    from .protocol import DreameMowerProtocol
    from .protocol_cloud import DreameMowerDreameHomeCloudProtocol


class _CloudPreflightOptions(TypedDict, total=False):
    retry_count: int
    timeout: float
    deadline: float


class _CloudRequestOptions(_CloudPreflightOptions, total=False):
    redact_response: bool
    on_dispatch: Callable[[], None]
    raise_on_api_error: bool


class _DreameLawnMowerClientTransport:
    """Own common transport operations over state initialized by the client.

    The assembled client supplies configuration and locks. This base supplies
    no default state and retains one device instance across all consumers.
    """

    _device: DreameMowerDevice | None
    _native_updates: NativeDeviceUpdates | None
    _device_ownership_lock: Lock
    _app_map_download_lock: Lock
    _closing: bool
    _descriptor: DreameLawnMowerDescriptor
    _username: str
    _password: str
    _country: str
    _account_type: str
    _update_callback: Callable[[], None] | None

    if TYPE_CHECKING:
        async def _async_cloud_read[ReadResult](
            self,
            read: Callable[[DreameCloudSession], Coroutine[Any, Any, ReadResult]],
        ) -> ReadResult: ...

        def _sync_get_cloud_properties(self, keys: str | Sequence[str]) -> Any: ...

        @staticmethod
        def _normalize_cloud_property_entries(payload: Any) -> list[dict[str, Any]]: ...

        @staticmethod
        def _coerce_property_bool(value: Any) -> bool | None: ...

        async def async_get_cloud_device_info(
            self, *, language: str | None = None,
        ) -> dict[str, Any] | None: ...

        def _sync_update_app_map_inventory_identity(
            self, maps: Sequence[Mapping[str, Any]],
        ) -> None: ...

        def _sync_get_app_map_objects(
            self, include_urls: bool = False,
        ) -> dict[str, Any]: ...

        def _sync_get_cloud_device_list_page(
            self, current: int, size: int, language: str | None,
            master: bool | None, shared_status: int | None,
        ) -> dict[str, Any] | None: ...

        def _sync_get_batch_ota_info(
            self, include_raw: bool = False,
        ) -> dict[str, Any]: ...

        def _sync_get_debug_ota_catalog(
            self, model_name: str | None = None, current_version: str | None = None,
            include_raw: bool = False,
        ) -> dict[str, Any]: ...

    def _sync_get_cloud_protocol(
        self, *, deadline: float | None = None
    ) -> DreameMowerDreameHomeCloudProtocol:
        device = self._ensure_device()
        protocol: DreameMowerProtocol | None = getattr(device, "_protocol", None)
        cloud: DreameMowerDreameHomeCloudProtocol | None = getattr(
            protocol, "cloud", None
        )
        if cloud is None:
            raise DreameLawnMowerConnectionError("Cloud connection is unavailable.")
        if not getattr(cloud, "logged_in", False):
            login_options: dict[str, float] = {}
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

    def _ensure_device(
        self, *, deadline: float | None = None, cancelled: Event | None = None,
    ) -> DreameMowerDevice:
        if deadline is None:
            self._device_ownership_lock.acquire()
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self._device_ownership_lock.acquire(
                timeout=remaining,
            ):
                raise DreameLawnMowerConnectionError(
                    "Cloud device info timed out waiting for device ownership."
                )
        try:
            if (cancelled is not None and cancelled.is_set()) or (
                deadline is not None and time.monotonic() >= deadline
            ):
                raise DreameLawnMowerConnectionError(
                    "Cloud device info ended before device initialization."
                )
            if self._closing:
                raise DreameLawnMowerConnectionError(
                    "The mower client is shutting down."
                )
            if self._device is not None:
                if self._native_updates is not None:
                    self._device._native_update_scheduler = (
                        self._native_updates.schedule
                    )
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
            if self._native_updates is not None:
                self._device._native_update_scheduler = self._native_updates.schedule
            if self._update_callback is not None:
                self._device.listen(self._update_callback)
            return self._device
        finally:
            self._device_ownership_lock.release()

    def _sync_call_app_action(
        self,
        payload: Mapping[str, Any],
        *,
        siid: int = 2,
        aiid: int = 50,
        retry_count: int | None = None,
        timeout: float | None = None,
        deadline: float | None = None,
        redact_response: bool = False,
        on_dispatch: Callable[[], None] | None = None,
        raise_on_api_error: bool = False,
    ) -> Any:
        cloud = (
            self._sync_get_cloud_protocol(deadline=deadline)
            if deadline is not None
            else self._sync_get_cloud_protocol()
        )
        if not getattr(cloud, "_host", None):
            try:
                preflight_options: _CloudPreflightOptions = {}
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DreameLawnMowerConnectionError(
                            "Point-cloud cloud setup timed out."
                        )
                    preflight_options = {
                        "retry_count": 0,
                        "timeout": remaining,
                        "deadline": deadline,
                    }
                if hasattr(cloud, "get_device_info_v2"):
                    cloud.get_device_info_v2("en", **preflight_options)
                elif hasattr(cloud, "get_device_info"):
                    cloud.get_device_info(**preflight_options)
            except DeviceException as err:
                raise DreameLawnMowerConnectionError(str(err)) from err
        try:
            request_options: _CloudRequestOptions = {}
            if retry_count is not None:
                request_options["retry_count"] = retry_count
            if timeout is not None:
                request_options["timeout"] = timeout
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DreameLawnMowerConnectionError(
                        "Point-cloud cloud request timed out."
                    )
                request_options["timeout"] = (
                    min(timeout, remaining) if timeout is not None else remaining
                )
                request_options["deadline"] = deadline
            if redact_response:
                request_options["redact_response"] = True
            if on_dispatch is not None:
                request_options["on_dispatch"] = on_dispatch
            if raise_on_api_error:
                request_options["raise_on_api_error"] = True
            if hasattr(cloud, "call_app_action"):
                response = cloud.call_app_action(
                    payload,
                    siid=siid,
                    aiid=aiid,
                    **request_options,
                )
            else:
                request_options.setdefault(
                    "retry_count",
                    2 if payload.get("m") == "g" else 0,
                )
                response = cloud.send(
                    "action",
                    {
                        "did": str(cloud.device_id),
                        "siid": siid,
                        "aiid": aiid,
                        "in": [payload],
                    },
                    **request_options,
                )
        except DreameLawnMowerCloudAPIError:
            raise
        except DeviceException as err:
            raise DreameLawnMowerConnectionError(str(err)) from err

        out = response.get("out") if isinstance(response, Mapping) else None
        if isinstance(out, Sequence) and not isinstance(out, str | bytes | bytearray):
            return out[0] if out else None
        return response
