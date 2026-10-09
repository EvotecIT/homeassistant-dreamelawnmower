"""Native map-object inventory reads with owned cache updates."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from threading import Event
from typing import TYPE_CHECKING, Any

from .client_app_reads import async_read_app_action
from .client_refresh import _run_state_worker
from .exceptions import DreameLawnMowerConnectionError
from .map_objects import (
    map_object_description,
    map_object_names,
    map_objects_result,
    normalized_object_names,
)

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_read_map_objects(
    client: DreameLawnMowerClient, *, include_urls: bool,
) -> dict[str, Any]:
    """Preserve privacy and map identity through native reads and cancellation."""
    deadline = time.monotonic() + 20
    cancelled = Event()

    def cache[T](operation: Callable[[], T]) -> T:
        def active() -> None:
            if cancelled.is_set() or client._closing:
                raise DreameLawnMowerConnectionError("Map object read was cancelled")
            if time.monotonic() >= deadline:
                raise DreameLawnMowerConnectionError("Map object read timed out")

        while True:
            active()
            if client._app_map_object_cache_lock.acquire(timeout=0.05):
                break
        try:
            active()
            return operation()
        finally:
            client._app_map_object_cache_lock.release()

    async def read(cloud: DreameCloudSession) -> dict[str, Any]:
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                identity = await _run_state_worker(
                    lambda: cache(lambda: client._latest_app_map_inventory_identity),
                    cancelled,
                )
                response = await async_read_app_action(
                    client, {"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
                    deadline=deadline,
                )
                names = map_object_names(response)
                normalized = normalized_object_names(names)

                def store_names() -> None:
                    if (identity is not None
                            and identity == client._latest_app_map_inventory_identity):
                        client._latest_app_map_object_names = normalized
                        client._latest_app_map_object_inventory_identity = identity

                await _run_state_worker(lambda: cache(store_names), cancelled)
                objects = []
                for raw_name in names:
                    item = map_object_description(raw_name, include_urls=include_urls)
                    if include_urls:
                        item["url_checked"] = True
                        try:
                            url = await cloud.async_get_interim_file_url(
                                client._descriptor.did, client._descriptor.model,
                                str(raw_name), deadline=deadline,
                            )
                            item["url_present"] = bool(url)
                            item["url"] = url
                        except Exception as err:  # noqa: BLE001 - per-object evidence
                            item["error"] = str(err)
                    objects.append(item)
                return map_objects_result(response, objects, include_urls=include_urls)
        except TimeoutError as err:
            raise DreameLawnMowerConnectionError("Map object read timed out") from err
        finally:
            cancelled.set()

    return await client._async_cloud_read(read)
