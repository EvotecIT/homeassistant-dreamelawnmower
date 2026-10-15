"""Coalesce map-switch signals through the existing metadata refresh owner."""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api import DreameLawnMowerSnapshot
    from .coordinator import DreameLawnMowerCoordinator
    from .coordinator_refresh import DreameLawnMowerRefreshMixin

MAP_SWITCH_SETTLE_SECONDS = 2.0


def observe_map_switch(
    coordinator: DreameLawnMowerCoordinator, snapshot: DreameLawnMowerSnapshot
) -> None:
    """Retire old map evidence once per announcement, before publishing poses."""
    observed = getattr(snapshot, "map_switch_event_at", None)
    previous = getattr(coordinator, "_last_map_switch_event_at", None)
    if observed is None or (previous is not None and observed <= previous):
        return
    coordinator._last_map_switch_event_at = observed
    coordinator._map_switch_settle_until = monotonic() + MAP_SWITCH_SETTLE_SECONDS
    coordinator._invalidate_runtime_map_identity()
    coordinator._invalidate_inflight_schedule_refreshes()
    coordinator.app_maps_refreshed_at = None
    coordinator.app_maps_refresh_succeeded = False
    coordinator.vector_map_details_refreshed_at = None
    coordinator.schedules_refreshed_at = None
    coordinator.batch_device_data_refreshed_at = None
    coordinator._schedule_metadata_refresh(refresh_map_and_runtime=True)


async def async_wait_for_map_switch(coordinator: DreameLawnMowerRefreshMixin) -> None:
    """Wait for the latest marker; metadata cancellation also cancels this wait."""
    while True:
        remaining = getattr(coordinator, "_map_switch_settle_until", 0) - monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(remaining)
