"""Native vector-map reads with owned parsing and rendering work."""

from __future__ import annotations

import time
from dataclasses import replace
from threading import Event
from typing import TYPE_CHECKING, Any

from .app_map_geometry import parse_app_map_geometry
from .client_app_map_view import current_prefetched_app_maps
from .client_app_reads import async_read_app_action
from .client_map_helpers import _current_app_map_index
from .client_mowing_map import bounded_mowing_map_batch
from .client_refresh import _run_state_worker
from .client_state_reads import async_read_device_state
from .client_vector_map_view import (
    _select_vector_map_geometry,
    vector_map_details,
    vector_map_view,
)
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
    prefetched_app_maps: dict[str, Any] | None = None,
) -> MowingMapScene:
    """Own native geometry retrieval and drain any started rendering worker."""
    async def read(cloud: DreameCloudSession) -> MowingMapScene:
        generation = client._runtime_geometry_generation
        batch_error = None
        try:
            data = await cloud.async_get_batch_device_datas(client._descriptor.did, [])
        except DreameLawnMowerConnectionError as err:
            data = None
            batch_error = err
        app_maps = current_prefetched_app_maps(client, prefetched_app_maps)
        if await _needs_app_geometry(data, map_index, background=True):
            try:
                app_maps = await client.async_get_app_maps(
                    include_payload=True, include_objects=False,
                )
            except Exception as app_error:
                if batch_error is not None:
                    raise batch_error from app_error
                raise
        if batch_error is not None and not await _app_geometry_available(
            app_maps, map_index,
        ):
            raise batch_error
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


async def _app_geometry_available(
    app_maps: dict[str, Any] | None, map_index: int | None,
) -> bool:
    """Reject unusable fallback data before acquiring contended device state."""
    return await _run_state_worker(
        lambda: parse_app_map_geometry(app_maps, map_index) is not None,
        Event(),
    )


async def _vector_geometry_available(
    data: dict[str, Any] | None,
    app_maps: dict[str, Any] | None,
    map_index: int | None,
) -> bool:
    """Recognize live geometry or standalone saved history without device state."""
    return await _run_state_worker(
        lambda: _select_vector_map_geometry(data, map_index, app_maps)[0] is not None,
        Event(),
    )


async def async_vector_details(
    client: DreameLawnMowerClient, *,
    prefetched_app_maps: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async def read(cloud: DreameCloudSession) -> dict[str, Any]:
        generation = client._runtime_geometry_generation
        batch_error = None
        try:
            data = await cloud.async_get_batch_device_datas(client._descriptor.did, [])
        except DreameLawnMowerConnectionError as err:
            data = None
            batch_error = str(err)
        hint = await _map_hint(client)
        app_maps = current_prefetched_app_maps(client, prefetched_app_maps)
        app_error = None
        needs_app_geometry = await _needs_app_geometry(data, hint)
        if needs_app_geometry:
            try:
                app_maps = await client.async_get_app_maps(
                    include_payload=True, include_objects=False,
                )
            except Exception as err:  # noqa: BLE001 - optional map source
                app_error = str(err)
            if hint is None and app_maps is not None:
                hint = app_maps.get("current_map_index")
        if needs_app_geometry and not await _vector_geometry_available(
            data, app_maps, hint,
        ):
            return {
                "available": False, "source": "batch_vector_map",
                "error": batch_error or app_error or (
                    "No vector map data returned by the batch map path."
                ),
            }
        details = await async_read_device_state(
            client,
            lambda _device: vector_map_details(
                data, hint, client=client, app_maps=app_maps,
                geometry_generation=generation,
            ),
            refresh=False,
        )
        if not details["available"] and app_error is not None:
            details["error"] = app_error
        return details

    return await client._async_cloud_read(read)


async def async_vector_view(
    client: DreameLawnMowerClient,
    *,
    current_map_index: int | None,
    label_scale: float,
    style: MapRenderStyle | None,
    prefetched_app_maps: dict[str, Any] | None = None,
    geometry_generation: int | None = None,
) -> DreameLawnMowerMapView:
    async def read(cloud: DreameCloudSession) -> DreameLawnMowerMapView:
        generation = (
            client._runtime_geometry_generation
            if geometry_generation is None else geometry_generation
        )
        batch_error = None
        try:
            data = await cloud.async_get_batch_device_datas(client._descriptor.did, [])
        except DreameLawnMowerConnectionError as err:
            data = None
            batch_error = str(err)
        hint = (
            current_map_index
            if current_map_index is not None
            else await _map_hint(client)
        )
        app_maps = current_prefetched_app_maps(client, prefetched_app_maps)
        app_error = None
        needs_app_geometry = await _needs_app_geometry(data, hint)
        if needs_app_geometry:
            try:
                app_maps = await client.async_get_app_maps(
                    include_payload=True, include_objects=False,
                )
            except Exception as err:  # noqa: BLE001 - retain other camera sources
                app_error = str(err)
            if app_maps is None:
                app_maps = await _run_state_worker(
                    lambda: current_prefetched_app_maps(client, prefetched_app_maps),
                    Event(),
                )
            if hint is None and app_maps is not None:
                hint = app_maps.get("current_map_index")
        if needs_app_geometry and not await _vector_geometry_available(
            data, app_maps, hint,
        ):
            return DreameLawnMowerMapView(
                source="batch_vector_map", error=batch_error or app_error or (
                    "No vector map data returned by the batch map path."
                ),
            )
        view = await async_read_device_state(
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
        return replace(view, error=app_error) if (
            not view.available and app_error is not None
        ) else view

    return await client._async_cloud_read(read)
