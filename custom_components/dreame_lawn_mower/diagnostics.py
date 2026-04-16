"""Diagnostics for Dreame lawn mower."""

from __future__ import annotations

from dataclasses import asdict

from homeassistant.components.diagnostics import async_redact_data

from .const import CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME, DOMAIN

TO_REDACT = {CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME}


async def async_get_config_entry_diagnostics(hass, entry):
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    snapshot = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "snapshot": None
        if snapshot is None
        else async_redact_data(asdict(snapshot), TO_REDACT),
    }
