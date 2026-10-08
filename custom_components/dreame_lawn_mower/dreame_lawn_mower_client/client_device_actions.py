"""Native execution of the device's shared state and action policy."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Generator
from threading import Event
from typing import TYPE_CHECKING, Any

from .client_refresh import _run_state_worker
from .client_rpc import async_device_rpc
from .client_state_reads import read_locked_device_state
from .device_action_plan import (
    ActionDelay,
    ActionRequest,
    DevicePlanEffect,
    DeviceReconnect,
    MapProperties,
    PropertyReadRequest,
    PropertyRequest,
    PropertyResponse,
)
from .exceptions import DeviceException, DreameLawnMowerConnectionError

_ACTION_TIMEOUT = 20
_ACTION_CLEANUP_GRACE = 2.0
_LOGGER = logging.getLogger(__name__)


class _PlanCleanupExpired(Exception):
    """The state lock stayed unavailable beyond the cleanup grace period."""

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .client_cleanup import OwnedCleanup
    from .cloud_session import DreameCloudSession


async def async_run_device_plan(
    client: DreameLawnMowerClient,
    create_plan: Callable[[Any], Generator[DevicePlanEffect, Any, Any]],
    *,
    _cleanup: OwnedCleanup | None = None,
    require_device: Callable[[Any], None] | None = None,
    deadline: float | None = None,
) -> Any:
    """Own bounded state steps, cancellable delays and non-replayed actions."""
    action_deadline = time.monotonic() + _ACTION_TIMEOUT
    deadline = (
        min(deadline, action_deadline) if deadline is not None else action_deadline
    )
    if _cleanup is not None:
        _cleanup.require_active(client)
        deadline = min(deadline, _cleanup.deadline)
    cancelled = Event()
    attempted = False

    async def run(_cloud: DreameCloudSession) -> Any:
        device = (
            _cleanup.device
            if _cleanup is not None
            else await _run_state_worker(
                lambda: client._ensure_device(deadline=deadline, cancelled=cancelled),
                cancelled,
            )
        )
        plan = create_plan(device)
        started = False

        def require_active() -> None:
            if _cleanup is not None:
                _cleanup.require_active(client)
            if (
                cancelled.is_set()
                or (_cleanup is None and client._closing)
                or client._device is not device
                or time.monotonic() >= deadline
            ):
                raise DreameLawnMowerConnectionError(
                    "Device action expired or was cancelled"
                )
            if require_device is not None:
                require_device(device)

        async def advance(
            response: Any = None,
            error: Exception | None = None,
        ) -> tuple[bool, Any]:
            def step(_device: Any) -> tuple[bool, Any]:
                nonlocal started
                try:
                    if not started:
                        started = True
                        return False, next(plan)
                    return False, plan.throw(error) if error else plan.send(response)
                except StopIteration as completed:
                    return True, completed.value

            return await _run_state_worker(
                lambda: read_locked_device_state(device, step, require_active),
                cancelled,
            )

        try:
            done, effect = await advance()
            while not done:
                try:
                    if isinstance(effect, ActionDelay):
                        await asyncio.sleep(effect.seconds)
                        response = None
                    elif isinstance(effect, DeviceReconnect):
                        from .client_startup import async_start_device

                        require_active()
                        if _cleanup is not None:
                            raise DreameLawnMowerConnectionError(
                                "Cannot reconnect during cleanup"
                            )
                        await async_start_device(
                            client,
                            device,
                            _cloud,
                            deadline=deadline,
                            cancelled=cancelled,
                        )
                        require_active()
                        response = None
                    elif isinstance(effect, PropertyResponse):
                        from .device_property_read import (
                            apply_device_property_response_plan,
                        )

                        require_active()
                        def response_plan(
                            current: Any,
                            response_effect: PropertyResponse = effect,
                        ) -> Generator[DevicePlanEffect, Any, bool]:
                            return apply_device_property_response_plan(
                                current,
                                response_effect.results,
                                require_fresh_state=response_effect.require_fresh_state,
                            )

                        response = await async_run_device_plan(
                            client,
                            response_plan,
                            deadline=deadline,
                            _cleanup=_cleanup,
                            require_device=lambda current: require_active(),
                        )
                    elif isinstance(effect, MapProperties):
                        from .client_map_application import NativeMapApplication

                        require_active()
                        application = NativeMapApplication(
                            client,
                            device,
                            effect.manager,
                            deadline=deadline,
                            require_device=lambda current: require_active(),
                        )
                        await application.receive_properties(effect.properties)
                        require_active()
                        response = None
                    else:

                        async def command(
                            current: Any,
                            cloud: DreameCloudSession,
                            protocol: Any,
                            request_id: int,
                            request: ActionRequest
                            | PropertyRequest
                            | PropertyReadRequest = effect,
                        ) -> Any:
                            nonlocal attempted
                            require_active()
                            if current is not device:
                                raise DreameLawnMowerConnectionError(
                                    "Device changed before action dispatch"
                                )
                            if isinstance(request, PropertyReadRequest):
                                read_deadline = (
                                    min(deadline, request.deadline)
                                    if request.deadline is not None
                                    else deadline
                                )
                                return await cloud.async_read_device_properties(
                                    client._descriptor.did,
                                    protocol._host,
                                    request_id,
                                    request.properties,
                                    deadline=read_deadline,
                                )
                            def mark_dispatched() -> None:
                                nonlocal attempted
                                attempted = True
                                if (
                                    isinstance(request, PropertyRequest)
                                    and request.on_dispatch is not None
                                ):
                                    request.on_dispatch()

                            if isinstance(request, PropertyRequest):
                                return await cloud.async_command_device_property(
                                    client._descriptor.did,
                                    protocol._host,
                                    request_id,
                                    request.siid,
                                    request.piid,
                                    request.value,
                                    deadline=deadline,
                                    on_dispatch=mark_dispatched,
                                )
                            return await cloud.async_command_device_action(
                                client._descriptor.did,
                                protocol._host,
                                request_id,
                                request.siid,
                                request.aiid,
                                request.parameters,
                                deadline=deadline,
                                on_dispatch=mark_dispatched,
                            )

                        response = await async_device_rpc(
                            client,
                            command,
                            deadline=deadline,
                            _cleanup=_cleanup,
                        )
                except Exception as error:
                    done, effect = await advance(error=error)
                else:
                    done, effect = await advance(response)
            return effect
        finally:
            # Property applications retain unstarted callbacks on close. Drain
            # that bookkeeping under the same state lock as normal plan steps,
            # even if the caller is cancelled again while the worker is queued.
            cleanup_deadline = min(deadline, time.monotonic()) + _ACTION_CLEANUP_GRACE

            def cleanup_active() -> None:
                if time.monotonic() >= cleanup_deadline:
                    raise _PlanCleanupExpired

            transferred = Event()

            def close_plan() -> None:
                try:
                    read_locked_device_state(
                        device, lambda current: plan.close(), cleanup_active
                    )
                except _PlanCleanupExpired:
                    # A finalizer can mutate callback bookkeeping. Keep the
                    # plan alive until another state owner can close it safely.
                    device._plan_cleanup.defer(plan)
                    transferred.set()
                    raise
                else:
                    transferred.set()

            async def close_owned() -> None:
                try:
                    async with asyncio.timeout(
                        max(0, cleanup_deadline - time.monotonic())
                    ):
                        await _run_state_worker(close_plan, Event())
                except TimeoutError as error:
                    # A queued worker can be stopped before its body starts.
                    # A started worker drains before returning, so this owner
                    # can transfer a still-unclosed plan exactly once.
                    if not transferred.is_set():
                        device._plan_cleanup.defer(plan)
                    raise _PlanCleanupExpired from error

            closing = asyncio.create_task(close_owned())
            interrupted = False
            while not closing.done():
                try:
                    await asyncio.wait({closing})
                except asyncio.CancelledError:
                    interrupted = True
            try:
                closing.result()
            except _PlanCleanupExpired:
                _LOGGER.warning("Deferred device-plan cleanup while state was busy")
            if interrupted:
                raise asyncio.CancelledError

    async def bounded(cloud: DreameCloudSession) -> Any:
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                return await run(cloud)
        except TimeoutError as error:
            if attempted:
                raise DeviceException(
                    "Device action acknowledgement timed out"
                ) from error
            raise DreameLawnMowerConnectionError("Device action timed out") from error
        except DreameLawnMowerConnectionError as error:
            if attempted:
                raise DeviceException(str(error)) from error
            raise
        finally:
            cancelled.set()

    if _cleanup is not None:
        return await bounded(_cleanup.cloud)
    return await client._async_cloud_read(bounded)
