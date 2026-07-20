"""Diagnostics for Dreame lawn mower."""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import system_info
from homeassistant.loader import async_get_loaded_integration

from .const import DOMAIN
from .debug import build_debug_payload
from .reporting import (
    build_coordinator_diagnostics,
    build_entity_diagnostics,
    build_report_context,
)


async def async_get_config_entry_diagnostics(hass, entry):
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_request_refresh()
    integration = async_get_loaded_integration(hass, DOMAIN)
    registry = er.async_get(hass)
    registry_entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    recent_events = getattr(coordinator, "diagnostic_events", None)
    return build_debug_payload(
        entry_data=entry.data,
        entry_options=entry.options,
        snapshot=coordinator.data,
        device=coordinator.client._device,
        report_context=build_report_context(
            system_info=await system_info.async_get_system_info(hass),
            integration_version=integration.version,
            config_entry=entry,
        ),
        coordinator_diagnostics=build_coordinator_diagnostics(coordinator),
        entity_diagnostics=build_entity_diagnostics(
            registry_entries,
            hass.states.get,
        ),
        recent_events=recent_events.as_list() if recent_events is not None else [],
    )
