"""Native execution of the device's shared state and action policy."""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Generator
from threading import Event
from typing import TYPE_CHECKING, Any

from .client_refresh import _run_state_worker
from .client_rpc import async_device_rpc
from .client_state_reads import read_locked_device_state
from .device_action_plan import ActionDelay, ActionRequest, PropertyRequest
from .exceptions import DeviceException, DreameLawnMowerConnectionError

_ACTION_TIMEOUT = 20

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .client_cleanup import OwnedCleanup
    from .cloud_session import DreameCloudSession


async def async_run_device_plan(
    client: DreameLawnMowerClient,
    create_plan: Callable[
        [Any], Generator[ActionDelay | ActionRequest | PropertyRequest, Any, Any]
    ],
    *,
    _cleanup: OwnedCleanup | None = None,
) -> Any:
    """Own bounded state steps, cancellable delays and non-replayed actions."""
    deadline = time.monotonic() + _ACTION_TIMEOUT
    if _cleanup is not None:
        _cleanup.require_active(client)
        deadline = min(deadline, _cleanup.deadline)
    cancelled = Event()
    attempted = False

    async def run(_cloud: DreameCloudSession) -> Any:
        device = _cleanup.device if _cleanup is not None else await _run_state_worker(
            lambda: client._ensure_device(deadline=deadline, cancelled=cancelled),
            cancelled,
        )
        plan = create_plan(device)
        started = False

        def require_active() -> None:
            if _cleanup is not None:
                _cleanup.require_active(client)
            if (cancelled.is_set() or (_cleanup is None and client._closing)
                    or client._device is not device
                    or time.monotonic() >= deadline):
                raise DreameLawnMowerConnectionError(
                    "Device action expired or was cancelled"
                )

        async def advance(
            response: Any = None, error: Exception | None = None,
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
                    else:

                        async def command(
                            current: Any,
                            cloud: DreameCloudSession,
                            protocol: Any,
                            request_id: int,
                            request: ActionRequest | PropertyRequest = effect,
                        ) -> Any:
                            nonlocal attempted
                            require_active()
                            if current is not device:
                                raise DreameLawnMowerConnectionError(
                                    "Device changed before action dispatch"
                                )
                            attempted = True
                            if isinstance(request, PropertyRequest):
                                return await cloud.async_command_device_property(
                                    client._descriptor.did, protocol._host, request_id,
                                    request.siid, request.piid, request.value,
                                    deadline=deadline,
                                )
                            return await cloud.async_command_device_action(
                                client._descriptor.did, protocol._host, request_id,
                                request.siid, request.aiid, request.parameters,
                                deadline=deadline,
                            )
                        response = await async_device_rpc(
                            client, command, deadline=deadline, _cleanup=_cleanup,
                        )
                except Exception as error:
                    done, effect = await advance(error=error)
                else:
                    done, effect = await advance(response)
            return effect
        finally:
            # Plans contain no state-mutating finalizers; GeneratorExit skips
            # acknowledgement effects when cancellation interrupts transport.
            plan.close()

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
