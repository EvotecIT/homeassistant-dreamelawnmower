"""Owned native execution of the shared Dreame/MOVA map polling policy."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from .client_map_application import NativeMapApplication
from .client_map_maintenance import async_refresh_saved_map_list
from .device_types import DIID, DreameMowerProperty
from .exceptions import DreameLawnMowerConnectionError
from .map_frame_request import MapUpdateRequest
from .map_poll_request import MapPollRequest

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession
    from .device import DreameMowerDevice


class NativeMapPolling:
    """Coalesce timer ticks into one captured manager's owned native poll."""

    def __init__(
        self, client: DreameLawnMowerClient, device: DreameMowerDevice
    ) -> None:
        self._client, self._device = client, device
        self._manager = device._map_manager
        self._loop = asyncio.get_running_loop()
        self._task: asyncio.Task[None] | None = None

    def request(self) -> None:
        if self._client._closing or self._loop.is_closed():
            return
        try:
            self._loop.call_soon_threadsafe(self._start)
        except RuntimeError:
            if not self._loop.is_closed():
                raise

    def _start(self) -> None:
        if (
            self._client._closing
            or self._client._device is not self._device
            or self._manager is None
            or self._manager._disconnected
            or self._device._map_manager is not self._manager
            or (self._task is not None and not self._task.done())
        ):
            return
        application = NativeMapApplication(
            self._client,
            self._device,
            self._manager,
            deadline=time.monotonic() + 20,
        )
        self._task = asyncio.create_task(async_poll_maps(application))
        self._client._cloud_read_tasks.add(self._task)
        self._task.add_done_callback(self._completed)

    def _completed(self, task: asyncio.Task[None]) -> None:
        self._client._cloud_read_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logging.getLogger(__name__).warning(
                "Map polling failed (%s)",
                type(task.exception()).__name__,
            )


async def async_poll_maps(application: NativeMapApplication) -> None:
    """Retain one owner and deadline through polling, fallback and application."""
    client, manager = application.client, application.manager

    async def run(cloud: DreameCloudSession) -> None:
        def prepare() -> None:
            if not manager._protocol.dreame_cloud:
                raise DreameLawnMowerConnectionError(
                    "Native map polling requires Dreame/MOVA cloud"
                )

        await application.state(prepare)
        plan = manager._update_plan()

        async def effect(request: MapPollRequest) -> Any:
            await application.state(lambda: None)
            if request.kind in {"list", "recovery"}:
                return await async_refresh_saved_map_list(
                    client,
                    recovery=request.kind == "recovery",
                    expected_owner=(application.device, manager),
                )
            if request.kind == "current":
                return await application.request_current(request.start_time)
            if request.kind == "object":
                property_id = DIID(DreameMowerProperty.OBJECT_NAME)
                if property_id is None:
                    raise DreameLawnMowerConnectionError(
                        "Map object property is unavailable"
                    )
                result = await cloud.async_get_properties(
                    client._descriptor.did,
                    property_id,
                    deadline=application.deadline,
                )
                if result and "value" in result[0]:
                    return await application.cloud_object(
                        result[0]["value"], result[0].get("updateDate")
                    )
                return None
            if request.kind == "full":
                return await application.follow_up(MapUpdateRequest("full"))
            if request.kind == "changed":
                return await application.follow_up(MapUpdateRequest("changed"))
            raise DreameLawnMowerConnectionError(
                "Unsupported native map polling operation"
            )

        response: Any = None
        error: Exception | None = None

        def advance() -> MapPollRequest | None:
            try:
                return plan.throw(error) if error is not None else plan.send(response)
            except StopIteration:
                return None

        try:
            while (request := await application.state(advance)) is not None:
                try:
                    response = await effect(request)
                    error = None
                except Exception as ex:
                    error = ex
        finally:
            # State readers have drained before reaching here. The plan's
            # finalizer only releases this captured manager's polling flags.
            plan.close()

    async def bounded(cloud: DreameCloudSession) -> None:
        try:
            async with asyncio.timeout(max(0, application.deadline - time.monotonic())):
                await run(cloud)
        except TimeoutError as error:
            raise DreameLawnMowerConnectionError("Map polling timed out") from error

    await client._async_cloud_read(bounded)
