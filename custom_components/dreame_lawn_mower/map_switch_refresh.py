"""Coalesce map-switch signals through the existing metadata refresh owner."""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import TYPE_CHECKING

from .control_options import active_map_index
from .schedule_cache import ScheduleActionReadBackoff

if TYPE_CHECKING:
    from .api import DreameLawnMowerSnapshot
    from .coordinator import DreameLawnMowerCoordinator
    from .coordinator_refresh import DreameLawnMowerRefreshMixin

MAP_SWITCH_SETTLE_SECONDS = 2.0
MAP_SWITCH_RETRY_DELAYS = (0.75, 1.5, 3.0)
_LOGGER = logging.getLogger(__name__)


def observe_map_switch(
    coordinator: DreameLawnMowerCoordinator, snapshot: DreameLawnMowerSnapshot
) -> None:
    """Retire old map evidence once per announcement, before publishing poses."""
    observed = getattr(snapshot, "map_switch_event_at", None)
    previous = getattr(coordinator, "_last_map_switch_event_at", None)
    if observed is None or (previous is not None and observed <= previous):
        return
    coordinator._map_switch_overlapping = (
        getattr(coordinator, "_map_switch_pending_event", None) is not None
        or getattr(coordinator, "_map_switch_inventory_pending_event", None) is not None
    )
    if coordinator._runtime_map_identity_is_fresh() and getattr(
        coordinator, "_runtime_active_map_index", None
    ) is not None:
        coordinator._map_switch_previous_index = getattr(
            coordinator, "_runtime_active_map_index", None
        )
    else:
        coordinator._map_switch_previous_index = active_map_index(
            getattr(coordinator, "app_maps", None)
        )
    coordinator._last_map_switch_event_at = observed
    coordinator._map_switch_pending_event = observed
    coordinator._map_switch_inventory_pending_event = observed
    coordinator._map_switch_settle_until = monotonic() + MAP_SWITCH_SETTLE_SECONDS
    preview = getattr(coordinator, "map_restart_preview", None)
    if preview is not None:
        preview.invalidate(observed)
    coordinator._invalidate_runtime_map_identity()
    coordinator.client.invalidate_runtime_map_geometry()
    coordinator._invalidate_inflight_schedule_refreshes()
    coordinator._schedule_action_read_backoff = ScheduleActionReadBackoff()
    coordinator.app_maps_refreshed_at = None
    coordinator.app_maps_refresh_succeeded = False
    coordinator.app_map_objects_refreshed_at = None
    coordinator.vector_map_details_refreshed_at = None
    coordinator.schedules_refreshed_at = None
    coordinator.batch_device_data_refreshed_at = None
    coordinator._schedule_metadata_refresh(refresh_map_and_runtime=True)


async def async_flush_restart_preview(coordinator: object) -> None:
    """Drain optional preview retirement through existing refresh/stop owners."""
    preview = getattr(coordinator, "map_restart_preview", None)
    if preview is not None:
        try:
            await preview.async_flush_invalidated()
        except Exception:  # Optional storage must not stop mower metadata recovery.
            _LOGGER.debug("Unable to retire optional map restart preview")


async def async_wait_for_map_switch(coordinator: DreameLawnMowerRefreshMixin) -> None:
    """Wait for the latest marker; metadata cancellation also cancels this wait."""
    while True:
        remaining = getattr(coordinator, "_map_switch_settle_until", 0) - monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(remaining)


async def async_read_settled_map_index(
    coordinator: DreameLawnMowerRefreshMixin,
) -> int | None:
    """Give an announced switch bounded readback time before renewing its lease."""
    event = getattr(coordinator, "_map_switch_pending_event", None)
    previous = getattr(coordinator, "_map_switch_previous_index", None)
    index = await coordinator.client.async_get_current_app_map_index()
    if event is None:
        return index
    for delay in MAP_SWITCH_RETRY_DELAYS:
        if event != getattr(coordinator, "_map_switch_pending_event", None):
            return index
        if (
            not getattr(coordinator, "_map_switch_overlapping", False)
            and index is not None and (previous is None or index != previous)
        ):
            break
        await asyncio.sleep(delay)
        if event != getattr(coordinator, "_map_switch_pending_event", None):
            return index
        index = await coordinator.client.async_get_current_app_map_index()
    if (
        event == getattr(coordinator, "_map_switch_pending_event", None)
        and index is not None
    ):
        # A marker can also describe an unchanged map. Stop after the same
        # bounded backoff used for a commanded switch rather than retry forever.
        coordinator._map_switch_pending_event = None
    return index


async def async_prepare_map_switch_metadata(
    coordinator: DreameLawnMowerCoordinator,
    *, require_inventory: bool = True,
) -> bool:
    """Confirm pending map identity before any map-scoped metadata can be fresh."""
    await async_wait_for_map_switch(coordinator)
    if getattr(coordinator, "_map_switch_pending_event", None) is not None:
        refreshed, _index, _generation = (
            await coordinator._async_refresh_runtime_map_index(force=True)
        )
        if not refreshed:
            return False
    return not require_inventory or getattr(
        coordinator, "_map_switch_inventory_pending_event", None
    ) is None
