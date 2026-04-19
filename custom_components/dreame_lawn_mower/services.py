"""Home Assistant services for Dreame lawn mower."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import DreameLawnMowerCoordinator
from .dreame_client.client import (
    REMOTE_CONTROL_MAX_ROTATION,
    REMOTE_CONTROL_MAX_VELOCITY,
)
from .manual_control import remote_control_block_reason

ATTR_ENTRY_ID = "entry_id"
ATTR_CONFIRM_SCHEDULE_WRITE = "confirm_schedule_write"
ATTR_ENABLED = "enabled"
ATTR_EXECUTE = "execute"
ATTR_MAP_INDEX = "map_index"
ATTR_PLAN_ID = "plan_id"
ATTR_PROMPT = "prompt"
ATTR_ROTATION = "rotation"
ATTR_VELOCITY = "velocity"

SERVICE_REMOTE_CONTROL_STEP = "remote_control_step"
SERVICE_REMOTE_CONTROL_STOP = "remote_control_stop"
SERVICE_SET_SCHEDULE_PLAN_ENABLED = "set_schedule_plan_enabled"

_SERVICES_REGISTERED = "__services_registered"


def _bounded_int(*, name: str, limit: int) -> Any:
    """Return a voluptuous validator for a bounded integer control value."""

    def validator(value: Any) -> int:
        if isinstance(value, bool):
            raise vol.Invalid(f"{name} must be an integer")
        try:
            converted = int(value)
        except (TypeError, ValueError) as err:
            raise vol.Invalid(f"{name} must be an integer") from err
        if abs(converted) > limit:
            raise vol.Invalid(f"{name} must be between {-limit} and {limit}")
        return converted

    return validator


def _int_range(*, name: str, minimum: int | None = None) -> Any:
    """Return a voluptuous validator for an integer with an optional minimum."""

    def validator(value: Any) -> int:
        if isinstance(value, bool):
            raise vol.Invalid(f"{name} must be an integer")
        try:
            converted = int(value)
        except (TypeError, ValueError) as err:
            raise vol.Invalid(f"{name} must be an integer") from err
        if minimum is not None and converted < minimum:
            raise vol.Invalid(f"{name} must be at least {minimum}")
        return converted

    return validator


REMOTE_CONTROL_STEP_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_ROTATION): _bounded_int(
            name=ATTR_ROTATION,
            limit=REMOTE_CONTROL_MAX_ROTATION,
        ),
        vol.Required(ATTR_VELOCITY): _bounded_int(
            name=ATTR_VELOCITY,
            limit=REMOTE_CONTROL_MAX_VELOCITY,
        ),
        vol.Optional(ATTR_PROMPT, default=False): cv.boolean,
    }
)

REMOTE_CONTROL_STOP_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
    }
)

SET_SCHEDULE_PLAN_ENABLED_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_MAP_INDEX): _int_range(name=ATTR_MAP_INDEX, minimum=-1),
        vol.Required(ATTR_PLAN_ID): _int_range(name=ATTR_PLAN_ID, minimum=0),
        vol.Required(ATTR_ENABLED): cv.boolean,
        vol.Optional(ATTR_EXECUTE, default=False): cv.boolean,
        vol.Optional(ATTR_CONFIRM_SCHEDULE_WRITE, default=False): cv.boolean,
    }
)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register domain services once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_SERVICES_REGISTERED):
        return

    async def async_handle_remote_control_step(call: ServiceCall) -> None:
        coordinator = _coordinator_from_call(hass, call)
        _guard_remote_control_step(coordinator)
        await coordinator.client.async_remote_control_move_step(
            rotation=call.data[ATTR_ROTATION],
            velocity=call.data[ATTR_VELOCITY],
            prompt=call.data[ATTR_PROMPT],
        )
        await coordinator.async_request_refresh()

    async def async_handle_remote_control_stop(call: ServiceCall) -> None:
        coordinator = _coordinator_from_call(hass, call)
        await coordinator.client.async_remote_control_stop()
        await coordinator.async_request_refresh()

    async def async_handle_set_schedule_plan_enabled(call: ServiceCall) -> None:
        coordinator = _coordinator_from_call(hass, call)
        _guard_schedule_write_request(call)
        result = await coordinator.client.async_set_app_schedule_plan_enabled(
            map_index=call.data[ATTR_MAP_INDEX],
            plan_id=call.data[ATTR_PLAN_ID],
            enabled=call.data[ATTR_ENABLED],
            execute=call.data[ATTR_EXECUTE],
            confirm_write=call.data[ATTR_CONFIRM_SCHEDULE_WRITE],
        )
        coordinator.last_schedule_write_result = result
        coordinator.async_update_listeners()
        if call.data[ATTR_EXECUTE]:
            await coordinator.async_request_refresh()
        _notify_schedule_plan_enabled(coordinator, result)

    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOTE_CONTROL_STEP,
        async_handle_remote_control_step,
        schema=REMOTE_CONTROL_STEP_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOTE_CONTROL_STOP,
        async_handle_remote_control_stop,
        schema=REMOTE_CONTROL_STOP_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCHEDULE_PLAN_ENABLED,
        async_handle_set_schedule_plan_enabled,
        schema=SET_SCHEDULE_PLAN_ENABLED_SCHEMA,
    )
    domain_data[_SERVICES_REGISTERED] = True


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unregister domain services when the last entry unloads."""
    domain_data = hass.data.get(DOMAIN, {})
    if not domain_data.get(_SERVICES_REGISTERED):
        return

    hass.services.async_remove(DOMAIN, SERVICE_REMOTE_CONTROL_STEP)
    hass.services.async_remove(DOMAIN, SERVICE_REMOTE_CONTROL_STOP)
    hass.services.async_remove(DOMAIN, SERVICE_SET_SCHEDULE_PLAN_ENABLED)
    domain_data.pop(_SERVICES_REGISTERED, None)


