"""Native downloads for the existing map manager's reconciliation policy."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from .client_state_reads import async_read_device_state
from .exceptions import DreameLawnMowerConnectionError

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession
    from .device import DreameMowerDevice
    from .map_manager import DreameMapMowerMapManager

_LOGGER = logging.getLogger(__name__)


class NativeMapLists:
    """Own list refreshes requested by the existing map worker and callbacks."""

    def __init__(
        self, client: DreameLawnMowerClient, device: DreameMowerDevice
    ) -> None:
        self._client = client
        self._device = device
        self._manager = device._map_manager
        self._loop = asyncio.get_running_loop()
        self._tasks: dict[bool, asyncio.Task[None]] = {}
        self._refresh_lock = asyncio.Lock()

    def request(self, recovery: bool) -> None:
        if self._client._closing or self._loop.is_closed():
            return
        try:
            self._loop.call_soon_threadsafe(self._start, recovery)
        except RuntimeError:
            if not self._loop.is_closed():
                raise

    def _start(self, recovery: bool) -> None:
        task = self._tasks.get(recovery)
        if (
            self._client._closing
            or self._client._device is not self._device
            or self._manager is None
            or self._manager._disconnected
            or self._device._map_manager is not self._manager
            or (task is not None and not task.done())
        ):
            return
        task = asyncio.create_task(self._refresh(recovery))
        self._tasks[recovery] = task
        self._client._cloud_read_tasks.add(task)
        task.add_done_callback(self._completed)

    async def _refresh(self, recovery: bool) -> None:
        # Saved maps must be applied before recovery metadata references them.
        async with self._refresh_lock:
            await async_refresh_saved_map_list(self._client, recovery=recovery)

    def _completed(self, task: asyncio.Task[None]) -> None:
        self._client._cloud_read_tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                _LOGGER.warning("Map list refresh failed (%s)", type(error).__name__)


async def async_refresh_saved_map_list(
    client: DreameLawnMowerClient,
    *,
    recovery: bool = False,
) -> None:
    """Download outside state ownership, then reject stale manager/object results."""
    deadline = time.monotonic() + 20

    def snapshot(
        device: DreameMowerDevice,
    ) -> (
        tuple[
            DreameMowerDevice,
            DreameMapMowerMapManager,
            str,
            str | None,
        ]
        | None
    ):
        manager = device._map_manager
        if manager is None or manager._disconnected:
            return None
        if (
            recovery
            and manager._map_list_object_name
            and manager._need_map_list_request is not False
        ):
            # Recovery entries refer to saved map IDs. A failed or stale saved
            # refresh must keep recovery pending for the next maintenance cycle.
            return None
        name = (
            manager._recovery_map_list_object_name
            if recovery
            else manager._map_list_object_name
        )
        return (device, manager, name, manager._map_list_md5) if name else None

    async def refresh(cloud: DreameCloudSession) -> None:
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                state = await async_read_device_state(client, snapshot, refresh=False)
                if state is None:
                    return
                device, manager, object_name, md5 = state
                url = await cloud.async_get_interim_file_url(
                    client._descriptor.did,
                    client._descriptor.model,
                    object_name,
                    deadline=deadline,
                )
                if not url:
                    return
                payload = await cloud.async_get_public_file(url, deadline=deadline)

                def apply(current: DreameMowerDevice) -> None:
                    current_name = (
                        manager._recovery_map_list_object_name
                        if recovery
                        else manager._map_list_object_name
                    )
                    if (
                        current is device
                        and current._map_manager is manager
                        and not manager._disconnected
                        and current_name == object_name
                        and manager._map_list_md5 == md5
                    ):
                        if recovery:
                            manager._need_recovery_map_list_request = False
                            manager._apply_recovery_map_list(payload)
                        else:
                            manager._apply_map_list(payload)

                await async_read_device_state(client, apply, refresh=False)
        except TimeoutError as error:
            raise DreameLawnMowerConnectionError(
                "Saved map list refresh timed out"
            ) from error

    await client._async_cloud_read(refresh)
