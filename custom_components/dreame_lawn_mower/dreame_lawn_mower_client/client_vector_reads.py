"""Native vector-map reads with owned parsing and rendering work."""

from __future__ import annotations

import time
from threading import Event
from typing import TYPE_CHECKING, Any

from .client_app_reads import async_read_app_action
from .client_map_helpers import _current_app_map_index
from .client_mowing_map import bounded_mowing_map_batch
from .client_refresh import _run_state_worker
from .client_state_reads import async_read_device_state
from .client_vector_map_view import vector_map_details, vector_map_view
from .exceptions import DreameLawnMowerConnectionError
from .models import DreameLawnMowerMapView
from .vector_map import _parse_drawable_batch_vector_map

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
        generation = client._runtime_geometry_generation
        data = await cloud.async_get_batch_device_datas(client._descriptor.did, [])
        app_maps = None
        if await _needs_app_geometry(data, map_index, background=True):
            app_maps = await client.async_get_app_maps(
                include_payload=True, include_objects=False,
            )
        return await _run_state_worker(
            lambda: client._mowing_map_scene_from_batch(
                data, map_index=map_index, style=style, label_scale=label_scale,
                app_maps=app_maps,
                geometry_generation=generation,
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


async def _needs_app_geometry(
    data: dict[str, Any] | None, map_index: int | None, *, background: bool = False,
) -> bool:
    """Keep device-sized map parsing off the event loop, draining on cancel."""
    return await _run_state_worker(
        lambda: _parse_drawable_batch_vector_map(
            bounded_mowing_map_batch(data) if background else data,
            current_map_index=map_index,
        ) is None,
        Event(),
    )


async def async_vector_details(client: DreameLawnMowerClient) -> dict[str, Any]:
    async def read(cloud: DreameCloudSession) -> dict[str, Any]:
        generation = client._runtime_geometry_generation
        try:
            data = await cloud.async_get_batch_device_datas(client._descriptor.did, [])
        except DreameLawnMowerConnectionError as err:
            return {"available": False, "source": "batch_vector_map", "error": str(err)}
        hint = await _map_hint(client)
        app_maps = None
        if await _needs_app_geometry(data, hint):
            try:
                app_maps = await client.async_get_app_maps(
                    include_payload=True, include_objects=False,
                )
            except Exception as err:  # noqa: BLE001 - optional map source
                return {
                    "available": False, "source": "batch_vector_map", "error": str(err),
                }
            hint = app_maps.get("current_map_index")
        return await async_read_device_state(
            client,
            lambda _device: vector_map_details(
                data, hint, client=client, app_maps=app_maps,
                geometry_generation=generation,
            ),
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
        generation = client._runtime_geometry_generation
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
        app_maps = None
        if await _needs_app_geometry(data, hint):
            try:
                app_maps = await client.async_get_app_maps(
                    include_payload=True, include_objects=False,
                )
            except Exception as err:  # noqa: BLE001 - retain other camera sources
                return DreameLawnMowerMapView(source="batch_vector_map", error=str(err))
            if hint is None:
                hint = app_maps.get("current_map_index")
        return await async_read_device_state(
            client,
            lambda _device: vector_map_view(
                client,
                data,
                current_map_index=hint,
                label_scale=label_scale,
                style=style,
                app_maps=app_maps,
                geometry_generation=generation,
            ),
            refresh=False,
        )

    return await client._async_cloud_read(read)
