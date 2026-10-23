"""Thin HA adapter for unattended mowing eligibility and explicit commands."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from .debug import sanitize_diagnostic_text
from .dreame_lawn_mower_client import DreameLawnMowerConnectionError
from .dreame_lawn_mower_client.exceptions import MowingAdmissionRevokedError
from .dreame_lawn_mower_client.schedule_start import scheduled_start_block_reason
from .dreame_lawn_mower_client.scheduled_run_history import ScheduledRunHistory
from .mowing_permission import permission_block


async def async_start_scheduled_mowing(
    entity: Any,
    *,
    task_type: str = "all",
    minimum_battery: int = 30,
    map_index: int | None = None,
    zone_ids: list[int] | None = None,
    spot_ids: list[int] | None = None,
    contour_ids: list[list[int]] | None = None,
    permission_entity: str = "",
    permission_max_age: int = 1800,
    _expected_map_index: int | None = None,
    _dispatch_block: Callable[[], str | None] | None = None,
    _record_outcomes: bool = True,
    _before_dispatch: Callable[[], Awaitable[None]] | None = None,
    _previous_task_id: int | None = None,
) -> dict[str, Any]:
    """Skip an ineligible block immediately; never queue or resume its task."""
    coordinator = entity.coordinator
    scope_map = map_index if task_type != "all" else None
    targets = {"zone": zone_ids, "spot": spot_ids, "edge": contour_ids}

    def publish(
        status: str, reason: str, *, detail: str | None = None
    ) -> dict[str, Any]:
        return _publish(
            entity,
            task_type,
            status,
            reason,
            map_index=scope_map,
            targets=targets.get(task_type),
            detail=detail,
            record_outcome=_record_outcomes,
        )

    lock = getattr(coordinator, "_scheduled_mowing_lock", None)
    if lock is None:
        lock = coordinator._scheduled_mowing_lock = asyncio.Lock()
    if lock.locked():
        return publish("skipped", "scheduled_start_in_progress")
    async with lock:

        def external_block() -> dict[str, Any] | None:
            if _dispatch_block and (reason := _dispatch_block()):
                return publish("skipped", reason)
            if blocked := permission_block(
                entity.hass,
                permission_entity,
                permission_max_age,
            ):
                return publish("skipped", blocked[0], detail=blocked[1])
            return None

        if blocked_result := external_block():
            return blocked_result
        admission = None
        if permission_entity or _dispatch_block:

            def permitted() -> bool:
                return (
                    _dispatch_block is None or _dispatch_block() is None
                ) and permission_block(
                    entity.hass, permission_entity, permission_max_age
                ) is None

            admission = permitted
        target_admission = {"_admission": admission} if admission else {}
        if task_type != "all" and map_index is None:
            return publish("skipped", "target_map_required")
        if task_type != "all" and not targets.get(task_type):
            return publish("skipped", "task_targets_required")
        client = coordinator.client
        try:
            native = await client.async_get_schedule_start_evidence()
            if task_type == "all":
                scope_map = native.get("current_map_index")
            weather = await client.async_get_weather_protection(include_raw=False)
            snapshot = await client.async_refresh_authoritative_snapshot()
        except DreameLawnMowerConnectionError as err:
            return publish(
                "skipped",
                "eligibility_refresh_failed",
                detail=sanitize_diagnostic_text(err),
            )
        if _previous_task_id is not None and (
            getattr(snapshot, "mission_task_id", None) != _previous_task_id
        ):
            return publish("skipped", "pending_task_changed_or_unknown")
        reason = scheduled_start_block_reason(
            snapshot,
            weather,
            native,
            minimum_battery=minimum_battery,
            map_index=(map_index if task_type != "all" else _expected_map_index),
        )
        if reason:
            return publish("skipped", reason)
        if _before_dispatch:
            await _before_dispatch()
        if blocked_result := external_block():
            return blocked_result
        try:
            if task_type == "all":
                fields: dict[str, Any] = {}
                if _expected_map_index is not None:
                    fields["expected_map_index"] = _expected_map_index
                if admission:
                    fields["admission"] = admission
                await client.async_start_fresh_mowing(**fields)
            elif task_type == "zone":
                await entity.async_start_zone_mowing(
                    zone_ids,
                    require_inactive_task=True,
                    expected_map_index=map_index,
                    **target_admission,
                )
            elif task_type == "spot":
                await entity.async_start_spot_mowing(
                    spot_ids,
                    require_inactive_task=True,
                    expected_map_index=map_index,
                    **target_admission,
                )
            elif task_type == "edge":
                await entity.async_start_edge_mowing(
                    contour_ids,
                    require_inactive_task=True,
                    expected_map_index=map_index,
                    **target_admission,
                )
            else:
                raise ValueError("Unknown scheduled mowing task type.")
        except (DreameLawnMowerConnectionError, HomeAssistantError) as err:
            admission_error = (
                err if isinstance(err, MowingAdmissionRevokedError) else err.__cause__
            )
            if (
                isinstance(admission_error, MowingAdmissionRevokedError)
                and not admission_error.write_attempted
            ):
                if blocked_result := external_block():
                    return blocked_result
                return publish("skipped", "mowing_permission_revoked")
            return publish(
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
                return publish("submitted", "start_request_submitted")
        return publish("started", "mowing_start_confirmed")


def _publish(
    entity: Any,
    task_type: str,
    status: str,
    reason: str,
    *,
    map_index: int | None = None,
    targets: list[Any] | None = None,
    detail: str | None = None,
    record_outcome: bool = True,
    event_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose one outcome in response data, entity attributes, and an HA event."""
    if not record_outcome:
        return ScheduledRunHistory().record(
            task_type,
            status,
            reason,
            map_index=map_index,
            targets=targets,
            detail=detail,
        )
    history = getattr(entity.coordinator, "scheduled_run_history", None)
    if history is None:
        history = entity.coordinator.scheduled_run_history = ScheduledRunHistory()
    result = history.record(
        task_type,
        status,
        reason,
        map_index=map_index,
        targets=targets,
        detail=sanitize_diagnostic_text(detail) if detail else None,
    )
    entity.coordinator.last_scheduled_run = result
    if (
        checkpoint := getattr(entity.coordinator, "observation_checkpoint", None)
    ) is not None:
        checkpoint.async_schedule_save(urgent=True)
    entity.coordinator.async_update_listeners()
    entity.hass.bus.async_fire(
        "dreame_lawn_mower_scheduled_run",
        {"entity_id": entity.entity_id, **result, **(event_data or {})},
    )
    return result
