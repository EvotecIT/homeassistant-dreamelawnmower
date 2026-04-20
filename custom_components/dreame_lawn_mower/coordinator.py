"""Coordinator for Dreame lawn mower updates."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
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
from .dreame_lawn_mower_client.models import display_name_for_model

_LOGGER = logging.getLogger(__name__)

BATCH_DEVICE_DATA_REFRESH_INTERVAL = timedelta(minutes=15)
APP_MAP_OBJECT_REFRESH_INTERVAL = timedelta(minutes=30)
WEATHER_PROTECTION_REFRESH_INTERVAL = timedelta(minutes=5)


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
        self.app_map_objects: dict[str, Any] | None = None
        self.app_map_objects_refreshed_at: datetime | None = None
        self.batch_device_data: dict[str, Any] | None = None
        self.batch_device_data_refreshed_at: datetime | None = None
        self.weather_protection: dict[str, Any] | None = None
        self.weather_protection_refreshed_at: datetime | None = None
        self.last_batch_device_data_probe_result: dict[str, Any] | None = None
        self.last_preference_probe_result: dict[str, Any] | None = None
        self.last_preference_write_result: dict[str, Any] | None = None
        self.last_schedule_probe_result: dict[str, Any] | None = None
        self.last_schedule_write_result: dict[str, Any] | None = None
        self.last_task_status_probe_result: dict[str, Any] | None = None
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
            snapshot = await self.client.async_refresh()
        except DreameLawnMowerConnectionError as err:
            raise UpdateFailed(str(err)) from err
        await self.async_refresh_batch_device_data(force=False)
        await self.async_refresh_app_map_objects(force=False)
        await self.async_refresh_weather_protection(force=False)
        return snapshot

    async def async_refresh_batch_device_data(
        self,
        *,
        force: bool = False,
        source: str = "batch_device_data_auto",
    ) -> dict[str, Any] | None:
        """Refresh cached batch device data without failing the main poll."""
        now = datetime.now(UTC)
        if (
            not force
            and self.batch_device_data is not None
            and self.batch_device_data_refreshed_at is not None
            and now - self.batch_device_data_refreshed_at
            < BATCH_DEVICE_DATA_REFRESH_INTERVAL
        ):
            return self.batch_device_data

        try:
            batch_schedule, batch_mowing_preferences, batch_ota_info = await (
                self._async_fetch_batch_device_data()
            )
        except Exception as err:  # noqa: BLE001 - best-effort extra metadata
            _LOGGER.debug("Failed to refresh batch device data: %s", err)
            return self.batch_device_data

        payload = {
            "captured_at": now.isoformat(),
            "source": source,
            "batch_schedule": batch_schedule,
            "batch_mowing_preferences": batch_mowing_preferences,
            "batch_ota_info": batch_ota_info,
        }
        self.batch_device_data = payload
        self.batch_device_data_refreshed_at = now
        return payload

    async def _async_fetch_batch_device_data(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Fetch batch schedule, settings, and OTA payloads in parallel."""
        return await asyncio.gather(
            self.client.async_get_batch_schedules(include_raw=False),
            self.client.async_get_batch_mowing_preferences(include_raw=False),
            self.client.async_get_batch_ota_info(include_raw=False),
        )

    async def async_refresh_app_map_objects(
        self,
        *,
        force: bool = False,
        source: str = "app_map_objects_auto",
    ) -> dict[str, Any] | None:
        """Refresh cached read-only 3D map object metadata."""
        now = datetime.now(UTC)
        if (
            not force
            and self.app_map_objects is not None
            and self.app_map_objects_refreshed_at is not None
            and now - self.app_map_objects_refreshed_at
            < APP_MAP_OBJECT_REFRESH_INTERVAL
        ):
            return self.app_map_objects

        try:
            app_map_objects = await self.client.async_get_app_map_objects(
                include_urls=False,
            )
        except Exception as err:  # noqa: BLE001 - best-effort extra metadata
            _LOGGER.debug("Failed to refresh app map objects: %s", err)
            return self.app_map_objects

        payload = {
            "captured_at": now.isoformat(),
            "source": source,
            "app_map_objects": app_map_objects,
        }
        self.app_map_objects = payload
        self.app_map_objects_refreshed_at = now
        return payload

    async def async_refresh_weather_protection(
        self,
        *,
        force: bool = False,
        source: str = "weather_protection_auto",
    ) -> dict[str, Any] | None:
        """Refresh cached read-only weather and rain-protection state."""
        now = datetime.now(UTC)
        if (
            not force
            and self.weather_protection is not None
            and self.weather_protection_refreshed_at is not None
            and now - self.weather_protection_refreshed_at
            < WEATHER_PROTECTION_REFRESH_INTERVAL
        ):
            return self.weather_protection

        try:
            weather_protection = await self.client.async_get_weather_protection(
                include_raw=False,
            )
        except Exception as err:  # noqa: BLE001 - best-effort extra metadata
            _LOGGER.debug("Failed to refresh weather protection: %s", err)
            return self.weather_protection

        payload = dict(weather_protection)
        payload.setdefault("captured_at", now.isoformat())
        payload["source"] = source
        self.weather_protection = payload
        self.weather_protection_refreshed_at = now
        return payload

    async def async_shutdown(self) -> None:
        """Disconnect client resources."""
        await self.client.async_close()
