"""Native maintenance reset using shared read, command and policy owners."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .client_app_reads import async_command_app_action, async_run_app_read
from .device_settings_read_plan import read_maintenance
from .maintenance_reset_plan import (
    ReadMaintenance,
    capture_maintenance_result,
    plan_maintenance_reset,
)

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_reset_maintenance(
    client: DreameLawnMowerClient, item: str, execute: bool, confirm_write: bool
) -> dict[str, Any]:
    """Own preflight, at-most-once write and confirmation within one deadline."""
    deadline = time.monotonic() + 20

    async def run(_cloud: DreameCloudSession) -> dict[str, Any]:
        result: list[dict[str, Any]] = []
        driver = capture_maintenance_result(
            plan_maintenance_reset(item, execute, confirm_write), result
        )
        try:
            request = next(driver)
            while True:
                try:
                    if isinstance(request, ReadMaintenance):
                        response = await async_run_app_read(
                            client, read_maintenance(), deadline=deadline
                        )
                    else:
                        response = await async_command_app_action(
                            client, request.action, deadline=deadline
                        )
                except Exception as error:
                    request = driver.throw(error)
                else:
                    request = driver.send(response)
        except StopIteration:
            return result[0]
        finally:
            driver.close()

    return await client._async_cloud_read(run)
