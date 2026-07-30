"""Coordinator for Dreame lawn mower updates."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import (
    DreameLawnMowerClient,
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
from .coordinator_connectivity import DreameLawnMowerConnectivityMixin
from .coordinator_refresh import (
    METADATA_REFRESH_CONCURRENCY,
    DreameLawnMowerRefreshMixin,
    runtime_tracking_active,
)
from .diagnostic_events import DreameLawnMowerDiagnosticEventStore
from .dreame_lawn_mower_client.models import (
    DreameLawnMowerStatusBlob,
    display_name_for_model,
)
from .dreame_lawn_mower_client.schedule import (
    decode_schedule_payload_text,
    encode_schedule_payload_text,
)
from .performance import DreameLawnMowerPerformanceTracker
from .runtime_cache import DreameLawnMowerRuntimeTelemetryCache
from .schedule_cache import (
    has_complete_schedule_cache,
    invalidate_schedule_slot,
    merge_app_schedule_payload,
    merge_batch_schedule_payload,
    schedule_entry_has_usable_data,
    schedule_payload_has_usable_data,
)

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
DEVICE_SNAPSHOT_GENERATION_HISTORY = 32
PENDING_SCHEDULE_PLAN_MAX_CONTRADICTORY_READS = 1
BATCH_SCHEDULE_RESULT_COALESCE_SECONDS = 1.0


_runtime_tracking_active = runtime_tracking_active


class DreameLawnMowerCoordinator(
    DreameLawnMowerConnectivityMixin,
    DreameLawnMowerRefreshMixin,
    DataUpdateCoordinator[DreameLawnMowerSnapshot],
):
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
        self.app_maps_refresh_succeeded = False
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
        self._schedule_cache_generation = 0
        self._schedule_read_generation = 0
        self._published_schedule_read_generation = 0
        self._pending_schedule_plan_states: dict[
            tuple[int, int],
            tuple[int | None, bool],
        ] = {}
        self._pending_schedule_plan_state_contradictions: dict[
            tuple[int, int],
            int,
        ] = {}
        self._pending_schedule_uploads: dict[int, dict[str, Any]] = {}
        self._pending_schedule_upload_contradictions: dict[int, int] = {}
        self._pending_schedule_upload_active_indices: set[int] = set()
        self._batch_schedule_read_task: asyncio.Task[dict[str, Any]] | None = None
        self._batch_schedule_read_key: tuple[int | None, bool] | None = None
        self._batch_schedule_read_completed_at: float | None = None
        self._schedule_write_lock = asyncio.Lock()
        self._preference_write_lock = asyncio.Lock()
        self._device_refresh_lock = asyncio.Lock()
        self._device_snapshot_generation = 0
        self._published_device_snapshot_generation = 0
        self._device_snapshot_generations: dict[
            int,
            tuple[DreameLawnMowerSnapshot, int],
        ] = {}
        self._retained_device_snapshot_ids: set[int] = set()
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
        self.performance = DreameLawnMowerPerformanceTracker()
        self.last_batch_device_data_probe_result: dict[str, Any] | None = None
        self.last_map_probe_result: dict[str, Any] | None = None
        self.last_preference_probe_result: dict[str, Any] | None = None
        self.last_preference_write_result: dict[str, Any] | None = None
        self.last_schedule_probe_result: dict[str, Any] | None = None
        self.last_schedule_write_result: dict[str, Any] | None = None
        self.last_task_status_probe_result: dict[str, Any] | None = None
        self.last_weather_probe_result: dict[str, Any] | None = None
        self.last_maintenance_reset_result: dict[str, Any] | None = None
        self._client_update_task: asyncio.Task[None] | None = None
        self._client_update_pending = False
        self._metadata_refresh_task: asyncio.Task[None] | None = None
        self._metadata_shutdown_close_task: asyncio.Task[None] | None = None
        self._metadata_refresh_semaphore = asyncio.Semaphore(
            METADATA_REFRESH_CONCURRENCY
        )
        self._metadata_refresh_pending = False
        self._metadata_refresh_publish = True
        self._runtime_map_identity_verified = False
        self._foreground_refresh_count = 0
        self._metadata_refresh_count = 0
        self._shutting_down = False
        self._initialize_connectivity_recovery()

        super().__init__(
            hass,
            logger=_LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(
                    CONF_SCAN_INTERVAL,
                    DEFAULT_SCAN_INTERVAL_SECONDS,
                )
            ),
        )
        self.client.set_update_callback(self._handle_client_update)

    async def async_refresh_video_safety_state(self) -> DreameLawnMowerSnapshot:
        """Refresh only authoritative mower state before enabling live video.

        Video startup must not wait for maps, schedules, runtime tracks, or
        background metadata.  The shared lock still serializes the underlying
        cloud owner with the normal coordinator refresh.
        """
        for _attempt in range(2):
            async with self._device_refresh_lock:
                try:
                    snapshot = (
                        await self.client.async_refresh_authoritative_snapshot()
                    )
                except Exception as err:
                    self._record_connectivity_failure(err)
                    raise
                if not getattr(snapshot, "available", True):
                    self._record_connectivity_failure("Mower is temporarily offline.")
                    return snapshot
                self._record_device_snapshot(snapshot)
                if self._device_snapshot_is_stale(snapshot):
                    continue
                self._record_connectivity_success(snapshot)
                self.async_set_updated_data(snapshot)
                return snapshot
        raise RuntimeError("Mower state changed during video safety refresh.")

    def _record_device_snapshot(
        self,
        snapshot: DreameLawnMowerSnapshot,
        *,
        retain: bool = False,
    ) -> None:
        """Assign fetch order so slow hydration cannot publish stale state."""
        self._device_snapshot_generation = (
            getattr(self, "_device_snapshot_generation", 0) + 1
        )
        if not hasattr(self, "_device_snapshot_generations"):
            self._device_snapshot_generations = {}
        snapshot_id = id(snapshot)
        self._device_snapshot_generations[snapshot_id] = (
            snapshot,
            self._device_snapshot_generation,
        )
        if not hasattr(self, "_retained_device_snapshot_ids"):
            self._retained_device_snapshot_ids = set()
        if retain:
            self._retained_device_snapshot_ids.add(snapshot_id)
        while (
            len(self._device_snapshot_generations)
            > DEVICE_SNAPSHOT_GENERATION_HISTORY
        ):
            evictable = (
                snapshot_id
                for snapshot_id in self._device_snapshot_generations
                if snapshot_id not in self._retained_device_snapshot_ids
            )
            oldest = min(
                evictable,
                key=lambda snapshot_id: self._device_snapshot_generations[
                    snapshot_id
                ][1],
                default=None,
            )
            if oldest is None:
                break
            self._device_snapshot_generations.pop(oldest)

    def _release_device_snapshot(self, snapshot: DreameLawnMowerSnapshot) -> None:
        """Allow a hydrated snapshot record to be evicted by a later fetch."""
        retained = getattr(self, "_retained_device_snapshot_ids", None)
        if retained is not None:
            retained.discard(id(snapshot))

    def async_set_updated_data(self, data: DreameLawnMowerSnapshot) -> None:
        """Publish fetched snapshots only while they remain the newest."""
        if self._device_snapshot_is_stale(data):
            return
        generations = getattr(self, "_device_snapshot_generations", None)
        recorded = generations.get(id(data)) if generations else None
        generation = (
            recorded[1]
            if recorded is not None and recorded[0] is data
            else None
        )
        if generation is not None:
            self._published_device_snapshot_generation = generation
        super().async_set_updated_data(data)

    def _device_snapshot_is_stale(self, snapshot: DreameLawnMowerSnapshot) -> bool:
        """Return whether a newer device snapshot has already been fetched."""
        generations = getattr(self, "_device_snapshot_generations", None)
        recorded = generations.get(id(snapshot)) if generations else None
        if recorded is None or recorded[0] is not snapshot:
            return False
        generation = recorded[1]
        newest_fetched = getattr(self, "_device_snapshot_generation", 0)
        published = getattr(self, "_published_device_snapshot_generation", 0)
        newest_generation = max(newest_fetched, published)
        if generation >= newest_generation:
            return False
        _LOGGER.debug(
            "Ignoring stale mower snapshot generation %s after generation %s",
            generation,
            newest_generation,
        )
        return True

    def _handle_client_update(self) -> None:
        """Bridge device-thread MQTT updates onto the Home Assistant loop."""
        if self._shutting_down:
            return
        self.hass.loop.call_soon_threadsafe(self._schedule_client_update)

    def _schedule_client_update(self) -> None:
        """Coalesce device callbacks while one cached-state update is running."""
        if self._shutting_down:
            return
        if self._client_update_task is not None:
            self._client_update_pending = True
            return
        self._client_update_pending = False
        self._client_update_task = self.hass.async_create_task(
            self._async_process_client_update(),
            f"{DOMAIN}-realtime-update",
        )

    async def _async_process_client_update(self) -> None:
        """Publish cached MQTT state without waiting for the polling interval."""
        snapshot: DreameLawnMowerSnapshot | None = None
        try:
            # Collapse a burst of property callbacks into the newest device state.
            await asyncio.sleep(0)
            if not hasattr(self, "_device_refresh_lock"):
                self._device_refresh_lock = asyncio.Lock()
            async with self._device_refresh_lock:
                snapshot = await self.client.async_get_cached_snapshot()
                if not snapshot.available:
                    retained = self._record_connectivity_failure(
                        "Realtime mower connection was interrupted."
                    )
                    if retained is not None:
                        self.async_update_listeners()
                        return
                self._record_device_snapshot(snapshot, retain=True)
            if self._device_snapshot_is_stale(snapshot):
                return
            if not snapshot.available:
                self.runtime_status_blob = None
                self.client.update_runtime_live_tracking(None, active=False)
                self.async_set_updated_data(snapshot)
                return
            runtime_active = runtime_tracking_active(snapshot)
            runtime_map_index = (
                self._runtime_map_index()
                if not runtime_active
                or getattr(self, "_runtime_map_identity_verified", False)
                else None
            )
            runtime_status_blob: DreameLawnMowerStatusBlob | None = None
            runtime_status_error: Exception | None = None
            try:
                runtime_status_blob = await self.client.async_get_runtime_status_blob(
                    refresh=False,
                    include_cloud=False,
                )
            except Exception as err:  # noqa: BLE001 - best-effort MQTT metadata
                runtime_status_error = err

            bluetooth_connected: bool | None = None
            bluetooth_error: Exception | None = None
            try:
                bluetooth_connected = await self.client.async_get_bluetooth_connected(
                    refresh=False,
                    include_cloud=False,
                )
            except Exception as err:  # noqa: BLE001 - best-effort MQTT metadata
                bluetooth_error = err

            # Both optional reads can yield while a newer authoritative fetch
            # publishes. Commit no runtime or Bluetooth side effects until the
            # cached snapshot is still current after every await.
            if self._device_snapshot_is_stale(snapshot):
                return

            if runtime_status_error is None:
                try:
                    self.runtime_status_blob = runtime_status_blob
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
                    runtime_status_error = err
            if runtime_status_error is not None:
                _LOGGER.debug(
                    "Failed to process realtime status blob: %s",
                    runtime_status_error,
                )
                self.runtime_status_blob = None
                self.client.update_runtime_live_tracking(
                    None,
                    active=runtime_active,
                    map_index=runtime_map_index,
                )

            if bluetooth_error is None:
                self.bluetooth_connected = bluetooth_connected
            else:
                _LOGGER.debug(
                    "Failed to process realtime Bluetooth state: %s",
                    bluetooth_error,
                )
            self.async_set_updated_data(snapshot)
        except Exception as err:  # noqa: BLE001 - callback must not escape HA task
            _LOGGER.debug("Failed to process cached mower update: %s", err)
        finally:
            if snapshot is not None:
                self._release_device_snapshot(snapshot)
            self._client_update_task = None
            if (
                getattr(self, "_client_update_pending", False)
                and not getattr(self, "_shutting_down", False)
            ):
                self._client_update_pending = False
                self._schedule_client_update()

    async def async_refresh_schedules(
        self,
        *,
        force: bool = False,
    ) -> dict[str, Any] | None:
        """Refresh schedules through the fast batch path with app fallback."""
        now = datetime.now(UTC)
        refresh_generation = getattr(self, "_schedule_cache_generation", 0)
        if (
            not force
            and self.schedules is not None
            and self.schedules_refreshed_at is not None
            and now - self.schedules_refreshed_at < SCHEDULE_REFRESH_INTERVAL
        ):
            return self.schedules

        request_app_maps = getattr(self, "app_maps", None)
        request_map_indices = _app_map_index_hints(request_app_maps)
        request_map_hints_authoritative = _app_map_hints_are_authoritative(
            request_app_maps,
            refresh_succeeded=getattr(
                self,
                "app_maps_refresh_succeeded",
                False,
            ),
        )

        try:
            action_read_generation = self._begin_schedule_read()
            payload = await self.client.async_get_app_schedules(
                include_current_task=False,
                map_indices=(
                    [-1, *request_map_indices]
                    if request_map_indices or request_map_hints_authoritative
                    else None
                ),
            )
        except Exception as err:  # noqa: BLE001 - optional cloud capability
            _LOGGER.debug("Failed to refresh mower schedules: %s", err)
            if force:
                raise
            return self.schedules
        if not self._schedule_refresh_is_current(refresh_generation):
            return self.schedules
        action_read_succeeded = schedule_payload_has_usable_data(payload)
        if (
            action_read_succeeded
            and not self._schedule_read_can_publish(action_read_generation)
        ):
            return self.schedules
        app_maps = getattr(self, "app_maps", None)
        known_map_indices = _app_map_index_hints(app_maps)
        map_hints_authoritative = _app_map_hints_are_authoritative(
            app_maps,
            refresh_succeeded=getattr(
                self,
                "app_maps_refresh_succeeded",
                False,
            ),
        )
        map_index_hint = self._schedule_map_index_hint()
        expected_indices = _schedule_expected_indices(
            self.schedules,
            payload,
            known_map_indices,
            map_hints_authoritative=map_hints_authoritative,
        )
        self.schedules = merge_app_schedule_payload(
            self.schedules,
            payload,
            expected_indices=expected_indices,
            prune_unexpected_indices=map_hints_authoritative,
        )
        if action_read_succeeded:
            self._record_schedule_read_publication(action_read_generation)
        if map_hints_authoritative:
            self._prune_pending_schedule_writes(known_map_indices)
        self._acknowledge_pending_schedule_plan_states(payload)
        self._acknowledge_pending_schedule_uploads(payload)
        self._apply_pending_schedule_plan_states()
        self._apply_pending_schedule_uploads()
        action_read_indices = _usable_schedule_indices(payload)
        action_read_complete = set(expected_indices).issubset(action_read_indices)
        allow_unknown_batch_slot = not (
            map_hints_authoritative and action_read_complete
        )
        batch_read_succeeded = False
        try:
            batch_read_generation = self._begin_schedule_read()
            batch_payload = await self._async_get_shared_batch_schedules(
                map_index_hint=map_index_hint,
                force=force,
            )
        except Exception as err:  # noqa: BLE001 - optional cloud capability
            _LOGGER.debug("Failed to recover batch mower schedules: %s", err)
        else:
            if not self._schedule_refresh_is_current(refresh_generation):
                return self.schedules
            latest_app_maps = getattr(self, "app_maps", None)
            latest_map_indices = _app_map_index_hints(latest_app_maps)
            latest_map_hints_authoritative = _app_map_hints_are_authoritative(
                latest_app_maps,
                refresh_succeeded=getattr(
                    self,
                    "app_maps_refresh_succeeded",
                    False,
                ),
            )
            latest_map_index_hint = self._schedule_map_index_hint()
            if latest_map_index_hint != map_index_hint:
                self._invalidate_schedule_map_hint()
                batch_payload = _discard_stale_batch_schedule(batch_payload)
            latest_expected_indices = _schedule_expected_indices(
                self.schedules,
                payload,
                latest_map_indices,
                map_hints_authoritative=latest_map_hints_authoritative,
            )
            if (
                latest_expected_indices != expected_indices
                or latest_map_hints_authoritative != map_hints_authoritative
            ):
                known_map_indices = latest_map_indices
                map_hints_authoritative = latest_map_hints_authoritative
                expected_indices = latest_expected_indices
                self.schedules = merge_app_schedule_payload(
                    self.schedules,
                    payload,
                    expected_indices=expected_indices,
                    prune_unexpected_indices=map_hints_authoritative,
                )
                if map_hints_authoritative:
                    self._prune_pending_schedule_writes(known_map_indices)
                action_read_complete = set(expected_indices).issubset(
                    action_read_indices
                )
                allow_unknown_batch_slot = not (
                    map_hints_authoritative and action_read_complete
                )
            allowed_batch_hint_indices = (
                [map_index_hint]
                if (
                    allow_unknown_batch_slot
                    and map_hints_authoritative
                    and map_index_hint in known_map_indices
                )
                else None
            )
            batch_read_succeeded = self._cache_batch_schedules(
                batch_payload,
                now=now,
                allow_incomplete=allow_unknown_batch_slot,
                allowed_hint_indices=allowed_batch_hint_indices,
                read_generation=batch_read_generation,
                preserve_indices=action_read_indices,
            )
            batch_schedules = batch_payload.get("schedules")
            if (
                not batch_read_succeeded
                and not allow_unknown_batch_slot
                and not batch_payload.get("errors")
                and isinstance(batch_schedules, Sequence)
                and not isinstance(batch_schedules, str | bytes | bytearray)
                and len(batch_schedules) == 1
                and isinstance(batch_schedules[0], Mapping)
                and isinstance(batch_schedules[0].get("version"), int)
                and not isinstance(batch_schedules[0].get("version"), bool)
                and isinstance(self.schedules, dict)
            ):
                self.schedules["active_selection_available"] = False
        if not self._schedule_refresh_is_current(refresh_generation):
            return self.schedules
        if (
            action_read_succeeded
            and not batch_read_succeeded
            and not _schedule_payload_has_active_selection(self.schedules)
            and isinstance(self.schedules, dict)
        ):
            # Without SCHDT or a batch version, the normal calendar cannot
            # safely choose one slot. Keep the decoded schedules for the
            # diagnostic all-schedules view without presenting them as active.
            self.schedules["active_selection_available"] = False
        if action_read_succeeded or batch_read_succeeded:
            self.schedules_refreshed_at = now
        return self.schedules

    def _cache_batch_schedules(
        self,
        payload: Mapping[str, Any],
        *,
        now: datetime,
        allow_incomplete: bool = False,
        allowed_hint_indices: Sequence[int] | None = None,
        read_generation: int | None = None,
        preserve_indices: Sequence[int] = (),
    ) -> bool:
        """Merge one fast effective-schedule read into the app schedule cache."""
        if read_generation is not None and not self._schedule_read_can_publish(
            read_generation
        ):
            return False
        known_map_indices = _app_map_index_hints(getattr(self, "app_maps", None))
        if (
            not isinstance(self.schedules, Mapping)
            or (
                not allow_incomplete
                and not self._has_complete_schedule_cache(known_map_indices)
            )
        ):
            return False
        normalized = merge_batch_schedule_payload(
            self.schedules,
            payload,
            captured_at=now,
            allow_unknown_slot=allow_incomplete,
            allowed_hint_indices=(
                allowed_hint_indices
                if allowed_hint_indices is not None
                else ([] if allow_incomplete else [-1, *known_map_indices])
            ),
            preserve_indices=preserve_indices,
        )
        if normalized is None:
            return False
        self.schedules = normalized
        self.schedules_refreshed_at = now
        if read_generation is not None:
            self._record_schedule_read_publication(read_generation)
        self._apply_pending_schedule_plan_states()
        self._apply_pending_schedule_uploads()
        return True

    def _has_complete_schedule_cache(
        self,
        known_map_indices: Sequence[int],
    ) -> bool:
        """Return whether default and every known physical map are represented."""
        return has_complete_schedule_cache(self.schedules, known_map_indices)

    def _schedule_refresh_is_current(self, generation: int) -> bool:
        """Return whether a schedule read predates no confirmed write."""
        return generation == getattr(self, "_schedule_cache_generation", 0)

    def _begin_schedule_read(self) -> int:
        """Return an ordering token for one schedule request."""
        generation = getattr(self, "_schedule_read_generation", 0) + 1
        self._schedule_read_generation = generation
        return generation

    def _schedule_read_can_publish(self, generation: int) -> bool:
        """Return whether no newer schedule request has published."""
        return generation >= getattr(
            self,
            "_published_schedule_read_generation",
            0,
        )

    def _record_schedule_read_publication(self, generation: int) -> None:
        """Record the newest schedule request allowed to publish."""
        self._published_schedule_read_generation = generation

    def _invalidate_inflight_schedule_refreshes(self) -> None:
        """Prevent reads started before a confirmed write from publishing."""
        self._schedule_cache_generation = (
            getattr(self, "_schedule_cache_generation", 0) + 1
        )

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
            self._invalidate_inflight_schedule_refreshes()
            schedule_version = _schedule_write_version(result)
            pending_states = getattr(self, "_pending_schedule_plan_states", None)
            if pending_states is None:
                pending_states = {}
                self._pending_schedule_plan_states = pending_states
            pending_states[(map_index, plan_id)] = (
                schedule_version,
                bool(enabled),
            )
            pending_contradictions = getattr(
                self,
                "_pending_schedule_plan_state_contradictions",
                None,
            )
            if pending_contradictions is None:
                pending_contradictions = {}
                self._pending_schedule_plan_state_contradictions = (
                    pending_contradictions
                )
            pending_contradictions.pop((map_index, plan_id), None)
            self._reconcile_cached_schedule_plan_enabled(
                map_index=map_index,
                plan_id=plan_id,
                enabled=enabled,
                schedule_version=schedule_version,
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
        schedule_version: int | None = None,
    ) -> None:
        """Apply a confirmed schedule write to the shared cache."""
        schedules = (
            self.schedules.get("schedules")
            if isinstance(self.schedules, dict)
            else None
        )
        if not isinstance(schedules, list):
            return
        matching_numeric_indices = {
            schedule.get("idx")
            for schedule in schedules
            if isinstance(schedule, Mapping)
            and schedule.get("version") == schedule_version
            and isinstance(schedule.get("idx"), int)
            and not isinstance(schedule.get("idx"), bool)
        }
        unknown_slot_is_unambiguous = (
            not matching_numeric_indices or matching_numeric_indices == {map_index}
        )
        for schedule in schedules:
            if not isinstance(schedule, dict) or not (
                schedule.get("idx") == map_index
                or (
                    schedule_version is not None
                    and schedule.get("idx") is None
                    and schedule.get("version") == schedule_version
                    and unknown_slot_is_unambiguous
                )
            ):
                continue
            plans = schedule.get("plans")
            if not isinstance(plans, list):
                return
            for plan in plans:
                if isinstance(plan, dict) and plan.get("plan_id") == plan_id:
                    plan["enabled"] = enabled
                    return
        if (
            schedule_version is not None
            and not unknown_slot_is_unambiguous
            and isinstance(self.schedules, dict)
            and self.schedules.get("active_schedule_index") is None
            and any(
                isinstance(schedule, Mapping)
                and schedule.get("idx") is None
                and schedule.get("version") == schedule_version
                for schedule in schedules
            )
        ):
            self.schedules["active_selection_available"] = False

    def _acknowledge_pending_schedule_plan_states(
        self,
        payload: Mapping[str, Any],
    ) -> None:
        """Clear confirmed writes once an action read observes their result."""
        pending_states = getattr(self, "_pending_schedule_plan_states", None)
        schedules = payload.get("schedules")
        if not pending_states or not isinstance(schedules, Sequence):
            return
        pending_contradictions = getattr(
            self,
            "_pending_schedule_plan_state_contradictions",
            None,
        )
        if pending_contradictions is None:
            pending_contradictions = {}
            self._pending_schedule_plan_state_contradictions = (
                pending_contradictions
            )
        for key, (version, enabled) in tuple(pending_states.items()):
            map_index, plan_id = key
            schedule = next(
                (
                    entry
                    for entry in schedules
                    if isinstance(entry, Mapping)
                    and entry.get("idx") == map_index
                    and schedule_entry_has_usable_data(entry)
                ),
                None,
            )
            if schedule is None:
                continue
            if version is not None and schedule.get("version") != version:
                pending_states.pop(key, None)
                pending_contradictions.pop(key, None)
                continue
            plans = schedule.get("plans")
            plan = next(
                (
                    entry
                    for entry in plans
                    if isinstance(entry, Mapping) and entry.get("plan_id") == plan_id
                ),
                None,
            )
            if plan is None or bool(plan.get("enabled")) == enabled:
                pending_states.pop(key, None)
                pending_contradictions.pop(key, None)
                continue
            contradictory_reads = pending_contradictions.get(key, 0) + 1
            if (
                contradictory_reads
                > PENDING_SCHEDULE_PLAN_MAX_CONTRADICTORY_READS
            ):
                pending_states.pop(key, None)
                pending_contradictions.pop(key, None)
            else:
                pending_contradictions[key] = contradictory_reads

    def _apply_pending_schedule_plan_states(self) -> None:
        """Keep confirmed toggles visible while cloud readbacks lag."""
        pending_states = getattr(self, "_pending_schedule_plan_states", None)
        schedules = (
            self.schedules.get("schedules")
            if isinstance(self.schedules, Mapping)
            else None
        )
        if not pending_states or not isinstance(schedules, Sequence):
            return
        for (map_index, plan_id), (version, enabled) in pending_states.items():
            matching_numeric_indices = {
                schedule.get("idx")
                for schedule in schedules
                if isinstance(schedule, Mapping)
                and schedule.get("version") == version
                and isinstance(schedule.get("idx"), int)
                and not isinstance(schedule.get("idx"), bool)
            }
            unknown_slot_is_unambiguous = (
                not matching_numeric_indices
                or matching_numeric_indices == {map_index}
            )
            for schedule in schedules:
                if not isinstance(schedule, dict) or not (
                    schedule.get("idx") == map_index
                    or (
                        version is not None
                        and schedule.get("idx") is None
                        and schedule.get("version") == version
                        and unknown_slot_is_unambiguous
                    )
                ):
                    continue
                if version is not None and schedule.get("version") != version:
                    continue
                plans = schedule.get("plans")
                if not isinstance(plans, Sequence):
                    continue
                for plan in plans:
                    if isinstance(plan, dict) and plan.get("plan_id") == plan_id:
                        plan["enabled"] = enabled
            if (
                version is not None
                and not unknown_slot_is_unambiguous
                and isinstance(self.schedules, dict)
                and self.schedules.get("active_schedule_index") is None
                and any(
                    isinstance(schedule, Mapping)
                    and schedule.get("idx") is None
                    and schedule.get("version") == version
                    for schedule in schedules
                )
            ):
                self.schedules["active_selection_available"] = False

    def _prune_pending_schedule_writes(
        self,
        known_map_indices: Sequence[int],
    ) -> None:
        """Drop pending writes for maps absent from authoritative app metadata."""
        valid_indices = {-1, *known_map_indices}
        pending_states = getattr(self, "_pending_schedule_plan_states", None)
        pending_state_contradictions = getattr(
            self,
            "_pending_schedule_plan_state_contradictions",
            None,
        )
        if pending_states:
            for key in tuple(pending_states):
                if key[0] in valid_indices:
                    continue
                pending_states.pop(key, None)
                if pending_state_contradictions is not None:
                    pending_state_contradictions.pop(key, None)

        pending_uploads = getattr(self, "_pending_schedule_uploads", None)
        pending_upload_contradictions = getattr(
            self,
            "_pending_schedule_upload_contradictions",
            None,
        )
        pending_active_indices = getattr(
            self,
            "_pending_schedule_upload_active_indices",
            None,
        )
        if pending_uploads:
            for map_index in tuple(pending_uploads):
                if map_index in valid_indices:
                    continue
                pending_uploads.pop(map_index, None)
                if pending_upload_contradictions is not None:
                    pending_upload_contradictions.pop(map_index, None)
                if pending_active_indices is not None:
                    pending_active_indices.discard(map_index)

    def _remember_pending_schedule_upload(
        self,
        *,
        map_index: int,
        plans: Sequence[Mapping[str, Any]],
        schedule_version: int | None,
    ) -> None:
        """Retain a confirmed full upload until an action read settles it."""
        normalized_plans = decode_schedule_payload_text(
            encode_schedule_payload_text(list(plans))
        )
        schedules = (
            self.schedules.get("schedules")
            if isinstance(getattr(self, "schedules", None), Mapping)
            else None
        )
        cached_schedule = next(
            (
                schedule
                for schedule in schedules or []
                if isinstance(schedule, Mapping)
                and schedule.get("idx") == map_index
            ),
            None,
        )
        if schedule_version is None and isinstance(cached_schedule, Mapping):
            cached_version = cached_schedule.get("version")
            if isinstance(cached_version, int) and not isinstance(
                cached_version,
                bool,
            ):
                schedule_version = cached_version

        pending_schedule: dict[str, Any] = {
            "idx": map_index,
            "available": bool(normalized_plans),
            "writable": True,
            "version": schedule_version,
            "plan_count": len(normalized_plans),
            "enabled_plan_count": sum(
                1 for plan in normalized_plans if plan.get("enabled")
            ),
            "plans": normalized_plans,
        }
        if isinstance(cached_schedule, Mapping):
            for key in ("label", "name"):
                if key in cached_schedule:
                    pending_schedule[key] = cached_schedule[key]

        pending_uploads = getattr(self, "_pending_schedule_uploads", None)
        if pending_uploads is None:
            pending_uploads = {}
            self._pending_schedule_uploads = pending_uploads
        pending_uploads[map_index] = pending_schedule
        pending_contradictions = getattr(
            self,
            "_pending_schedule_upload_contradictions",
            None,
        )
        if pending_contradictions is None:
            pending_contradictions = {}
            self._pending_schedule_upload_contradictions = pending_contradictions
        pending_contradictions.pop(map_index, None)
        pending_active_indices = getattr(
            self,
            "_pending_schedule_upload_active_indices",
            None,
        )
        if pending_active_indices is None:
            pending_active_indices = set()
            self._pending_schedule_upload_active_indices = pending_active_indices
        if (
            isinstance(getattr(self, "schedules", None), Mapping)
            and self.schedules.get("active_schedule_index") == map_index
        ):
            pending_active_indices.add(map_index)
        else:
            pending_active_indices.discard(map_index)

    def _acknowledge_pending_schedule_uploads(
        self,
        payload: Mapping[str, Any],
    ) -> None:
        """Settle full uploads only from authoritative action-slot reads."""
        pending_uploads = getattr(self, "_pending_schedule_uploads", None)
        schedules = payload.get("schedules")
        if not pending_uploads or not isinstance(schedules, Sequence):
            return
        pending_contradictions = getattr(
            self,
            "_pending_schedule_upload_contradictions",
            None,
        )
        if pending_contradictions is None:
            pending_contradictions = {}
            self._pending_schedule_upload_contradictions = pending_contradictions
        pending_active_indices = getattr(
            self,
            "_pending_schedule_upload_active_indices",
            None,
        )
        if pending_active_indices is None:
            pending_active_indices = set()
            self._pending_schedule_upload_active_indices = pending_active_indices

        for map_index, pending_schedule in tuple(pending_uploads.items()):
            schedule = next(
                (
                    entry
                    for entry in schedules
                    if isinstance(entry, Mapping)
                    and entry.get("idx") == map_index
                    and schedule_entry_has_usable_data(entry)
                ),
                None,
            )
            if schedule is None:
                continue
            pending_version = pending_schedule.get("version")
            if (
                pending_version is not None
                and schedule.get("version") != pending_version
            ):
                pending_uploads.pop(map_index, None)
                pending_contradictions.pop(map_index, None)
                pending_active_indices.discard(map_index)
                continue
            if schedule.get("plans") == pending_schedule.get("plans"):
                if (
                    map_index in pending_active_indices
                    and isinstance(self.schedules, dict)
                ):
                    self.schedules["active_schedule_version"] = pending_version
                    self.schedules["active_schedule_index"] = map_index
                    self.schedules["active_selection_available"] = True
                pending_uploads.pop(map_index, None)
                pending_contradictions.pop(map_index, None)
                pending_active_indices.discard(map_index)
                continue
            contradictory_reads = pending_contradictions.get(map_index, 0) + 1
            if (
                contradictory_reads
                > PENDING_SCHEDULE_PLAN_MAX_CONTRADICTORY_READS
            ):
                pending_uploads.pop(map_index, None)
                pending_contradictions.pop(map_index, None)
                pending_active_indices.discard(map_index)
            else:
                pending_contradictions[map_index] = contradictory_reads

    def _apply_pending_schedule_uploads(self) -> None:
        """Keep confirmed full uploads visible while cloud readbacks lag."""
        pending_uploads = getattr(self, "_pending_schedule_uploads", None)
        schedules = (
            self.schedules.get("schedules")
            if isinstance(getattr(self, "schedules", None), dict)
            else None
        )
        if not pending_uploads or not isinstance(schedules, list):
            return

        for map_index, pending_schedule in pending_uploads.items():
            replaced = False
            for position, schedule in enumerate(schedules):
                if isinstance(schedule, Mapping) and schedule.get("idx") == map_index:
                    schedules[position] = deepcopy(pending_schedule)
                    replaced = True
                    break
            if not replaced:
                schedules.append(deepcopy(pending_schedule))

            pending_version = pending_schedule.get("version")
            unknown_fallback = next(
                (
                    schedule
                    for schedule in schedules
                    if isinstance(schedule, Mapping)
                    and schedule.get("idx") is None
                    and schedule.get("version") == pending_version
                ),
                None,
            )
            if (
                isinstance(unknown_fallback, Mapping)
                and unknown_fallback.get("plans") != pending_schedule.get("plans")
                and self.schedules.get("active_schedule_index") is None
            ):
                # The batch version cannot identify which colliding slot is
                # active, and its content may predate the confirmed upload.
                self.schedules["active_selection_available"] = False

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
            self._invalidate_inflight_schedule_refreshes()
            pending_states = getattr(self, "_pending_schedule_plan_states", None)
            if pending_states:
                pending_contradictions = getattr(
                    self,
                    "_pending_schedule_plan_state_contradictions",
                    {},
                )
                for key in tuple(pending_states):
                    if key[0] == map_index:
                        pending_states.pop(key, None)
                        pending_contradictions.pop(key, None)
            schedule_version = _schedule_write_version(result)
            self._remember_pending_schedule_upload(
                map_index=map_index,
                plans=plans,
                schedule_version=schedule_version,
            )
            # Never let a recently cached pre-upload payload hide the new plans.
            self.schedules = invalidate_schedule_slot(
                getattr(self, "schedules", None),
                map_index,
                schedule_version=schedule_version,
            )
            self._apply_pending_schedule_uploads()
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

        schedule_generation = getattr(self, "_schedule_cache_generation", 0)
        try:
            (
                batch_schedule,
                batch_mowing_preferences,
                batch_ota_info,
                batch_schedule_generation,
            ) = await self._async_fetch_batch_device_data(force=force)
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
        if (
            batch_schedule is not self.schedules
            and self._schedule_refresh_is_current(schedule_generation)
        ):
            self._cache_batch_schedules(
                batch_schedule,
                now=now,
                read_generation=batch_schedule_generation,
            )
        return payload

    async def async_plan_mowing_preference_update(
        self,
        *,
        map_index: int,
        area_id: int | None,
        changes: Mapping[str, Any],
        execute: bool,
        confirm_write: bool,
    ) -> dict[str, Any]:
        """Serialize full-payload mowing preference reads and writes."""
        async with self._preference_write_lock:
            result = await self.client.async_plan_app_mowing_preference_update(
                map_index=map_index,
                area_id=area_id,
                changes=changes,
                execute=execute,
                confirm_write=confirm_write,
            )
            self.last_preference_write_result = result
            if execute:
                self.batch_device_data_refreshed_at = None
                try:
                    await self.async_refresh_batch_device_data(
                        force=True,
                        source="mowing_preference_write",
                    )
                    await self.async_request_refresh()
                finally:
                    self.async_update_listeners()
            else:
                self.async_update_listeners()
            return result

    async def _async_fetch_batch_device_data(
        self,
        *,
        force: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], int]:
        """Fetch batch schedule, settings, and OTA payloads in parallel."""
        cached_schedule = None if force else self._fresh_batch_schedule()
        if cached_schedule is None:
            schedule_generation = self._begin_schedule_read()
            map_index_hint = self._schedule_map_index_hint()
            schedule, preferences, ota = await asyncio.gather(
                self._async_get_shared_batch_schedules(
                    map_index_hint=map_index_hint,
                    force=force,
                ),
                self.client.async_get_batch_mowing_preferences(
                    include_raw=False,
                    map_index_hints=_app_map_index_hints(self.app_maps),
                ),
                self.client.async_get_batch_ota_info(include_raw=False),
            )
            if self._schedule_map_index_hint() != map_index_hint:
                self._invalidate_schedule_map_hint()
                schedule = _discard_stale_batch_schedule(schedule)
            return schedule, preferences, ota, schedule_generation

        batch_mowing_preferences, batch_ota_info = await asyncio.gather(
            self.client.async_get_batch_mowing_preferences(
                include_raw=False,
                map_index_hints=_app_map_index_hints(self.app_maps),
            ),
            self.client.async_get_batch_ota_info(include_raw=False),
        )
        return (
            cached_schedule,
            batch_mowing_preferences,
            batch_ota_info,
            getattr(self, "_published_schedule_read_generation", 0),
        )

    def _schedule_map_index_hint(self) -> int | None:
        """Return the safest available writable-slot hint for batch decoding."""
        map_index_hint = getattr(self, "selected_map_index", None)
        if map_index_hint is None:
            map_index_hint = active_map_index(getattr(self, "app_maps", None))
        known_map_indices = _app_map_index_hints(getattr(self, "app_maps", None))
        if map_index_hint is None and len(known_map_indices) == 1:
            map_index_hint = known_map_indices[0]
        return map_index_hint

    async def _async_get_shared_batch_schedules(
        self,
        *,
        map_index_hint: int | None,
        force: bool,
    ) -> dict[str, Any]:
        """Coalesce aligned schedule and batch-metadata cloud reads."""
        discover_map_index = map_index_hint is not None
        key = (map_index_hint, discover_map_index)
        loop = asyncio.get_running_loop()
        task = getattr(self, "_batch_schedule_read_task", None)
        completed_at = getattr(self, "_batch_schedule_read_completed_at", None)
        reusable_result = (
            task is not None
            and task.done()
            and not force
            and completed_at is not None
            and loop.time() - completed_at <= BATCH_SCHEDULE_RESULT_COALESCE_SECONDS
        )
        if (
            task is None
            or getattr(self, "_batch_schedule_read_key", None) != key
            or (task.done() and not reusable_result)
        ):
            options: dict[str, Any] = {
                "include_raw": False,
                "map_index_hint": map_index_hint,
            }
            if not discover_map_index:
                # App-map discovery already ran in this metadata cycle. Batch
                # decoding can safely retain an explicit unknown slot.
                options["discover_map_index"] = False
            task = asyncio.create_task(
                self.client.async_get_batch_schedules(**options)
            )
            self._batch_schedule_read_task = task
            self._batch_schedule_read_key = key
            self._batch_schedule_read_completed_at = None

            def record_completion(completed: asyncio.Task[dict[str, Any]]) -> None:
                if getattr(self, "_batch_schedule_read_task", None) is completed:
                    self._batch_schedule_read_completed_at = loop.time()

            task.add_done_callback(record_completion)
        return await asyncio.shield(task)

    def _fresh_batch_schedule(self) -> dict[str, Any] | None:
        """Return the recent shared schedule cache when batch-sourced."""
        refreshed_at = self.schedules_refreshed_at
        if (
            not isinstance(self.schedules, dict)
            or self.schedules.get("source")
            not in {
                "batch_device_data_schedule",
                "app_action_schedule_with_batch_refresh",
            }
            or refreshed_at is None
            or datetime.now(UTC) - refreshed_at >= SCHEDULE_REFRESH_INTERVAL
        ):
            return None
        return self.schedules

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

        previous_map_indices = set(
            _app_map_index_hints(getattr(self, "app_maps", None))
        )
        previous_map_hints_authoritative = _app_map_hints_are_authoritative(
            getattr(self, "app_maps", None),
            refresh_succeeded=getattr(
                self,
                "app_maps_refresh_succeeded",
                False,
            ),
        )
        try:
            app_maps = await self.client.async_get_app_maps(
                include_payload=True,
                include_objects=False,
                include_object_urls=False,
            )
        except Exception as err:  # noqa: BLE001 - best-effort extra metadata
            _LOGGER.debug("Failed to refresh app maps: %s", err)
            self.app_maps_refresh_succeeded = False
            return self.app_maps

        payload = dict(app_maps)
        payload.setdefault("captured_at", now.isoformat())
        payload["source"] = source
        self.app_maps = payload
        self.app_maps_refreshed_at = now
        self.app_maps_refresh_succeeded = True
        current_idx = active_map_index(payload)
        known_map_indices = set(_app_map_index_hints(payload))
        map_hints_authoritative = _app_map_hints_are_authoritative(
            payload,
            refresh_succeeded=True,
        )
        if (
            map_hints_authoritative
            and (
                not previous_map_hints_authoritative
                or known_map_indices != previous_map_indices
            )
        ):
            self._invalidate_schedule_map_hint()
        if current_idx is not None:
            if self.selected_map_index != current_idx:
                self._invalidate_schedule_map_hint()
            if (
                self.selected_map_index is not None
                and self.selected_map_index != current_idx
            ):
                self.selected_contour_id = None
                self.selected_zone_id = None
                self.selected_spot_id = None
                self.selected_maintenance_point_id = None
            self.selected_map_index = current_idx
        elif (
            self.selected_map_index is not None
            and map_hints_authoritative
            and self.selected_map_index not in known_map_indices
        ):
            self._invalidate_schedule_map_hint()
            self.selected_map_index = None
            self.selected_contour_id = None
            self.selected_zone_id = None
            self.selected_spot_id = None
            self.selected_maintenance_point_id = None
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
        if self.selected_map_index != map_index:
            self._invalidate_schedule_map_hint()
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

    def _invalidate_schedule_map_hint(self) -> None:
        """Expire schedule selection tied to the previous active map."""
        self.schedules_refreshed_at = None
        if isinstance(getattr(self, "schedules", None), dict):
            self.schedules["active_selection_available"] = False

    async def async_shutdown(self) -> None:
        """Disconnect client resources."""
        self._shutting_down = True
        await self._async_shutdown_connectivity_recovery()
        self._client_update_pending = False
        self.client.set_update_callback(None)
        task = self._client_update_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if await self._async_drain_metadata_for_shutdown():
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


def _app_map_hints_are_authoritative(
    app_maps: Mapping[str, Any] | None,
    *,
    refresh_succeeded: bool,
) -> bool:
    """Return whether an empty map list is a confirmed, current result."""
    if not refresh_succeeded or not isinstance(app_maps, Mapping):
        return False
    maps = app_maps.get("maps")
    return (
        app_maps.get("map_list_valid") is True
        and isinstance(maps, Sequence)
        and not isinstance(maps, str | bytes | bytearray)
    )


def _schedule_expected_indices(
    existing: Mapping[str, Any] | None,
    incoming: Mapping[str, Any],
    known_map_indices: Sequence[int],
    *,
    map_hints_authoritative: bool,
) -> list[int]:
    """Return authoritative or conservatively discovered schedule slot ids."""
    if map_hints_authoritative:
        return [-1, *known_map_indices]

    indices = [-1, *known_map_indices]
    for payload in (existing, incoming):
        schedules = payload.get("schedules") if isinstance(payload, Mapping) else None
        if not isinstance(schedules, Sequence) or isinstance(
            schedules,
            str | bytes | bytearray,
        ):
            continue
        for schedule in schedules:
            index = schedule.get("idx") if isinstance(schedule, Mapping) else None
            if (
                isinstance(index, int)
                and not isinstance(index, bool)
                and index >= 0
                and index not in indices
            ):
                indices.append(index)
    return indices


def _usable_schedule_indices(payload: Mapping[str, Any]) -> list[int]:
    """Return writable slots read authoritatively in one action payload."""
    schedules = payload.get("schedules")
    if not isinstance(schedules, Sequence):
        return []
    return [
        index
        for schedule in schedules
        if isinstance(schedule, Mapping)
        and schedule_entry_has_usable_data(schedule)
        and isinstance((index := schedule.get("idx")), int)
        and not isinstance(index, bool)
    ]


def _schedule_payload_has_active_selection(
    payload: Mapping[str, Any] | None,
) -> bool:
    """Return whether the cache identifies the effective schedule version."""
    if not isinstance(payload, Mapping):
        return False
    current_task = payload.get("current_task")
    if isinstance(current_task, Mapping):
        version = current_task.get("version")
        if isinstance(version, int) and not isinstance(version, bool):
            return True
    version = payload.get("active_schedule_version")
    return isinstance(version, int) and not isinstance(version, bool)


def _discard_stale_batch_schedule(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a non-publishable batch result after its map hint changed."""
    normalized = dict(payload)
    normalized["available"] = False
    normalized["schedules"] = []
    normalized.pop("active_schedule_version", None)
    normalized.pop("active_schedule_index", None)
    normalized.pop("current_task", None)
    errors = payload.get("errors")
    normalized["errors"] = [
        *(
            errors
            if isinstance(errors, Sequence)
            and not isinstance(errors, str | bytes | bytearray)
            else []
        ),
        {
            "stage": "schedule",
            "error": "active map changed during batch read",
        },
    ]
    return normalized


def _schedule_write_version(result: Mapping[str, Any]) -> int | None:
    """Return the writable version confirmed by a schedule write."""
    version = result.get("version")
    return (
        version
        if isinstance(version, int) and not isinstance(version, bool)
        else None
    )
