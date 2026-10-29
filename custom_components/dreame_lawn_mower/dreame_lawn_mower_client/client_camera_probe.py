"""Owned native camera property probing with serialized response application."""

from __future__ import annotations

import time
from collections.abc import Generator
from threading import Event
from typing import TYPE_CHECKING, Any, cast

from .camera_property_probe import camera_property_probe_plan
from .client_device_actions import async_run_device_plan
from .client_refresh import _run_state_worker
from .client_rpc import async_device_rpc
from .client_state_reads import async_read_device_state, read_locked_device_state
from .device_action_plan import PropertyResponse
from .exceptions import DreameLawnMowerConnectionError

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession

_PROPERTY_PROBE_TIMEOUT = 20
_DIAGNOSTIC_TIMEOUT = 1


async def async_camera_property_probe(client: DreameLawnMowerClient) -> dict[str, Any]:
    """Read camera properties natively and retain the legacy diagnostic policy."""
    deadline = time.monotonic() + _PROPERTY_PROBE_TIMEOUT
    cancelled = Event()

    async def read(_cloud: DreameCloudSession) -> dict[str, Any]:
        device = await async_read_device_state(
            client, lambda value: value, refresh=False)
        plan = camera_property_probe_plan(device)
        state_deadline = deadline

        def active() -> None:
            if (cancelled.is_set() or client._closing or client._device is not device
                    or time.monotonic() >= state_deadline):
                raise DreameLawnMowerConnectionError("Camera property probe expired")

        def step(response: Any = None, error: Exception | None = None,
                 *, initial: bool = False) -> tuple[bool, Any]:
            def advance(_device: Any) -> tuple[bool, Any]:
                try:
                    return False, (next(plan) if initial else
                                   plan.throw(error) if error else plan.send(response))
                except StopIteration as completed:
                    return True, completed.value

            return read_locked_device_state(device, advance, active)

        try:
            done, requested = await _run_state_worker(
                lambda: step(initial=True), cancelled)
            if done:
                return cast(dict[str, Any], requested)

            async def request(current: Any, cloud: DreameCloudSession,
                              protocol: Any, request_id: int) -> Any:
                active()
                if current is not device:
                    raise DreameLawnMowerConnectionError("Camera device changed")
                return await cloud.async_read_device_properties(
                    client._descriptor.did, protocol._host, request_id, requested,
                    deadline=deadline,
                )

            try:
                response = await async_device_rpc(client, request, deadline=deadline)
            except Exception as error:
                # Transport expiry must remain a diagnostic field. Give the
                # state-only error result a separate bounded lock budget.
                state_deadline = time.monotonic() + _DIAGNOSTIC_TIMEOUT

                def finish_error(error: Exception = error) -> tuple[bool, Any]:
                    return step(error=error)

                done, result = await _run_state_worker(finish_error, cancelled)
            else:
                done, result = await _run_state_worker(
                    lambda: step(response), cancelled)
            if not done:
                if not isinstance(result, PropertyResponse):
                    raise RuntimeError(
                        "Camera property probe requested another response"
                    )
                effect = result

                def apply(current: Any) -> Generator[PropertyResponse, Any, Any]:
                    active()
                    if current is not device:
                        raise DreameLawnMowerConnectionError("Camera device changed")
                    return (yield effect)

                try:
                    handled = await async_run_device_plan(
                        client, apply, deadline=deadline,
                        require_device=lambda current: active(),
                    )
                except Exception as error:
                    state_deadline = time.monotonic() + _DIAGNOSTIC_TIMEOUT

                    def finish_application_error(
                        error: Exception = error,
                    ) -> tuple[bool, Any]:
                        return step(error=error)

                    done, result = await _run_state_worker(
                        finish_application_error, cancelled,
                    )
                else:
                    done, result = await _run_state_worker(
                        lambda: step(handled), cancelled,
                    )
                if not done:
                    raise RuntimeError(
                        "Camera property probe requested another response"
                    )
            return cast(dict[str, Any], result)
        finally:
            cancelled.set()
            plan.close()

    return await client._async_cloud_read(read)
