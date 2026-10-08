"""Native camera handshake with cleanup retained through cancellation and close."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Generator
from threading import Event
from typing import TYPE_CHECKING, Any

from .client_app_reads import async_command_app_action
from .client_camera import (
    _validate_stream_operation,
    _validate_stream_payload_mode,
    require_stream_toggle_response,
    stream_toggle_action,
)
from .client_camera_reads import async_camera_feature_support
from .client_cleanup import OwnedCleanup, finish_owned_cleanup
from .client_device_actions import async_run_device_plan
from .client_refresh import _run_state_worker, async_update_device
from .client_state_reads import async_read_device_state, read_locked_device_state
from .device_action_plan import ActionDelay, ActionRequest, device_action_plan
from .device_types import PIID, DreameMowerAction, DreameMowerProperty
from .exceptions import DreameLawnMowerConnectionError
from .payload_utils import _json_safe
from .stream_commands import stream_action_parameters

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


def _stream_plan(
    device: Any, operation: str, oper_type: str, payload_mode: str,
) -> Generator[ActionDelay | ActionRequest, Any, Any]:
    payload = {"operType": oper_type, "operation": operation}
    if payload_mode == "empty_session":
        payload["session"] = ""
    parameters = stream_action_parameters(
        PIID(DreameMowerProperty.STREAM_STATUS), payload,
        include_session=payload_mode == "with_session",
        session=device.status.stream_session,
    )
    return (yield from device_action_plan(
        device, DreameMowerAction.STREAM_VIDEO, parameters,
    ))


async def async_camera_handshake(
    client: DreameLawnMowerClient, *, timeout: float, interval: float,
    operation: str, payload_mode: str,
) -> dict[str, Any]:
    """Own start/poll/end and drain a bounded cleanup before releasing resources."""
    operation = _validate_stream_operation(operation)
    payload_mode = _validate_stream_payload_mode(payload_mode)
    if not math.isfinite(timeout) or not math.isfinite(interval):
        raise ValueError("Handshake timeout and interval must be finite")

    async def command(oper_type: str, cleanup: OwnedCleanup | None = None) -> Any:
        if payload_mode == "app_action":
            response = await async_command_app_action(
                client, stream_toggle_action(oper_type == "start"),
                deadline=time.monotonic() + 20, _cleanup=cleanup,
            )
            return require_stream_toggle_response(response)
        return await async_run_device_plan(
            client, lambda device: _stream_plan(
                device, operation, oper_type, payload_mode,
            ), _cleanup=cleanup,
        )

    async def run(cloud: DreameCloudSession) -> dict[str, Any]:
        def before_state(device: Any) -> tuple[Any, dict[str, Any]]:
            client._guard_camera_stream_probe_idle(device)
            return device, client._stream_status_payload(device)

        device, before = await async_read_device_state(
            client, before_state, refresh=True,
        )
        support = await async_camera_feature_support(
            client, refresh=False, include_cloud=True, language="en",
        )
        if not support.supported:
            raise DreameLawnMowerConnectionError(
                support.reason or "Camera/photo support is not available."
            )
        output: dict[str, Any] = {
            "operation": operation, "payload_mode": payload_mode,
            "before": before, "start_result": None, "polls": [],
            "end_result": None, "after": None, "cleanup_error": None,
        }

        async def cleanup(scope: OwnedCleanup) -> None:
            try:
                output["end_result"] = _json_safe(await command("end", scope))
            except Exception as error:
                output["cleanup_error"] = str(error)
            try:
                refreshed = await async_update_device(client, _cleanup=scope)
                output["after"] = await _run_state_worker(
                    lambda: read_locked_device_state(
                        refreshed, client._stream_status_payload,
                        lambda: scope.require_active(client),
                    ), Event(),
                )
            except Exception as error:
                if output["cleanup_error"] is None:
                    output["cleanup_error"] = str(error)

        try:
            output["start_result"] = _json_safe(await command("start"))
            deadline = time.monotonic() + max(timeout, 0)
            while True:
                if timeout > 0 and time.monotonic() >= deadline:
                    break
                poll = await async_read_device_state(
                    client, client._stream_status_payload, refresh=True,
                    deadline=deadline if timeout > 0 else None,
                )
                output["polls"].append(poll)
                if poll["stream_session_present"] or poll["stream_status"]:
                    break
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(min(
                    max(interval, 0.1), max(0, deadline - time.monotonic()),
                ))
        finally:
            try:
                await finish_owned_cleanup(client, device, cloud, cleanup)
            except Exception as error:
                if output["cleanup_error"] is None:
                    output["cleanup_error"] = str(error)
        return output

    return await client._async_cloud_read(run)
