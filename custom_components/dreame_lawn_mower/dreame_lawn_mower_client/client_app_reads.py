"""Native app reads sharing device ownership with legacy commands."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable, Generator, Mapping
from threading import Event
from typing import TYPE_CHECKING, Any

from .app_read_transport import AppReadRequest, capture_app_result
from .client_refresh import _run_state_worker
from .cloud_wire import cloud_strings
from .exceptions import DreameLawnMowerConnectionError

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_read_app_action(
    client: DreameLawnMowerClient,
    action: Mapping[str, Any],
    *,
    deadline: float,
    strict_response: bool = False,
) -> Any:
    """Keep setup, routing and RPC within one owned, bounded native read."""
    if action.get("m") != "g":
        raise ValueError("App action read requires m='g'")
    if not math.isfinite(deadline):
        raise ValueError("App read deadline must be finite")
    return await _async_app_action(
        client,
        action,
        deadline=deadline,
        strict_response=strict_response,
    )


async def async_command_app_action(
    client: DreameLawnMowerClient,
    action: Mapping[str, Any],
    *,
    deadline: float,
    on_dispatch: Callable[[], None] | None = None,
) -> Any:
    """Share device routing and RPC ownership without retrying mutations."""
    if action.get("m") not in {"a", "s"}:
        raise ValueError("App command requires m='a' or m='s'")
    if not math.isfinite(deadline):
        raise ValueError("App command deadline must be finite")
    return await _async_app_action(
        client,
        action,
        deadline=deadline,
        command=True,
        on_dispatch=on_dispatch,
    )


async def _async_app_action(
    client: DreameLawnMowerClient,
    action: Mapping[str, Any],
    *,
    deadline: float,
    strict_response: bool = False,
    command: bool = False,
    on_dispatch: Callable[[], None] | None = None,
) -> Any:
    cancelled = Event()

    async def read(cloud: DreameCloudSession) -> Any:
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                device = await _run_state_worker(
                    lambda: client._ensure_device(
                        deadline=deadline, cancelled=cancelled
                    ),
                    cancelled,
                )
                protocol = device._protocol.cloud
                if protocol is None:
                    raise DreameLawnMowerConnectionError(
                        "Cloud protocol is unavailable"
                    )
                async with protocol.async_rpc_operation(
                    deadline=deadline
                ) as request_id:
                    if client._closing or client._device is not device:
                        raise DreameLawnMowerConnectionError(
                            "Device changed during app operation"
                        )
                    if not protocol._host:
                        info = await cloud.async_get_device_info(
                            client._descriptor.did,
                            deadline=deadline,
                        )
                        if not info or str(info.get("did")) != client._descriptor.did:
                            raise DreameLawnMowerConnectionError(
                                "Cloud device identity is invalid"
                            )
                        host = info.get(cloud_strings(client._account_type)[9])
                        if not isinstance(host, str) or not host:
                            raise DreameLawnMowerConnectionError(
                                "Cloud device routing is invalid"
                            )
                        try:
                            protocol._handle_device_info(info)
                        except (KeyError, TypeError, ValueError) as err:
                            raise DreameLawnMowerConnectionError(
                                "Cloud device routing is invalid"
                            ) from err
                    if client._closing or client._device is not device:
                        raise DreameLawnMowerConnectionError(
                            "Device changed during app operation"
                        )
                    if command:
                        return await cloud.async_command_app_action(
                            client._descriptor.did,
                            protocol._host,
                            request_id,
                            action,
                            deadline=deadline,
                            on_dispatch=on_dispatch,
                            timeout=max(0.001, deadline - time.monotonic()),
                        )
                    return await cloud.async_read_app_action(
                        client._descriptor.did,
                        protocol._host,
                        request_id,
                        action,
                        deadline=deadline,
                        strict_response=strict_response,
                        timeout=max(0.001, deadline - time.monotonic()),
                    )
        except TimeoutError as err:
            raise DreameLawnMowerConnectionError("App operation timed out") from err
        finally:
            cancelled.set()

    return await client._async_cloud_read(read)


async def async_run_app_read(
    client: DreameLawnMowerClient,
    plan: Generator[AppReadRequest, Any, dict[str, Any]],
) -> dict[str, Any]:
    """Own all requests and cleanup in one read-only protocol plan."""
    async def read(_cloud: DreameCloudSession) -> dict[str, Any]:
        result: list[dict[str, Any]] = []
        plan_with_result = capture_app_result(plan, result)
        try:
            request = next(plan_with_result)
            while True:
                deadline = time.monotonic() + request.timeout
                if request.deadline is not None:
                    deadline = min(deadline, request.deadline)
                try:
                    response = await async_read_app_action(
                        client,
                        request.action,
                        deadline=deadline,
                    )
                except Exception as error:
                    request = plan_with_result.throw(error)
                else:
                    request = plan_with_result.send(response)
        except StopIteration:
            return result[0]
        finally:
            plan_with_result.close()

    return await client._async_cloud_read(read)