def _coordinator_values(hass: HomeAssistant) -> Iterable[DreameLawnMowerCoordinator]:
    """Yield configured mower coordinators from domain data."""
    for value in hass.data.get(DOMAIN, {}).values():
        if isinstance(value, DreameLawnMowerCoordinator):
            yield value


def _coordinator_from_call(
    hass: HomeAssistant,
    call: ServiceCall,
) -> DreameLawnMowerCoordinator:
    """Return the coordinator targeted by a service call."""
    entry_id = call.data.get(ATTR_ENTRY_ID)
    if entry_id:
        coordinator = hass.data.get(DOMAIN, {}).get(entry_id)
        if isinstance(coordinator, DreameLawnMowerCoordinator):
            return coordinator
        raise HomeAssistantError(f"No Dreame lawn mower entry found for {entry_id}.")

    coordinators = list(_coordinator_values(hass))
    if len(coordinators) == 1:
        return coordinators[0]
    if not coordinators:
        raise HomeAssistantError("No Dreame lawn mower entries are loaded.")
    raise HomeAssistantError(
        "Multiple Dreame lawn mower entries are loaded; pass entry_id."
    )


def _guard_remote_control_step(coordinator: DreameLawnMowerCoordinator) -> None:
    """Block movement when the current snapshot looks unsafe for manual driving."""
    snapshot = coordinator.data
    if snapshot is None:
        raise HomeAssistantError("Mower state is not available yet.")

    if reason := remote_control_block_reason(snapshot):
        raise HomeAssistantError(reason)


def _guard_schedule_write_request(call: ServiceCall) -> None:
    """Block schedule writes unless the HA service confirmation gate is set."""
    if call.data[ATTR_EXECUTE] and not call.data[ATTR_CONFIRM_SCHEDULE_WRITE]:
        raise HomeAssistantError(
            "Schedule writes require confirm_schedule_write when execute is true."
        )


def _notify_schedule_plan_enabled(
    coordinator: DreameLawnMowerCoordinator,
    result: dict[str, Any],
) -> None:
    """Create a user-visible notification for a schedule write or dry-run."""
    title, message = _schedule_write_notification(result)
    persistent_notification.async_create(
        coordinator.hass,
        message,
        title=title,
        notification_id=(
            f"{DOMAIN}_{coordinator.entry.entry_id}_schedule_plan_enabled"
        ),
    )


def _schedule_write_notification(result: dict[str, Any]) -> tuple[str, str]:
    """Return title and message for a schedule write result notification."""
    request = json.dumps(result.get("request"), sort_keys=True)
    schedule = result.get("schedule")
    target_plan = result.get("target_plan")
    schedule_label = (
        schedule.get("label")
        if isinstance(schedule, dict)
        else f"map {result.get('map_index')}"
    )
    plan_name = (
        target_plan.get("name")
        if isinstance(target_plan, dict) and target_plan.get("name")
        else f"plan {result.get('plan_id')}"
    )
    change_text = "will change" if result.get("changed") else "already matched"
    if result.get("executed"):
        title = "Dreame Lawn Mower Schedule Updated"
        action = "Sent"
        change_text = "changed" if result.get("changed") else "was already matched"
    else:
        title = "Dreame Lawn Mower Schedule Dry Run"
        action = "Built dry-run"

    message = (
        f"{action} schedule enable request for {schedule_label} {plan_name}: "
        f"previous={result.get('previous_enabled')}, "
        f"target={result.get('enabled')} ({change_text}), "
        f"version={result.get('version')}. Request: `{request}`"
    )
    if result.get("executed") and result.get("response_data") is not None:
        response = json.dumps(result.get("response_data"), sort_keys=True)
        message = f"{message} Response: `{response}`"
    return title, message
