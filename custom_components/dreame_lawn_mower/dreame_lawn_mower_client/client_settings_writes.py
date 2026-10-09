"""Native HTTP execution of shared device-setting mutation plans."""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from .client_app_reads import async_command_app_action, async_run_app_read
from .device_settings_read_plan import read_device_settings
from .device_settings_write_plan import ReadDeviceSettings, SettingsPlan
from .exceptions import DreameLawnMowerConnectionError

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_write_device_settings(
    client: DreameLawnMowerClient, plan: SettingsPlan,
) -> dict[str, Any]:
    """Own preflight, one write and readback, without replaying the mutation."""
    # At most four network stages: CFG, write, CFG, and optional RPET.
    deadline = time.monotonic() + 80

    async def execute() -> dict[str, Any]:
        request = next(plan)
        while True:
            if isinstance(request, ReadDeviceSettings):
                response = await async_run_app_read(
                    client, read_device_settings(
                        include_rain_end_time=request.include_rain_end_time,
                    ), deadline=deadline,
                )
            else:
                response = await async_command_app_action(
                    client, request.action,
                    deadline=min(deadline, time.monotonic() + 20),
                )
            try:
                request = plan.send(response)
            except StopIteration as completed:
                result: dict[str, Any] = completed.value
                return result

    async def run(_cloud: DreameCloudSession) -> dict[str, Any]:
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                async with client._device_settings_write_lock:
                    return await execute()
        except TimeoutError as error:
            raise DreameLawnMowerConnectionError(
                "Device settings update timed out"
            ) from error

    try:
        return await client._async_cloud_read(run)
    finally:
        plan.close()
