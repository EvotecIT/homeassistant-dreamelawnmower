"""Native map payload reads sharing the legacy cursor transaction lock."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from .client_app_reads import async_run_app_read
from .client_maps import _app_map_inventory_identity
from .exceptions import DreameLawnMowerConnectionError
from .map_read_plan import read_maps

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_read_maps(
    client: DreameLawnMowerClient,
    *,
    chunk_size: int,
    include_payload: bool,
    include_objects: bool,
    include_object_urls: bool,
) -> dict[str, Any]:
    """Hold map selection through chunks, cache commits and object inventory."""

    async def read(_cloud: DreameCloudSession) -> dict[str, Any]:
        lock = client._app_map_download_lock
        acquired = False
        try:
            try:
                async with asyncio.timeout(15):
                    while not lock.acquire(blocking=False):
                        await asyncio.sleep(0.01)
                    acquired = True
            except TimeoutError as err:
                raise DreameLawnMowerConnectionError(
                    "Map read timed out waiting for another operation"
                ) from err
            result = await async_run_app_read(
                client,
                read_maps(client._app_map_payload_cache, chunk_size, include_payload),
            )
            identity = _app_map_inventory_identity(result["maps"])
            cache_lock = client._app_map_object_cache_lock
            cache_acquired = False
            try:
                async with asyncio.timeout(15):
                    while not cache_lock.acquire(blocking=False):
                        await asyncio.sleep(0.01)
                    cache_acquired = True
                    client._set_app_map_inventory_identity(identity)
            except TimeoutError as err:
                raise DreameLawnMowerConnectionError(
                    "Map read timed out updating inventory"
                ) from err
            finally:
                if cache_acquired:
                    cache_lock.release()
            if include_objects:
                try:
                    result["objects"] = await client.async_get_app_map_objects(
                        include_urls=include_object_urls,
                    )
                except Exception as err:  # noqa: BLE001 - diagnostic evidence
                    result["objects"] = {"error": str(err)}
            return result
        finally:
            if acquired:
                lock.release()

    return await client._async_cloud_read(read)
