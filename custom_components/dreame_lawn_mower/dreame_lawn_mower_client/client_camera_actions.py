"""Camera commands through the existing device action policy and native owner."""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

from .client_app_reads import async_command_app_action
from .client_camera import (
    camera_feature_support_from_device,
    require_photo_response,
    require_photo_support,
    require_stream_toggle_response,
    stream_toggle_action,
)
from .client_device_actions import async_run_device_plan
from .client_state_reads import async_read_device_state
from .device_action_plan import ActionDelay, ActionRequest, device_action_plan
from .device_types import DreameMowerAction
from .exceptions import (
    DeviceException,
    DreameLawnMowerConnectionError,
    InvalidActionException,
)

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_set_camera_stream_enabled(
    client: DreameLawnMowerClient, enabled: bool,
) -> Any:
    """Refresh and guard starts; keep stopping independent of fresh state."""
    async def run(_cloud: DreameCloudSession) -> Any:
        if enabled:
            await async_read_device_state(
                client, client._guard_camera_stream_probe_idle, refresh=True
            )
        response = await async_command_app_action(
            client, stream_toggle_action(enabled), deadline=time.monotonic() + 20
        )
        return require_stream_toggle_response(response)

    return await client._async_cloud_read(run)


def _photo_plan(
    device: Any, parameters: Any,
) -> Generator[ActionDelay | ActionRequest, Any, Any]:
    # The native driver holds the state lock through guard and action preparation.
    require_photo_support(camera_feature_support_from_device(device))
    result = yield from device_action_plan(
        device, DreameMowerAction.GET_PHOTO_INFO, parameters
    )
    return require_photo_response(result)


async def async_request_photo_info(
    client: DreameLawnMowerClient, parameters: Any,
) -> Any:
    """Own the guarded request without replaying an uncertain photo command."""
    try:
        return await async_run_device_plan(
            client, lambda device: _photo_plan(device, parameters)
        )
    except (DeviceException, InvalidActionException) as err:
        raise DreameLawnMowerConnectionError(str(err)) from err
