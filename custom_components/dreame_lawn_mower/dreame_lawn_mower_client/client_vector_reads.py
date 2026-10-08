"""Native vector-map reads with owned parsing and rendering work."""

from __future__ import annotations

import time
from threading import Event
from typing import TYPE_CHECKING, Any

from .client_app_reads import async_read_app_action
from .client_map_helpers import _current_app_map_index
from .client_mowing_map import mowing_scene_from_batch
from .client_refresh import _run_state_worker
from .client_state_reads import async_read_device_state
from .client_vector_map_view import vector_map_details, vector_map_view
from .exceptions import DreameLawnMowerConnectionError
from .models import DreameLawnMowerMapView

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession
    from .map_visuals import MapRenderStyle
    from .mowing_map import MowingMapScene


async def async_mowing_scene(
    client: DreameLawnMowerClient,
    *,
    map_index: int,
    style: MapRenderStyle,
    label_scale: float,
) -> MowingMapScene:
    """Own native geometry retrieval and drain any started rendering worker."""
    async def read(cloud: DreameCloudSession) -> MowingMapScene:
        data = await cloud.async_get_batch_device_datas(client._descriptor.did, [])
        return await _run_state_worker(
            lambda: mowing_scene_from_batch(
                data, map_index=map_index, style=style, label_scale=label_scale
            ),
            Event(),
        )

    return await client._async_cloud_read(read)


async def _map_hint(client: DreameLawnMowerClient) -> int | None:
    try:
        response = await async_read_app_action(
            client,
            {"m": "g", "t": "MAPL"},
            deadline=time.monotonic() + 20,
        )
        return _current_app_map_index(response)
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
            return DreameLawnMowerMapView(
                source="batch_vector_map", error=str(err),
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
