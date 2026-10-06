"""Native vector-map reads with owned parsing and rendering work."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .client_app_reads import async_read_app_action
from .client_map_helpers import _normalize_app_map_entries
from .client_shared_helpers import _positive_int
from .client_state_reads import async_read_device_state
from .client_vector_map_view import vector_map_details, vector_map_view
from .exceptions import DreameLawnMowerConnectionError
from .models import DreameLawnMowerMapView

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession
    from .map_visuals import MapRenderStyle


async def _map_hint(client: DreameLawnMowerClient) -> int | None:
    try:
        response = await async_read_app_action(
            client,
            {"m": "g", "t": "MAPL"},
            deadline=time.monotonic() + 20,
        )
        for entry in _normalize_app_map_entries(response):
            if entry.get("current"):
                return _positive_int(entry.get("idx"))
    except Exception:  # noqa: BLE001 - same best-effort hint as synchronous reader
        return None
    return None


async def async_vector_details(client: DreameLawnMowerClient) -> dict[str, Any]:
    async def read(cloud: DreameCloudSession) -> dict[str, Any]:
        try:
            data = await cloud.async_get_batch_device_datas(client._descriptor.did, [])
        except DreameLawnMowerConnectionError as err:
            return {"available": False, "source": "batch_vector_map", "error": str(err)}
        hint = await _map_hint(client)
        return await async_read_device_state(
            client,
            lambda _device: vector_map_details(data, hint),
            refresh=False,
        )

    return await client._async_cloud_read(read)


async def async_vector_view(
    client: DreameLawnMowerClient,
    *,
    current_map_index: int | None,
    label_scale: float,
    style: MapRenderStyle | None,
) -> DreameLawnMowerMapView:
    async def read(cloud: DreameCloudSession) -> DreameLawnMowerMapView:
        try:
            data = await cloud.async_get_batch_device_datas(client._descriptor.did, [])
        except DreameLawnMowerConnectionError as err:
            error = str(err)
            return await async_read_device_state(
                client,
                lambda _device: DreameLawnMowerMapView(
                    source="batch_vector_map",
                    error=error,
                    diagnostics=client._safe_map_diagnostics(
                        source="batch_vector_map", reason=error
                    ),
                ),
                refresh=False,
            )
        hint = (
            current_map_index
            if current_map_index is not None
            else await _map_hint(client)
        )
        return await async_read_device_state(
            client,
            lambda _device: vector_map_view(
                client,
                data,
                current_map_index=hint,
                label_scale=label_scale,
                style=style,
            ),
            refresh=False,
        )

    return await client._async_cloud_read(read)
