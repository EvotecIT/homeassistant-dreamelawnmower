"""Thin HA adapter for unattended mowing eligibility and explicit commands."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .debug import sanitize_diagnostic_text
from .dreame_lawn_mower_client import DreameLawnMowerConnectionError
from .dreame_lawn_mower_client.schedule_start import scheduled_start_block_reason


async def async_start_scheduled_mowing(
    entity: Any,
    *,
    task_type: str = "all",
    minimum_battery: int = 30,
    map_index: int | None = None,
    zone_ids: list[int] | None = None,
    spot_ids: list[int] | None = None,
    contour_ids: list[list[int]] | None = None,
) -> dict[str, Any]:
    """Skip an ineligible block immediately; never queue or resume its task."""
    coordinator = entity.coordinator
    lock = getattr(coordinator, "_scheduled_mowing_lock", None)
    if lock is None:
        lock = coordinator._scheduled_mowing_lock = asyncio.Lock()
    if lock.locked():
        return _publish(entity, task_type, "skipped", "scheduled_start_in_progress")
    async with lock:
        if task_type != "all" and map_index is None:
            return _publish(entity, task_type, "skipped", "target_map_required")
        targets = {"zone": zone_ids, "spot": spot_ids, "edge": contour_ids}
        if task_type != "all" and not targets.get(task_type):
            return _publish(entity, task_type, "skipped", "task_targets_required")
        client = coordinator.client
        try:
            native = await client.async_get_schedule_start_evidence()
            weather = await client.async_get_weather_protection(include_raw=False)
            snapshot = await client.async_refresh_authoritative_snapshot()
        except DreameLawnMowerConnectionError as err:
            return _publish(
                entity,
                task_type,
                "skipped",
                "eligibility_refresh_failed",
                detail=sanitize_diagnostic_text(err),
            )
        reason = scheduled_start_block_reason(
            snapshot,
            weather,
            native,
            minimum_battery=minimum_battery,
            map_index=map_index if task_type != "all" else None,
        )
        if reason:
            return _publish(entity, task_type, "skipped", reason)
        try:
            if task_type == "all":
                await client.async_start_fresh_mowing()
            elif task_type == "zone":
                await entity.async_start_zone_mowing(
                    zone_ids, require_inactive_task=True
                )
            elif task_type == "spot":
                await entity.async_start_spot_mowing(
                    spot_ids, require_inactive_task=True
                )
            elif task_type == "edge":
                await entity.async_start_edge_mowing(
                    contour_ids, require_inactive_task=True
                )
            else:
                raise ValueError("Unknown scheduled mowing task type.")
        except (DreameLawnMowerConnectionError, HomeAssistantError) as err:
            return _publish(
                entity,
                task_type,
                "failed",
                "command_not_confirmed",
                detail=sanitize_diagnostic_text(err),
            )
        if task_type == "all":
            # An acknowledgement alone is not proof that mowing started.
            try:
                observed = await client.async_refresh_authoritative_snapshot()
            except DreameLawnMowerConnectionError:
                observed = None
            if observed is None or observed.mowing_session_active is not True:
                return _publish(
                    entity, task_type, "submitted", "start_request_submitted"
                )
        return _publish(entity, task_type, "started", "mowing_start_confirmed")


def _publish(
    entity: Any,
    task_type: str,
    status: str,
    reason: str,
    *,
    detail: str | None = None,
) -> dict[str, Any]:
    """Expose one outcome in response data, entity attributes, and an HA event."""
    result = {"task_type": task_type, "status": status, "reason": reason}
    if detail:
        result["detail"] = detail
    entity.coordinator.last_scheduled_run = result
    entity.coordinator.async_update_listeners()
    entity.hass.bus.async_fire(
        "dreame_lawn_mower_scheduled_run",
        {"entity_id": entity.entity_id, **result},
    )
    return result
