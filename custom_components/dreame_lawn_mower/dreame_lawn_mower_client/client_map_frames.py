"""Native map-frame RPC using the existing device request-ID owner."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import TYPE_CHECKING, Any

from .client_rpc import async_device_rpc
from .client_state_reads import async_read_device_state
from .device_types import DreameMowerAction, DreameMowerActionMapping
from .exceptions import DreameLawnMowerConnectionError
from .map_frame_request import map_frame_parameters
from .public_download import MAX_PUBLIC_MAP_BYTES

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession
    from .device import DreameMowerDevice
    from .map_manager import DreameMapMowerMapManager


async def async_request_map_frame(
    client: DreameLawnMowerClient,
    parameters: dict[str, Any] | None,
    *,
    deadline: float,
    manager: DreameMapMowerMapManager | None = None,
) -> Any:
    """Dispatch once; frame decoding and cache reconciliation belong to the caller."""
    payload = map_frame_parameters(parameters)
    mapping = DreameMowerActionMapping[DreameMowerAction.REQUEST_MAP]

    async def request(
        device: Any,
        cloud: DreameCloudSession,
        protocol: Any,
        request_id: int,
    ) -> Any:
        if manager is not None and (
            device._map_manager is not manager or manager._disconnected
        ):
            raise DreameLawnMowerConnectionError("Map frame owner changed")
        return await cloud.async_command_device_action(
            client._descriptor.did,
            protocol._host,
            request_id,
            mapping["siid"],
            mapping["aiid"],
            payload,
            deadline=deadline,
        )

    return await async_device_rpc(client, request, deadline=deadline)


async def async_request_next_map_frame(
    client: DreameLawnMowerClient,
    device: DreameMowerDevice,
    manager: DreameMapMowerMapManager,
    map_id: int,
    frame_id: int,
    *, deadline: float,
) -> Any:
    """Return the response, False for an empty response, or None for a duplicate."""
    if not math.isfinite(deadline):
        raise ValueError("Map frame deadline must be finite")

    def require_owner(current: DreameMowerDevice) -> None:
        if (current is not device or current._map_manager is not manager
                or manager._disconnected):
            raise DreameLawnMowerConnectionError("Map frame owner changed")

    async def request(cloud: DreameCloudSession) -> Any:
        reserved = False

        def prepare(current: DreameMowerDevice) -> dict[str, Any] | None:
            nonlocal reserved
            require_owner(current)
            parameters = manager._prepare_next_p_map(map_id, frame_id)
            reserved = parameters is not None
            return parameters

        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                parameters = await async_read_device_state(
                    client, prepare, refresh=False
                )
                if parameters is None:
                    return None
                result = await async_request_map_frame(
                    client, parameters, deadline=deadline, manager=manager
                )
                await async_read_device_state(client, require_owner, refresh=False)
                return False if result is None else result
        except TimeoutError as error:
            raise DreameLawnMowerConnectionError(
                "Next map frame request timed out"
            ) from error
        finally:
            if reserved:
                # Queue-only cleanup must not wait for device I/O or owner lifetime.
                manager._finish_next_p_map(map_id, frame_id)

    return await client._async_cloud_read(request)


async def async_download_frame_object(
    client: DreameLawnMowerClient,
    device: DreameMowerDevice,
    manager: DreameMapMowerMapManager,
    object_name: str,
    *,
    deadline: float,
) -> tuple[bytes | None, str | None]:
    """Download a frame for its captured owner; leave decoding to the map manager."""
    if not math.isfinite(deadline):
        raise ValueError("Map frame deadline must be finite")
    name, separator, suffix = object_name.partition(",")
    key = suffix.split(",", 1)[0] if separator else None

    def require_owner(current: DreameMowerDevice) -> None:
        if (
            current is not device
            or current._map_manager is not manager
            or manager._disconnected
        ):
            raise DreameLawnMowerConnectionError("Map frame owner changed")

    async def download(cloud: DreameCloudSession) -> tuple[bytes | None, str | None]:
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                await async_read_device_state(client, require_owner, refresh=False)
                url = await cloud.async_get_interim_file_url(
                    client._descriptor.did,
                    client._descriptor.model,
                    name,
                    deadline=deadline,
                )
                payload = (
                    await cloud.async_get_public_file(
                        url, deadline=deadline, max_bytes=MAX_PUBLIC_MAP_BYTES,
                    )
                    if url
                    else None
                )
                await async_read_device_state(client, require_owner, refresh=False)
                return payload, key
        except TimeoutError as error:
            raise DreameLawnMowerConnectionError(
                "Map frame download timed out"
            ) from error

    return await client._async_cloud_read(download)


class NativeMissingMapFrames:
    """Coalesce missing-frame requests; frame delivery stays with MQTT."""

    def __init__(
        self, client: DreameLawnMowerClient, device: DreameMowerDevice
    ) -> None:
        self._client = client
        self._device = device
        self._manager = device._map_manager
        self._loop = asyncio.get_running_loop()
        self._task: asyncio.Task[None] | None = None
        self._pending = False

    def request(self) -> None:
        if self._client._closing or self._loop.is_closed():
            return
        try:
            self._loop.call_soon_threadsafe(self._start)
        except RuntimeError:
            if not self._loop.is_closed():
                raise

    def _start(self) -> None:
        if (self._client._closing or self._client._device is not self._device
                or self._manager is None or self._manager._disconnected
                or self._device._map_manager is not self._manager):
            return
        if self._task is not None and not self._task.done():
            self._pending = True
            return
        self._pending = False
        self._task = asyncio.create_task(self._request())
        self._client._cloud_read_tasks.add(self._task)
        self._task.add_done_callback(self._completed)

    def _completed(self, task: asyncio.Task[None]) -> None:
        self._client._cloud_read_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logging.getLogger(__name__).warning(
                "Missing map frame request failed (%s)",
                type(task.exception()).__name__,
            )

        if self._pending and not task.cancelled() and self._task is task:
            self._start()

    async def _request(self) -> None:
        def prepare(device: DreameMowerDevice) -> dict[str, Any] | None:
            if (device is not self._device or device._map_manager is not self._manager
                    or self._manager is None or self._manager._disconnected):
                return None
            return self._manager._prepare_missing_p_map()

        parameters = await async_read_device_state(self._client, prepare, refresh=False)
        if parameters is not None:
            await async_request_map_frame(
                self._client, parameters, deadline=time.monotonic() + 20,
                manager=self._manager,
            )
