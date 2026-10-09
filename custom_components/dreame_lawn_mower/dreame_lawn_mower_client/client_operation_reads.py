"""Owned diagnostic snapshots composed from native public reads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .client_state_reads import async_read_device_state

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_capture_operation_snapshot(
    client: DreameLawnMowerClient,
    *,
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
    """Refresh once, retaining partial evidence and owning optional legacy work."""
    async def read(_cloud: DreameCloudSession) -> dict[str, Any]:
        payload = await async_read_device_state(
            client,
            lambda device: client._operation_snapshot_from_device(device, label),
            refresh=True,
        )
        errors = payload["errors"]
        if include_status_blob:
            try:
                status = await client.async_get_status_blob(
                    refresh=False, include_cloud=include_cloud_status_blob,
                )
                payload["status_blob"] = (
                    status.as_dict() if status is not None else None
                )
            except Exception as err:  # noqa: BLE001 - diagnostics retain partial evidence
                payload["status_blob"] = None
                errors.append({"section": "status_blob", "error": str(err)})
        if include_remote_control:
            try:
                support = await client.async_get_remote_control_support(refresh=False)
                payload["remote_control_support"] = support.as_dict()
            except Exception as err:  # noqa: BLE001 - diagnostics retain partial evidence
                payload["remote_control_support"] = None
                errors.append({"section": "remote_control_support", "error": str(err)})
        if include_map_view:
            try:
                view = await client.async_refresh_map_view(
                    timeout=map_timeout, interval=map_interval,
                )
                payload["map_view"] = view.as_dict()
            except Exception as err:  # noqa: BLE001 - diagnostics retain partial evidence
                payload["map_view"] = None
                errors.append({"section": "map_view", "error": str(err)})
        if include_firmware:
            try:
                firmware = await client.async_get_firmware_update_support(
                    refresh=False, include_cloud=True, include_debug_ota_catalog=True,
                    language=language,
                )
                payload["firmware_update"] = firmware.as_dict()
            except Exception as err:  # noqa: BLE001 - diagnostics retain partial evidence
                payload["firmware_update"] = None
                errors.append({"section": "firmware_update", "error": str(err)})
        return payload

    return await client._async_cloud_read(read)
