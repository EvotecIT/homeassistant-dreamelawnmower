"""Native schedule reads serialized with legacy read/modify/write operations."""

from __future__ import annotations

import asyncio
from collections.abc import Generator, Sequence
from typing import TYPE_CHECKING, Any

from .app_read_transport import AppReadRequest
from .client_app_reads import async_run_app_read
from .exceptions import DreameLawnMowerConnectionError
from .schedule_read_plan import read_schedules, read_start_evidence

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession


async def async_read_schedules(
    client: DreameLawnMowerClient,
    *,
    include_raw: bool,
    map_indices: Sequence[int] | None,
    chunk_size: int,
    include_current_task: bool,
) -> dict[str, Any]:
    """Run the canonical schedule plan using owned native read-only app RPCs."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero.")

    return await _run_serialized_plan(client, read_schedules(
        client,
        include_raw=include_raw,
        map_indices=map_indices,
        chunk_size=chunk_size,
        include_current_task=include_current_task,
    ))


async def async_read_start_evidence(
    client: DreameLawnMowerClient,
) -> dict[str, Any]:
    """Keep authoritative inventory and schedule reads in one transaction."""
    return await _run_serialized_plan(client, read_start_evidence(client))


async def _run_serialized_plan(
    client: DreameLawnMowerClient,
    plan: Generator[AppReadRequest, Any, dict[str, Any]],
) -> dict[str, Any]:
    async def read(_cloud: DreameCloudSession) -> dict[str, Any]:
        gate = client._schedule_async_gate
        lock = client._schedule_operation_lock
        gate_acquired = False
        lock_acquired = False
        try:
            try:
                async with asyncio.timeout(15):
                    await gate.acquire()
                    gate_acquired = True
                    # Both acquire and release the RLock on this loop thread.
                    # The async gate excludes other tasks on the same thread.
                    while not lock.acquire(blocking=False):
                        await asyncio.sleep(0.01)
                    lock_acquired = True
            except TimeoutError as error:
                raise DreameLawnMowerConnectionError(
                    "Schedule read timed out waiting for another operation."
                ) from error
            return await async_run_app_read(client, plan)
        finally:
            if lock_acquired:
                lock.release()
            if gate_acquired:
                gate.release()

    return await client._async_cloud_read(read)
