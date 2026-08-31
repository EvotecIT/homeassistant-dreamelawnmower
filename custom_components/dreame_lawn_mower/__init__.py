"""Dreame lawn mower integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_DID,
    CONFIG_ENTRY_MINOR_VERSION,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import DreameLawnMowerCoordinator
from .notifications import DreameLawnMowerNotificationManager
from .performance import format_performance_sample
from .point_cloud_api import (
    POINT_CLOUD_API_DATA_KEY,
    DreameLawnMowerPointCloudAPI,
    async_setup_point_cloud_api,
)
from .services import async_setup_services, async_unload_services
from .video_lan_cache import DreameLawnMowerVideoLanCache
from .video_provisioning_cache import DreameLawnMowerVideoProvisioningCache

_LOGGER = logging.getLogger(__name__)
SLOW_SETUP_SECONDS = 15.0


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Enable the primary map camera without overriding user choices."""
    if entry.version > CONFIG_ENTRY_VERSION:
        return False

    if (
        entry.version == CONFIG_ENTRY_VERSION
        and entry.minor_version < CONFIG_ENTRY_MINOR_VERSION
    ):
        unique_id = entry.data.get(CONF_DID) or entry.unique_id
        if unique_id and not entry.pref_disable_new_entities:
            registry = er.async_get(hass)
            entity_id = registry.async_get_entity_id(
                Platform.CAMERA,
                DOMAIN,
                f"{unique_id}_map",
            )
            if entity_id is not None:
                registry_entry = registry.async_get(entity_id)
                if (
                    registry_entry is not None
                    and registry_entry.disabled_by
                    is er.RegistryEntryDisabler.INTEGRATION
                ):
                    registry.async_update_entity(entity_id, disabled_by=None)

        hass.config_entries.async_update_entry(
            entry,
            version=CONFIG_ENTRY_VERSION,
            minor_version=CONFIG_ENTRY_MINOR_VERSION,
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Dreame lawn mower from a config entry."""
    coordinator = DreameLawnMowerCoordinator(hass, entry)

    async def _async_shutdown_on_stop(_: Event) -> None:
        """Release integration resources before Home Assistant waits for tasks."""
        await coordinator.async_shutdown_for_home_assistant_stop()

    remove_stop_listener = hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STOP,
        _async_shutdown_on_stop,
    )
    setup_cycle = coordinator.performance.start("setup")
    setup_outcome = "completed"
    lan_cache = DreameLawnMowerVideoLanCache(
        hass,
        entry_id=entry.entry_id,
        did=coordinator.client.descriptor.did,
    )
    provisioning_cache = DreameLawnMowerVideoProvisioningCache(
        hass,
        entry_id=entry.entry_id,
        did=coordinator.client.descriptor.did,
    )
    try:
        try:
            await setup_cycle.measure("lan_video_cache", lan_cache.async_load)
        except Exception as err:  # noqa: BLE001 - cloud setup remains available.
            _LOGGER.warning(
                "Failed to load Dreame LAN video cache during setup: %s",
                err,
            )
        coordinator.video_lan_cache = lan_cache

        try:
            await setup_cycle.measure(
                "video_provisioning_cache",
                provisioning_cache.async_load,
            )
        except Exception as err:  # noqa: BLE001 - cloud setup remains available.
            _LOGGER.warning(
                "Failed to load Dreame video provisioning cache: %s",
                err,
            )
        coordinator.video_provisioning_cache = provisioning_cache

        platforms = tuple(PLATFORMS)
        await setup_cycle.measure(
            "first_refresh",
            coordinator.async_config_entry_first_refresh,
        )

        hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
        async_setup_point_cloud_api(hass)
        coordinator.loaded_platforms = platforms
        await setup_cycle.measure("services", lambda: async_setup_services(hass))
        await setup_cycle.measure(
            "platforms",
            lambda: hass.config_entries.async_forward_entry_setups(entry, platforms),
        )

        notification_manager = DreameLawnMowerNotificationManager(coordinator)
        await notification_manager.async_start()

        entry.async_on_unload(remove_stop_listener)
        entry.async_on_unload(notification_manager.stop)
        entry.async_on_unload(entry.add_update_listener(_async_update_listener))
        return True
    except asyncio.CancelledError:
        setup_outcome = "cancelled"
        remove_stop_listener()
        await _async_cleanup_failed_setup(hass, entry, coordinator)
        raise
    except Exception as err:  # noqa: BLE001 - retain setup failure behavior
        setup_outcome = type(err).__name__
        remove_stop_listener()
        await _async_cleanup_failed_setup(hass, entry, coordinator)
        raise
    finally:
        sample = setup_cycle.finish(outcome=setup_outcome)
        total, phases = format_performance_sample(sample)
        message = (
            "Dreame mower performance: operation=setup outcome=%s total=%.3fs "
            "phases=[%s] metadata_background=%s"
        )
        args = (
            sample.outcome,
            total,
            phases,
            getattr(coordinator, "_metadata_refresh_task", None) is not None,
        )
        if total >= SLOW_SETUP_SECONDS:
            _LOGGER.warning(message, *args)
        else:
            _LOGGER.info(message, *args)


async def _async_cleanup_failed_setup(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: DreameLawnMowerCoordinator,
) -> None:
    """Drain coordinator resources registered before a failed setup."""
    domain_data = hass.data.get(DOMAIN)
    if (
        isinstance(domain_data, dict)
        and domain_data.get(entry.entry_id) is coordinator
    ):
        domain_data.pop(entry.entry_id, None)
    try:
        await coordinator.async_shutdown()
    except Exception as err:  # noqa: BLE001 - preserve the original setup error
        _LOGGER.warning(
            "Failed to fully close Dreame mower after setup error: %s",
            err,
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Dreame lawn mower entry."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    platforms = getattr(coordinator, "loaded_platforms", tuple(PLATFORMS))
    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        point_cloud_api = hass.data[DOMAIN].get(POINT_CLOUD_API_DATA_KEY)
        if isinstance(point_cloud_api, DreameLawnMowerPointCloudAPI):
            point_cloud_api.purge_entry(entry.entry_id)
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
