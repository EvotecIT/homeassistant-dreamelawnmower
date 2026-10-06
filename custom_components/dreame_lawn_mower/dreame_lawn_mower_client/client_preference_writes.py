"""Native preference writes with shared planning and exact readback."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .client_app_reads import async_command_app_action, async_run_app_read
from .exceptions import DreameLawnMowerConnectionError
from .mowing_preferences_read_plan import read_mowing_preferences
from .preference_write_plan import (
    PreferenceCommand,
    PreferenceDelay,
    PreferenceWriteRequest,
    ReadPreferences,
    plan_preference_update,
)
from .preference_write_transport import capture_preference_result

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_update_preferences(
    client: DreameLawnMowerClient,
    map_index: int,
    area_id: int | None,
    changes: Mapping[str, Any],
    execute: bool,
    confirm_write: bool,
) -> dict[str, Any]:
    """Own preflight, ordered writes and bounded readback through shutdown."""
    deadline = time.monotonic() + 120

    async def run(_cloud: DreameCloudSession) -> dict[str, Any]:
        result: list[dict[str, Any]] = []
        driver = capture_preference_result(
            plan_preference_update(
                client.descriptor.model,
                client.descriptor.display_model,
                map_index,
                area_id,
                changes,
                execute,
                confirm_write,
            ),
            result,
        )

        async def dispatch(request: PreferenceWriteRequest) -> Any:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DreameLawnMowerConnectionError("Preference update timed out.")
            match request:
                case ReadPreferences():
                    return await async_run_app_read(
                        client,
                        read_mowing_preferences(
                            map_indices=[request.map_index],
                            deadline=deadline,
                        ),
                        deadline=deadline,
                    )
                case PreferenceCommand():
                    return await async_command_app_action(
                        client,
                        request.action,
                        deadline=min(deadline, time.monotonic() + 20),
                    )
                case PreferenceDelay():
                    try:
                        async with asyncio.timeout(remaining):
                            await asyncio.sleep(request.seconds)
                    except TimeoutError as error:
                        raise DreameLawnMowerConnectionError(
                            "Preference update timed out."
                        ) from error
                    return None

        try:
            request = next(driver)
            while True:
                try:
                    response = await dispatch(request)
                except Exception as error:
                    request = driver.throw(error)
                else:
                    request = driver.send(response)
        except StopIteration:
            return result[0]
        finally:
            driver.close()

    return await client._async_cloud_read(run)
