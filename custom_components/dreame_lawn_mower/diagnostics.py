"""Diagnostics for Dreame lawn mower."""

from __future__ import annotations

from .const import DOMAIN
from .debug import build_debug_payload


async def async_get_config_entry_diagnostics(hass, entry):
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_request_refresh()
    return build_debug_payload(
        entry_data=entry.data,
        snapshot=coordinator.data,
        device=coordinator.client._device,
    )
