"""Coordinator for Dreame lawn mower updates."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    DreameLawnMowerDescriptor,
    DreameLawnMowerSnapshot,
)
from .const import (
    CONF_ACCOUNT_TYPE,
    CONF_COUNTRY,
    CONF_DID,
    CONF_HOST,
    CONF_MAC,
    CONF_MODEL,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
)
from .dreame_client.models import display_name_for_model

_LOGGER = logging.getLogger(__name__)


class DreameLawnMowerCoordinator(DataUpdateCoordinator[DreameLawnMowerSnapshot]):
    """Manage mower state updates for a single config entry."""

    def __init__(self, hass, entry: ConfigEntry) -> None:
        descriptor = DreameLawnMowerDescriptor(
            did=entry.data[CONF_DID],
            name=entry.data[CONF_NAME],
            model=entry.data[CONF_MODEL],
            display_model=display_name_for_model(entry.data[CONF_MODEL])
            or entry.data[CONF_MODEL],
            account_type=entry.data[CONF_ACCOUNT_TYPE],
            country=entry.data[CONF_COUNTRY],
            host=entry.data.get(CONF_HOST),
            mac=entry.data.get(CONF_MAC),
            token=entry.data.get(CONF_TOKEN),
        )
        self.client = DreameLawnMowerClient(
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
            country=entry.data[CONF_COUNTRY],
            account_type=entry.data[CONF_ACCOUNT_TYPE],
            descriptor=descriptor,
        )
        self.entry = entry
        self.last_preference_probe_result: dict[str, Any] | None = None
        self.last_schedule_probe_result: dict[str, Any] | None = None
        self.last_schedule_write_result: dict[str, Any] | None = None
        self.last_weather_probe_result: dict[str, Any] | None = None

        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(
                    CONF_SCAN_INTERVAL,
                    DEFAULT_SCAN_INTERVAL_SECONDS,
                )
            ),
        )

    async def _async_update_data(self) -> DreameLawnMowerSnapshot:
        """Fetch the latest mower snapshot."""
        try:
            return await self.client.async_refresh()
        except DreameLawnMowerConnectionError as err:
            raise UpdateFailed(str(err)) from err

    async def async_shutdown(self) -> None:
        """Disconnect client resources."""
        await self.client.async_close()
