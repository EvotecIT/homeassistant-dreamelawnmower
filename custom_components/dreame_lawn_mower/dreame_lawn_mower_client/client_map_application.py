"""Ordered native frame acquisition and application for Dreame/MOVA clients."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any

from .client_device_actions import async_run_device_plan
from .client_map_frames import (
    async_download_frame_object,
    async_request_map_frame,
    async_request_next_map_frame,
)
from .client_map_maintenance import async_refresh_saved_map_list
from .client_state_reads import async_read_device_state
from .const import (
    MAP_REQUEST_PARAMETER_FORCE_TYPE,
    MAP_REQUEST_PARAMETER_FRAME_TYPE,
    MAP_REQUEST_PARAMETER_REQ_TYPE,
)
from .device_action_plan import ActionDelay, ActionRequest, PropertyRequest
from .exceptions import DreameLawnMowerConnectionError
from .map_frame_request import MapUpdateRequest

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession
    from .device import DreameMowerDevice
    from .map_manager import DreameMapMowerMapManager


class NativeMapApplication:
    """Keep one device, manager and deadline through nested frame follow-ups."""

    def __init__(
        self,
        client: DreameLawnMowerClient,
        device: DreameMowerDevice,
        manager: DreameMapMowerMapManager,
        *,
        deadline: float,
    ) -> None:
        if not math.isfinite(deadline):
            raise ValueError("Map application deadline must be finite")
        self.client, self.device, self.manager = client, device, manager
        self.deadline = deadline

    def require_owner(self, device: DreameMowerDevice) -> None:
        if (
            device is not self.device
            or device._map_manager is not self.manager
            or self.manager._disconnected
        ):
            raise DreameLawnMowerConnectionError("Map application owner changed")
        if time.monotonic() >= self.deadline:
            raise DreameLawnMowerConnectionError("Map application timed out")

    async def state[T](self, operation: Callable[[], T]) -> T:
        def apply(device: DreameMowerDevice) -> T:
            self.require_owner(device)
            return operation()

        return await async_read_device_state(self.client, apply, refresh=False)

    async def request_next(self, map_id: int, frame_id: int) -> bool | None:
        """Own RPC, downloads, decoded frames and all resulting follow-ups."""

        async def run(_cloud: DreameCloudSession) -> bool | None:
            async with asyncio.timeout(max(0, self.deadline - time.monotonic())):
                await self.state(lambda: None)
                result = await async_request_next_map_frame(
                    self.client,
                    self.device,
                    self.manager,
                    map_id,
                    frame_id,
                    deadline=self.deadline,
                )
                if result is None:
                    return None
                if not self.manager._map_action_succeeded(result):
                    return False
                name, raw, timestamp = await self.state(
                    lambda: self.manager._read_p_map_response(result)
                )
                if name:
                    await self.object_frame(name, timestamp)
                if raw:
                    await self.raw_frame(raw, timestamp)
                if (
                    not name
                    and not raw
                    and await self.state(lambda: self.manager._vslam_map)
                ):
                    await self.new_map()
                    return False
                return True

        try:
            return await self.client._async_cloud_read(run)
        except TimeoutError as error:
            raise DreameLawnMowerConnectionError("Map application timed out") from error

    async def raw_frame(
        self, raw: str, timestamp: int | None, key: str | None = None
    ) -> None:
        partial = await self.state(
            lambda: self.manager._decode_map_partial(raw, timestamp, key)
        )
        plan = self.manager._add_map_data_plan(partial)

        def advance() -> MapUpdateRequest | None:
            try:
                return next(plan)
            except StopIteration:
                return None

        try:
            while (request := await self.state(advance)) is not None:
                await self.follow_up(request)
        finally:
            # The frame plan has no state-mutating finalizers.
            plan.close()

    async def object_frame(self, name: str, timestamp: int | None) -> None:
        payload, key = await async_download_frame_object(
            self.client,
            self.device,
            self.manager,
            name,
            deadline=self.deadline,
        )
        if payload:
            await self.raw_frame(payload.decode(), timestamp, key)

    async def base_map(self) -> bool:
        result = await async_request_map_frame(
            self.client,
            {
                MAP_REQUEST_PARAMETER_REQ_TYPE: 1,
                MAP_REQUEST_PARAMETER_FRAME_TYPE: "I",
                MAP_REQUEST_PARAMETER_FORCE_TYPE: 1,
            },
            deadline=self.deadline,
            manager=self.manager,
        )
        if not self.manager._map_action_succeeded(result):
            # The supported Dreame/MOVA cloud owner's legacy fallback is a no-op.
            return False
        name, raw, timestamp = await self.state(lambda: self._base_response(result))
        if name:
            await self.object_frame(name, timestamp)
            return True
        if raw:
            await self.raw_frame(raw, timestamp)
            return True
        return False

    def _base_response(
        self, result: object
    ) -> tuple[str | None, str | None, int | None]:
        if not self.manager._map_action_succeeded(result):
            return None, None, None
        name, raw = self.manager._read_i_map_response(result, None)
        return name, raw, self.manager._last_robot_time

    async def new_map(self) -> None:
        def prepare() -> bool:
            self.manager._new_map_request_time = time.time()
            return self.manager._map_data is None

        if await self.state(prepare):
            await self.base_map()
        else:
            await async_request_map_frame(
                self.client,
                None,
                deadline=self.deadline,
                manager=self.manager,
            )

    async def follow_up(self, request: MapUpdateRequest) -> None:
        await self.state(lambda: None)
        if request.kind == "changed":

            def notification(
                device: DreameMowerDevice,
            ) -> Generator[ActionDelay | ActionRequest | PropertyRequest, Any]:
                # This step runs under the driver's state lock, so removing the
                # listener cannot race a separate registration check.
                if self.manager._change_callback is not None:
                    yield from device._map_changed_plan()

            await async_run_device_plan(
                self.client,
                notification,
                require_device=self.require_owner,
            )
        elif request.kind == "base":
            await self.base_map()
        elif request.kind == "next":
            if request.map_id is not None and request.frame_id is not None:
                await self.request_next(request.map_id, request.frame_id)
        elif request.kind == "full":
            await async_request_map_frame(
                self.client,
                None,
                deadline=self.deadline,
                manager=self.manager,
            )
        elif request.kind == "missing":
            parameters = await self.state(self.manager._prepare_missing_p_map)
            if parameters is not None:
                await async_request_map_frame(
                    self.client,
                    parameters,
                    deadline=self.deadline,
                    manager=self.manager,
                )
        elif request.kind == "list":
            await async_refresh_saved_map_list(
                self.client,
                expected_owner=(self.device, self.manager),
            )
