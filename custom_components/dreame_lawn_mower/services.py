"""Home Assistant services for Dreame lawn mower."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import voluptuous as vol
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
ATTR_PROMPT = "prompt"
ATTR_ROTATION = "rotation"
ATTR_VELOCITY = "velocity"

SERVICE_REMOTE_CONTROL_STEP = "remote_control_step"
SERVICE_REMOTE_CONTROL_STOP = "remote_control_stop"

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
    domain_data[_SERVICES_REGISTERED] = True


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unregister domain services when the last entry unloads."""
    domain_data = hass.data.get(DOMAIN, {})
    if not domain_data.get(_SERVICES_REGISTERED):
        return

    hass.services.async_remove(DOMAIN, SERVICE_REMOTE_CONTROL_STEP)
    hass.services.async_remove(DOMAIN, SERVICE_REMOTE_CONTROL_STOP)
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
