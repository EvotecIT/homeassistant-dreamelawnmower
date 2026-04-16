"""Dreame lawn mower integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import DreameLawnMowerCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dreame lawn mower from a config entry."""
    coordinator = DreameLawnMowerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Dreame lawn mower entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)

