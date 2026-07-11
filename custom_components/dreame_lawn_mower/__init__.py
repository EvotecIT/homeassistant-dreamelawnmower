"""Dreame lawn mower integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_VIDEO_TRANSPORT,
    DOMAIN,
    PLATFORMS,
    VIDEO_TRANSPORT_LAN,
)
from .coordinator import DreameLawnMowerCoordinator
from .services import async_setup_services, async_unload_services
from .video_lan_cache import DreameLawnMowerVideoLanCache

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dreame lawn mower from a config entry."""
    coordinator = DreameLawnMowerCoordinator(hass, entry)
    lan_cache = DreameLawnMowerVideoLanCache(
        hass,
        entry_id=entry.entry_id,
        did=coordinator.client.descriptor.did,
    )
    try:
        await lan_cache.async_load()
    except Exception as err:  # noqa: BLE001 - normal cloud setup remains available.
        _LOGGER.warning("Failed to load Dreame LAN video cache during setup: %s", err)
    coordinator.video_lan_cache = lan_cache
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        if not (
            entry.options.get(CONF_VIDEO_TRANSPORT) == VIDEO_TRANSPORT_LAN
            and lan_cache.inputs is not None
            and lan_cache.endpoint is not None
        ):
            raise
        _LOGGER.warning(
            "Dreame cloud is unavailable; starting a previously proven cached "
            "LAN-only video mode"
        )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await async_setup_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Dreame lawn mower entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
        if not any(
            isinstance(value, DreameLawnMowerCoordinator)
            for value in hass.data[DOMAIN].values()
        ):
            await async_unload_services(hass)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
