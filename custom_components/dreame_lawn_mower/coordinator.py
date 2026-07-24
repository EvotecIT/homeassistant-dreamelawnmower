"""Coordinator for Dreame lawn mower updates."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    DreameLawnMowerDescriptor,
    DreameLawnMowerFirmwareUpdateSupport,
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
from .control_options import active_map_index
from .debug import sanitize_diagnostic_text
from .diagnostic_events import (
    DreameLawnMowerDiagnosticEventStore,
    record_diagnostic_event,
)
from .dreame_lawn_mower_client.models import (
    DreameLawnMowerStatusBlob,
    display_name_for_model,
)
from .runtime_cache import DreameLawnMowerRuntimeTelemetryCache

_LOGGER = logging.getLogger(__name__)

BATCH_DEVICE_DATA_REFRESH_INTERVAL = timedelta(minutes=15)
APP_MAP_REFRESH_INTERVAL = timedelta(minutes=5)
APP_MAP_OBJECT_REFRESH_INTERVAL = timedelta(minutes=30)
VECTOR_MAP_REFRESH_INTERVAL = timedelta(minutes=5)
WEATHER_PROTECTION_REFRESH_INTERVAL = timedelta(minutes=5)
MAINTENANCE_REFRESH_INTERVAL = timedelta(minutes=5)
VOICE_SETTINGS_REFRESH_INTERVAL = timedelta(minutes=5)
SCHEDULE_REFRESH_INTERVAL = timedelta(minutes=5)
FIRMWARE_UPDATE_REFRESH_INTERVAL = timedelta(minutes=15)


def runtime_tracking_active(snapshot: DreameLawnMowerSnapshot) -> bool:
    """Prefer explicit heartbeat session state over legacy activity state."""
    session_active = getattr(snapshot, "mowing_session_active", None)
    if session_active is not None:
        return bool(session_active)
    return getattr(snapshot, "activity", None) in {"mowing", "paused", "returning"}


_runtime_tracking_active = runtime_tracking_active


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
        self.app_maps: dict[str, Any] | None = None
        self.app_maps_refreshed_at: datetime | None = None
        self.app_map_objects_refreshed_at: datetime | None = None
        self.batch_device_data: dict[str, Any] | None = None
        self.batch_device_data_refreshed_at: datetime | None = None
        self.firmware_update_support: DreameLawnMowerFirmwareUpdateSupport | None = None
        self.firmware_update_support_refreshed_at: datetime | None = None
        self.vector_map_details: dict[str, Any] | None = None
        self.vector_map_details_refreshed_at: datetime | None = None
        self.weather_protection: dict[str, Any] | None = None
        self.weather_protection_refreshed_at: datetime | None = None
        self.maintenance_status: dict[str, Any] | None = None
        self.maintenance_status_refreshed_at: datetime | None = None
        self.voice_settings: dict[str, Any] | None = None
        self.voice_settings_refreshed_at: datetime | None = None
        self.schedules: dict[str, Any] | None = None
        self.schedules_refreshed_at: datetime | None = None
        self._schedule_write_lock = asyncio.Lock()
        self.selected_mowing_action = "all_area"
        self.selected_map_index: int | None = None
        self.selected_contour_id: tuple[int, int] | None = None
        self.selected_zone_id: int | None = None
        self.selected_spot_id: int | None = None
        self.selected_maintenance_point_id: int | None = None
        self.bluetooth_connected: bool | None = None
        self.runtime_status_blob: DreameLawnMowerStatusBlob | None = None
        self.runtime_telemetry_cache = DreameLawnMowerRuntimeTelemetryCache()
        self.diagnostic_events = DreameLawnMowerDiagnosticEventStore()
        self.last_batch_device_data_probe_result: dict[str, Any] | None = None
        self.last_preference_probe_result: dict[str, Any] | None = None
        self.last_preference_write_result: dict[str, Any] | None = None
        self.last_schedule_probe_result: dict[str, Any] | None = None
        self.last_schedule_write_result: dict[str, Any] | None = None
        self.last_task_status_probe_result: dict[str, Any] | None = None
        self.last_weather_probe_result: dict[str, Any] | None = None
        self.last_maintenance_reset_result: dict[str, Any] | None = None
        self._client_update_task: asyncio.Task[None] | None = None
        self._shutting_down = False

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
        self.client.set_update_callback(self._handle_client_update)

    def _handle_client_update(self) -> None:
        """Bridge device-thread MQTT updates onto the Home Assistant loop."""
        if self._shutting_down:
            return
        self.hass.loop.call_soon_threadsafe(self._schedule_client_update)

    def _schedule_client_update(self) -> None:
        """Coalesce device callbacks while one cached-state update is running."""
        if self._shutting_down or self._client_update_task is not None:
            return
        self._client_update_task = self.hass.async_create_task(
            self._async_process_client_update(),
            f"{DOMAIN}-realtime-update",
        )

    async def _async_process_client_update(self) -> None:
        """Publish cached MQTT state without waiting for the polling interval."""
        try:
            # Collapse a burst of property callbacks into the newest device state.
            await asyncio.sleep(0)
            snapshot = await self.client.async_get_cached_snapshot()
            if not snapshot.available:
                self.runtime_status_blob = None
                self.client.update_runtime_live_tracking(None, active=False)
                self.async_set_updated_data(snapshot)
                return

            runtime_active = runtime_tracking_active(snapshot)
            runtime_map_index = self._runtime_map_index()
            try:
                self.runtime_status_blob = (
                    await self.client.async_get_runtime_status_blob(
                        refresh=False,
                        include_cloud=False,
                    )
                )
                self.runtime_telemetry_cache.update(
                    self.runtime_status_blob,
                    allow_zero=runtime_active,
                    active_session=runtime_active,
                )
                self.client.update_runtime_live_tracking(
                    self.runtime_status_blob,
                    active=runtime_active,
                    map_index=runtime_map_index,
                )
            except Exception as err:  # noqa: BLE001 - best-effort MQTT metadata
                _LOGGER.debug("Failed to process realtime status blob: %s", err)
                self.runtime_status_blob = None
                self.client.update_runtime_live_tracking(
                    None,
                    active=runtime_active,
                    map_index=runtime_map_index,
                )

            try:
                self.bluetooth_connected = (
                    await self.client.async_get_bluetooth_connected(
                        refresh=False,
                        include_cloud=False,
                    )
                )
            except Exception as err:  # noqa: BLE001 - best-effort MQTT metadata
                _LOGGER.debug(
                    "Failed to process realtime Bluetooth state: %s",
                    err,
                )
            self.async_set_updated_data(snapshot)
        except Exception as err:  # noqa: BLE001 - callback must not escape HA task
            _LOGGER.debug("Failed to process cached mower update: %s", err)
        finally:
            self._client_update_task = None

    async def _async_update_data(self) -> DreameLawnMowerSnapshot:
        """Fetch the latest mower snapshot."""
        try:
            snapshot = await self.client.async_refresh()
        except DreameLawnMowerConnectionError as err:
            safe_error = sanitize_diagnostic_text(err)
            record_diagnostic_event(
                self,
                code="coordinator_update_failed",
                source="coordinator",
                message=safe_error,
                context={"exception_type": type(err).__name__},
            )
            raise UpdateFailed(safe_error) from err
        if not snapshot.available:
            self.runtime_status_blob = None
            self.client.update_runtime_live_tracking(None, active=False)
            return snapshot
        runtime_active = runtime_tracking_active(snapshot)
        previous_app_maps_refreshed_at = self.app_maps_refreshed_at
        await self.async_refresh_app_maps(force=runtime_active)
        runtime_map_index = self._runtime_map_index()
        if (
            runtime_active
            and self.app_maps_refreshed_at is previous_app_maps_refreshed_at
        ):
            # A forced refresh failed and returned the old cache. Do not stamp new
            # runtime points with an identity that may belong to another map.
            runtime_map_index = None
        try:
            self.runtime_status_blob = await self.client.async_get_runtime_status_blob(
                refresh=False,
                include_cloud=True,
            )
            self.runtime_telemetry_cache.update(
                self.runtime_status_blob,
                allow_zero=runtime_active,
                active_session=runtime_active,
            )
            self.client.update_runtime_live_tracking(
                self.runtime_status_blob,
                active=runtime_active,
                map_index=runtime_map_index,
            )
        except Exception as err:  # noqa: BLE001 - best-effort extra metadata
            _LOGGER.debug("Failed to refresh runtime status blob: %s", err)
            self.runtime_status_blob = None
            self.client.update_runtime_live_tracking(
                None,
                active=runtime_active,
                map_index=runtime_map_index,
            )
        try:
            self.bluetooth_connected = await self.client.async_get_bluetooth_connected(
                refresh=False,
                include_cloud=True,
            )
        except Exception as err:  # noqa: BLE001 - best-effort extra metadata
            _LOGGER.debug("Failed to refresh Bluetooth connection state: %s", err)
            self.bluetooth_connected = None
        await self.async_refresh_batch_device_data(force=False)
        await self.async_refresh_firmware_update_support(force=False)
        await self.async_refresh_app_map_objects(force=False)
        await self.async_refresh_vector_map_details(force=False)
        await self.async_refresh_weather_protection(force=False)
        await self.async_refresh_maintenance_status(force=False)
        await self.async_refresh_voice_settings(force=False)
        await self.async_refresh_schedules(force=False)
        return snapshot

    def _runtime_map_index(self) -> int | None:
        """Return the map identity used to scope transient runtime overlays."""
        return active_map_index(
            self.app_maps,
            selected_map_index=self.selected_map_index,
        )

    async def async_refresh_schedules(
        self,
        *,
        force: bool = False,
    ) -> dict[str, Any] | None:
        """Refresh the shared schedule cache without failing the main poll."""
        now = datetime.now(UTC)
        if (
            not force
            and self.schedules is not None
            and self.schedules_refreshed_at is not None
            and now - self.schedules_refreshed_at < SCHEDULE_REFRESH_INTERVAL
        ):
            return self.schedules
        try:
            payload = await self.client.async_get_app_schedules()
        except Exception as err:  # noqa: BLE001 - optional cloud capability
            _LOGGER.debug("Failed to refresh mower schedules: %s", err)
            if force:
                raise
            return self.schedules
        self.schedules = payload
        self.schedules_refreshed_at = now
        return payload

    async def async_set_schedule_plan_enabled(
        self,
        *,
        map_index: int,
        plan_id: int,
        enabled: bool,
    ) -> dict[str, Any]:
        """Write one schedule plan and reconcile every schedule consumer."""
        async with self._schedule_write_lock:
            result = await self.client.async_set_app_schedule_plan_enabled(
                map_index=map_index,
                plan_id=plan_id,
                enabled=enabled,
                execute=True,
                confirm_write=True,
            )
            self.last_schedule_write_result = result
            self._reconcile_cached_schedule_plan_enabled(
                map_index=map_index,
                plan_id=plan_id,
                enabled=enabled,
            )
            try:
                await self.async_refresh_schedules(force=True)
            finally:
                # The confirmed write is reflected immediately even when the
                # follow-up cloud read cannot verify it yet.
                self.async_update_listeners()
            return result

    def _reconcile_cached_schedule_plan_enabled(
        self,
        *,
        map_index: int,
        plan_id: int,
        enabled: bool,
    ) -> None:
        """Apply a confirmed schedule write to the shared cache."""
        schedules = (
            self.schedules.get("schedules")
            if isinstance(self.schedules, dict)
            else None
        )
        if not isinstance(schedules, list):
            return
        for schedule in schedules:
            if not isinstance(schedule, dict) or schedule.get("idx") != map_index:
                continue
            plans = schedule.get("plans")
            if not isinstance(plans, list):
                return
            for plan in plans:
                if isinstance(plan, dict) and plan.get("plan_id") == plan_id:
                    plan["enabled"] = enabled
                    return

    async def async_plan_schedule_upload(
        self,
        *,
        map_index: int,
        plans: Sequence[Mapping[str, Any]],
        chunk_size: int,
        execute: bool,
        confirm_write: bool,
    ) -> dict[str, Any]:
        """Plan or execute a schedule upload and reconcile shared consumers."""
        if not execute:
            result = await self.client.async_plan_app_schedule_upload(
                map_index=map_index,
                plans=plans,
                chunk_size=chunk_size,
                execute=False,
                confirm_write=confirm_write,
            )
            self.last_schedule_write_result = result
            self.async_update_listeners()
            return result

        async with self._schedule_write_lock:
            result = await self.client.async_plan_app_schedule_upload(
                map_index=map_index,
                plans=plans,
                chunk_size=chunk_size,
                execute=True,
                confirm_write=confirm_write,
            )
            self.last_schedule_write_result = result
            # Never let a recently cached pre-upload payload hide the new plans.
            self.schedules_refreshed_at = None
            try:
                await self.async_refresh_schedules(force=True)
            finally:
                self.async_update_listeners()
            return result

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
            (
                batch_schedule,
                batch_mowing_preferences,
                batch_ota_info,
            ) = await self._async_fetch_batch_device_data()
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
            self.client.async_get_batch_mowing_preferences(
                include_raw=False,
                map_index_hints=_app_map_index_hints(self.app_maps),
            ),
            self.client.async_get_batch_ota_info(include_raw=False),
        )

    async def async_refresh_firmware_update_support(
        self,
        *,
        force: bool = False,
    ) -> DreameLawnMowerFirmwareUpdateSupport | None:
        """Refresh cached firmware/update support without failing the main poll."""
        now = datetime.now(UTC)
        if (
            not force
            and self.firmware_update_support is not None
            and self.firmware_update_support_refreshed_at is not None
            and now - self.firmware_update_support_refreshed_at
            < FIRMWARE_UPDATE_REFRESH_INTERVAL
        ):
            return self.firmware_update_support

        try:
            support = await self.client.async_get_firmware_update_support(
                refresh=False,
                include_cloud=True,
                language="en",
            )
        except Exception as err:  # noqa: BLE001 - best-effort extra metadata
            _LOGGER.debug("Failed to refresh firmware update support: %s", err)
            return self.firmware_update_support

        self.firmware_update_support = support
        self.firmware_update_support_refreshed_at = now
        return support

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

    async def async_refresh_vector_map_details(
        self,
        *,
        force: bool = False,
        source: str = "vector_map_auto",
    ) -> dict[str, Any] | None:
        """Refresh cached batch vector-map details without failing the main poll."""
        now = datetime.now(UTC)
        if (
            not force
            and self.vector_map_details is not None
            and self.vector_map_details_refreshed_at is not None
            and now - self.vector_map_details_refreshed_at < VECTOR_MAP_REFRESH_INTERVAL
        ):
            return self.vector_map_details

        try:
            vector_map_details = await self.client.async_get_vector_map_details()
        except Exception as err:  # noqa: BLE001 - best-effort extra metadata
            _LOGGER.debug("Failed to refresh vector map details: %s", err)
            return self.vector_map_details

        payload = dict(vector_map_details)
        payload.setdefault("captured_at", now.isoformat())
        payload["source"] = source
        self.vector_map_details = payload
        self.vector_map_details_refreshed_at = now
        return payload

    async def async_refresh_app_maps(
        self,
        *,
        force: bool = False,
        source: str = "app_maps_auto",
    ) -> dict[str, Any] | None:
        """Refresh cached app-map payloads without failing the main poll."""
        now = datetime.now(UTC)
        if (
            not force
            and self.app_maps is not None
            and self.app_maps_refreshed_at is not None
            and now - self.app_maps_refreshed_at < APP_MAP_REFRESH_INTERVAL
        ):
            return self.app_maps

        try:
            app_maps = await self.client.async_get_app_maps(
                include_payload=True,
                include_objects=False,
                include_object_urls=False,
            )
        except Exception as err:  # noqa: BLE001 - best-effort extra metadata
            _LOGGER.debug("Failed to refresh app maps: %s", err)
            return self.app_maps

        payload = dict(app_maps)
        payload.setdefault("captured_at", now.isoformat())
        payload["source"] = source
        self.app_maps = payload
        self.app_maps_refreshed_at = now
        current_idx = active_map_index(payload)
        if current_idx is not None:
            if (
                self.selected_map_index is not None
                and self.selected_map_index != current_idx
            ):
                self.selected_contour_id = None
                self.selected_zone_id = None
                self.selected_spot_id = None
            self.selected_map_index = current_idx
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

    async def async_refresh_maintenance_status(
        self,
        *,
        force: bool = False,
        source: str = "maintenance_auto",
    ) -> dict[str, Any] | None:
        """Refresh cached read-only maintenance counter state."""
        now = datetime.now(UTC)
        if (
            not force
            and self.maintenance_status is not None
            and self.maintenance_status_refreshed_at is not None
            and now - self.maintenance_status_refreshed_at
            < MAINTENANCE_REFRESH_INTERVAL
        ):
            return self.maintenance_status

        try:
            maintenance_status = await self.client.async_get_maintenance_status(
                include_raw=False,
            )
        except Exception as err:  # noqa: BLE001 - best-effort extra metadata
            _LOGGER.debug("Failed to refresh maintenance status: %s", err)
            return self.maintenance_status

        payload = dict(maintenance_status)
        payload.setdefault("captured_at", now.isoformat())
        payload["source"] = source
        self.maintenance_status = payload
        self.maintenance_status_refreshed_at = now
        return payload

    async def async_refresh_voice_settings(
        self,
        *,
        force: bool = False,
        source: str = "voice_settings_auto",
    ) -> dict[str, Any] | None:
        """Refresh cached voice/language settings without failing the main poll."""
        now = datetime.now(UTC)
        if (
            not force
            and self.voice_settings is not None
            and self.voice_settings_refreshed_at is not None
            and now - self.voice_settings_refreshed_at < VOICE_SETTINGS_REFRESH_INTERVAL
        ):
            return self.voice_settings

        try:
            voice_settings = await self.client.async_get_voice_settings(
                include_raw=False
            )
        except Exception as err:  # noqa: BLE001 - best-effort extra metadata
            _LOGGER.debug("Failed to refresh voice settings: %s", err)
            return self.voice_settings

        payload = {
            "captured_at": now.isoformat(),
            "source": source,
            "voice_settings": voice_settings,
        }
        self.voice_settings = payload
        self.voice_settings_refreshed_at = now
        return payload

    async def async_switch_current_map(self, map_index: int) -> None:
        """Switch the active mower map and refresh all map-scoped state."""
        await self.client.async_switch_current_map(map_index)
        self.selected_map_index = map_index
        self.selected_contour_id = None
        self.selected_zone_id = None
        self.selected_spot_id = None
        self.selected_maintenance_point_id = None
        await self.async_request_refresh()
        await self.async_refresh_app_maps(
            force=True,
            source="app_maps_switch_current_map",
        )
        await self.async_refresh_vector_map_details(
            force=True,
            source="vector_map_switch_current_map",
        )
        self.async_update_listeners()

    async def async_shutdown(self) -> None:
        """Disconnect client resources."""
        self._shutting_down = True
        self.client.set_update_callback(None)
        task = self._client_update_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await self.client.async_close()


def _app_map_index_hints(app_maps: Mapping[str, Any] | None) -> list[int]:
    """Return known app map ids in display order for batch settings alignment."""
    maps = app_maps.get("maps") if isinstance(app_maps, Mapping) else None
    if not isinstance(maps, Sequence) or isinstance(maps, str | bytes | bytearray):
        return []

    indices: list[int] = []
    for entry in maps:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("created") is False:
            continue
        index = entry.get("idx")
        if not isinstance(index, int) or index < 0 or index in indices:
            continue
        indices.append(index)
    return indices
