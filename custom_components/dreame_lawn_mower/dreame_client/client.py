"""Async-friendly reusable mower client facade."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any

from .exceptions import DeviceException
from .models import (
    SUPPORTED_ACCOUNT_TYPES,
    DreameLawnMowerDescriptor,
    DreameLawnMowerSnapshot,
    DreameLawnMowerMapSummary,
    descriptor_from_cloud_record,
    display_name_for_model,
    map_summary_from_map_data,
    snapshot_from_device,
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

    @property
    def descriptor(self) -> DreameLawnMowerDescriptor:
        """Return the selected mower descriptor."""
        return self._descriptor

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

    async def async_refresh_map_summary(
        self,
        *,
        timeout: float = 8.0,
        interval: float = 0.5,
    ) -> DreameLawnMowerMapSummary | None:
        """Try to refresh map data and return a normalized summary."""
        return await asyncio.to_thread(
            self._sync_refresh_map_summary,
            timeout,
            interval,
        )

    async def async_get_map_png(
        self,
        *,
        timeout: float = 8.0,
        interval: float = 0.5,
    ) -> bytes | None:
        """Try to refresh the current mower map and return a rendered PNG."""
        return await asyncio.to_thread(
            self._sync_get_map_png,
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

    async def async_get_cloud_properties(
        self,
        keys: str | Sequence[str],
    ) -> Any:
        """Fetch raw cloud property values from the `iotstatus/props` endpoint."""
        return await asyncio.to_thread(self._sync_get_cloud_properties, keys)

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
        map_data = self._sync_wait_for_map(timeout, interval)
        if map_data is None:
            return None

        device = self._ensure_device()
        render_map_data = device.get_map_for_render(map_data) or map_data

        from .map import DreameMowerMapDataJsonRenderer

        renderer = DreameMowerMapDataJsonRenderer()
        return renderer.render_map(render_map_data)

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
