"""Native batch metadata reads using the shared cloud and payload owners."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from .batch_device_data import decode_batch_schedule_payload
from .client_app_reads import async_read_app_action
from .client_map_helpers import _normalize_app_map_entries
from .client_settings_helpers import _batch_schedule_keys
from .client_shared_helpers import _positive_int
from .exceptions import DreameLawnMowerConnectionError

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_read_batch_schedules(
    client: DreameLawnMowerClient,
    *,
    include_raw: bool,
    map_index_hint: int | None,
    discover_map_index: bool,
    timeout: float,
) -> dict[str, Any]:
    """Read the optional map hint and schedule under one operation deadline."""
    deadline = time.monotonic() + timeout

    async def read(cloud: DreameCloudSession) -> dict[str, Any]:
        hint = map_index_hint
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                if hint is None and discover_map_index:
                    try:
                        response = await async_read_app_action(
                            client, {"m": "g", "t": "MAPL"}, deadline=deadline,
                        )
                        for entry in _normalize_app_map_entries(response):
                            if entry.get("current"):
                                hint = _positive_int(entry.get("idx"))
                                break
                    except Exception:  # noqa: BLE001 - optional map hint
                        # Cancellation is a BaseException and must escape.
                        # The batch request retains the original deadline.
                        hint = None
                data = await cloud.async_get_batch_device_datas(
                    client._descriptor.did, _batch_schedule_keys(), deadline=deadline,
                )
                return decode_batch_schedule_payload(
                    data, include_raw=include_raw, map_index_hint=hint,
                )
        except TimeoutError as err:
            raise DreameLawnMowerConnectionError(
                "Batch schedule read timed out"
            ) from err

    return await client._async_cloud_read(read)
