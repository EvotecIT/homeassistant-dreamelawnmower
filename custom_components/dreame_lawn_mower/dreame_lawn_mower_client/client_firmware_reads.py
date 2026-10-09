"""Owned firmware-support reads using the shared native cloud operations."""

from __future__ import annotations

from threading import Event
from typing import TYPE_CHECKING

from .client_core_helpers import _merge_error_text
from .client_refresh import _run_state_worker
from .exceptions import DreameLawnMowerConnectionError
from .models import (
    DreameLawnMowerFirmwareUpdateSupport,
    firmware_update_support_from_device,
)
from .payload_utils import _as_optional_text

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_read_firmware_support(
    client: DreameLawnMowerClient,
    *,
    refresh: bool,
    include_cloud: bool,
    include_debug_ota_catalog: bool,
    language: str | None,
) -> DreameLawnMowerFirmwareUpdateSupport:
    """Collect partial evidence while owning cancellation through final state reads."""
    cancelled = Event()

    async def read(_cloud: DreameCloudSession) -> DreameLawnMowerFirmwareUpdateSupport:
        if refresh:
            device = await client._async_update_device()
        else:
            device = await _run_state_worker(
                lambda: client._ensure_device(cancelled=cancelled), cancelled,
            )

        cloud_device_info = None
        cloud_device_list_page = None
        cloud_firmware_check = None
        batch_ota_info = None
        debug_ota_catalog = None
        cloud_error = None
        if include_cloud:
            try:
                cloud_device_info = await client.async_get_cloud_device_info(
                    language=language,
                )
            except DreameLawnMowerConnectionError as err:
                cloud_error = _merge_error_text(
                    cloud_error,
                    "cloud_device_info",
                    str(err),
                )
            try:
                cloud_device_list_page = await client.async_get_cloud_device_list_page(
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
                cloud_firmware_check = await client.async_get_cloud_firmware_check(
                    language=language,
                )
            except DreameLawnMowerConnectionError as err:
                cloud_error = _merge_error_text(
                    cloud_error,
                    "cloud_firmware_check",
                    str(err),
                )
        try:
            batch_ota_info = await client.async_get_batch_ota_info()
        except DreameLawnMowerConnectionError as err:
            cloud_error = _merge_error_text(
                cloud_error,
                "batch_ota_info",
                str(err),
            )
        if include_debug_ota_catalog:
            try:
                debug_ota_catalog = await client.async_get_debug_ota_catalog(
                    current_version=_as_optional_text(
                        getattr(device.info, "firmware_version", None),
                    ),
                )
            except DreameLawnMowerConnectionError as err:
                debug_ota_catalog = {
                    "source": "debug_ota_catalog",
                    "available": False,
                    "errors": [{"stage": "fetch", "error": str(err)}],
                }

        def build() -> DreameLawnMowerFirmwareUpdateSupport:
            with device._state_lock:
                return firmware_update_support_from_device(
                    device,
                    cloud_device_info=cloud_device_info,
                    cloud_device_list_page=cloud_device_list_page,
                    cloud_firmware_check=cloud_firmware_check,
                    batch_ota_info=batch_ota_info,
                    debug_ota_catalog=debug_ota_catalog,
                    cloud_error=cloud_error,
                )

        return await _run_state_worker(build, cancelled)

    return await client._async_cloud_read(read)
