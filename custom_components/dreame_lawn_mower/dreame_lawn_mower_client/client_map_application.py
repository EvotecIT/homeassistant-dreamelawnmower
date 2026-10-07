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
    MAP_PARAMETER_TIME,
    MAP_REQUEST_PARAMETER_FORCE_TYPE,
    MAP_REQUEST_PARAMETER_FRAME_TYPE,
    MAP_REQUEST_PARAMETER_REQ_TYPE,
)
from .device_action_plan import ActionDelay, ActionRequest, PropertyRequest
from .exceptions import DreameLawnMowerCloudAPIError, DreameLawnMowerConnectionError
from .map_frame_request import MapUpdateRequest

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession
    from .device import DreameMowerDevice
    from .map_manager import DreameMapMowerMapManager
    from .map_types import MapDataPartial


class NativeMapApplication:
    """Keep one device, manager and deadline through nested frame follow-ups."""

    def __init__(
        self,
        client: DreameLawnMowerClient,
        device: DreameMowerDevice,
        manager: DreameMapMowerMapManager,
        *,
        deadline: float,
        require_device: Callable[[DreameMowerDevice], None] | None = None,
    ) -> None:
        if not math.isfinite(deadline):
            raise ValueError("Map application deadline must be finite")
        self.client, self.device, self.manager = client, device, manager
        self.protocol = device._protocol
        self.cloud_protocol = device._protocol.cloud
        self.deadline = deadline
        self._require_device = require_device

    def require_owner(self, device: DreameMowerDevice) -> None:
        if self._require_device is not None:
            self._require_device(device)
        if (
            device is not self.device
            or device._map_manager is not self.manager
            or device._protocol is not self.protocol
            or device._protocol.cloud is not self.cloud_protocol
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
                try:
                    result = await async_request_next_map_frame(
                        self.client,
                        self.device,
                        self.manager,
                        map_id,
                        frame_id,
                        deadline=self.deadline,
                    )
                except (DreameLawnMowerCloudAPIError, DreameLawnMowerConnectionError):
                    await self.state(lambda: None)
                    return False
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
    ) -> bool:
        partial = await self.state(
            lambda: self.manager._decode_map_partial(raw, timestamp, key)
        )
        return await self._apply_plan(self.manager._add_map_data_plan(partial))

    async def _apply_plan[T](self, plan: Generator[MapUpdateRequest, None, T]) -> T:
        result: T

        def advance() -> MapUpdateRequest | None:
            nonlocal result
            try:
                return next(plan)
            except StopIteration as completed:
                result = completed.value
                return None

        try:
            while (request := await self.state(advance)) is not None:
                await self.follow_up(request)
            return result
        finally:
            # The frame plan has no state-mutating finalizers.
            plan.close()

    async def object_frame(self, name: str, timestamp: int | None) -> None:
        payload, key = await self._download_object(name)
        if payload:
            await self.raw_frame(payload.decode(), timestamp, key)

    async def receive_properties(self, properties: list[dict[str, Any]]) -> None:
        """Own MQTT map preparation and its ordered cloud follow-ups."""

        async def run(_cloud: DreameCloudSession) -> None:
            async with asyncio.timeout(max(0, self.deadline - time.monotonic())):
                prepared = await self.state(
                    lambda: self.manager._prepare_map_properties(properties)
                )
                if prepared is None:
                    return
                partials, name, timestamp = prepared
                if name:
                    await self.cloud_object(name, timestamp, partials=partials)
                else:
                    await self._apply_plan(
                        self.manager._add_cloud_map_prefix_plan(partials, None)
                    )

        try:
            await self.client._async_cloud_read(run)
        except TimeoutError as error:
            raise DreameLawnMowerConnectionError("Map delivery timed out") from error

    async def cloud_object(
        self,
        name: str,
        timestamp: int | None,
        *,
        partials: list[MapDataPartial] | None = None,
    ) -> bool | None:
        """Queue future incremental objects until their predecessor arrives."""
        await self._apply_plan(self.manager._add_cloud_map_prefix_plan(partials, name))
        payload, key = await self._download_object(name)
        if not payload:
            return None
        partial = await self.state(
            lambda: self.manager._decode_map_partial(payload.decode(), timestamp, key)
        )
        return await self._apply_plan(self.manager._add_object_map_plan(partial))

    async def _download_object(self, name: str) -> tuple[bytes | None, str | None]:
        try:
            return await async_download_frame_object(
                self.client,
                self.device,
                self.manager,
                name,
                deadline=self.deadline,
            )
        except (DreameLawnMowerCloudAPIError, DreameLawnMowerConnectionError):
            # Failed acquisition is best effort only while this operation still
            # owns live resources and has time to try the remaining payload.
            await self.state(lambda: None)
            return None, None

    async def _request_frame(self, parameters: dict[str, Any] | None) -> Any:
        try:
            return await async_request_map_frame(
                self.client,
                parameters,
                deadline=self.deadline,
                manager=self.manager,
            )
        except (DreameLawnMowerCloudAPIError, DreameLawnMowerConnectionError):
            await self.state(lambda: None)
            return None

    async def request_current(self, start_time: int | None = None) -> bool:
        """Return the applied current-map result before the caller chooses fallback."""

        async def run(_cloud: DreameCloudSession) -> bool:
            async with asyncio.timeout(max(0, self.deadline - time.monotonic())):
                await self.state(lambda: None)
                return await self.base_map(start_time)

        try:
            return await self.client._async_cloud_read(run)
        except TimeoutError as error:
            raise DreameLawnMowerConnectionError("Map application timed out") from error

    async def base_map(self, start_time: int | None = None) -> bool:
        parameters: dict[str, Any] = {
            MAP_REQUEST_PARAMETER_REQ_TYPE: 1,
            MAP_REQUEST_PARAMETER_FRAME_TYPE: "I",
            MAP_REQUEST_PARAMETER_FORCE_TYPE: 1,
        }
        if start_time:
            parameters[MAP_PARAMETER_TIME] = start_time
        result = await self._request_frame(parameters)
        if not self.manager._map_action_succeeded(result):
            # The supported Dreame/MOVA cloud owner's legacy fallback is a no-op.
            return False
        name, raw, timestamp = await self.state(
            lambda: self._base_response(result, start_time)
        )
        if name:
            await self.object_frame(name, timestamp)
            return True
        if raw:
            await self.raw_frame(raw, timestamp)
            return True
        return False

    def _base_response(
        self, result: object, start_time: int | None
    ) -> tuple[str | None, str | None, int | None]:
        if not self.manager._map_action_succeeded(result):
            return None, None, None
        name, raw = self.manager._read_i_map_response(result, start_time)
        return name, raw, self.manager._last_robot_time

    async def new_map(self) -> None:
        def prepare() -> bool:
            self.manager._new_map_request_time = time.time()
            return self.manager._map_data is None

        if await self.state(prepare):
            await self.base_map()
        else:
            await self._request_frame(None)

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
        elif request.kind == "new":
            await self.new_map()
        elif request.kind == "next":
            if request.map_id is not None and request.frame_id is not None:
                await self.request_next(request.map_id, request.frame_id)
        elif request.kind == "full":
            await self._request_frame(None)
        elif request.kind == "missing":
            parameters = await self.state(self.manager._prepare_missing_p_map)
            if parameters is not None:
                await self._request_frame(parameters)
        elif request.kind == "list":
            await async_refresh_saved_map_list(
                self.client,
                expected_owner=(self.device, self.manager),
            )
