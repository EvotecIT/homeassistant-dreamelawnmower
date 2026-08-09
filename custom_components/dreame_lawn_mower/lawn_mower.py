"""Lawn mower platform for Dreame lawn mower."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import voluptuous as vol
from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    async_get_current_platform,
)

from .const import (
    ACTIVITY_DOCKED,
    ACTIVITY_ERROR,
    ACTIVITY_IDLE,
    ACTIVITY_MOWING,
    ACTIVITY_PAUSED,
    ACTIVITY_RETURNING,
    DOMAIN,
)
from .control_options import (
    MOWING_ACTION_EDGE,
    MOWING_ACTION_SPOT,
    MOWING_ACTION_ZONE,
    contour_label,
    current_contour_entries,
    current_map_index,
    current_spot_entries,
    current_zone_entries,
    map_entries,
    mowing_action_label,
)
from .coordinator import DreameLawnMowerCoordinator
from .dreame_lawn_mower_client.feature_capabilities import (
    resolved_feature_capabilities,
)
from .dreame_lawn_mower_client.mowing_preferences import (
    normalize_mowing_preference_mode,
)
from .entity import DreameLawnMowerEntity
from .mowing_preference_control import async_update_selected_mowing_preference
from .runtime_cache import begin_runtime_mission_session
from .services import (
    ATTR_CONFIRM_PREFERENCE_WRITE,
    ATTR_EXECUTE,
    ATTR_PREFERENCE_MODE,
    ATTR_ZONE_ID,
    MOWING_PREFERENCE_CHANGE_FIELDS,
    _guard_preference_write_request,
    preference_change_request,
)

ACTIVITY_MAP = {
    ACTIVITY_DOCKED: LawnMowerActivity.DOCKED,
    ACTIVITY_ERROR: LawnMowerActivity.ERROR,
    ACTIVITY_IDLE: LawnMowerActivity.DOCKED,
    ACTIVITY_MOWING: LawnMowerActivity.MOWING,
    ACTIVITY_PAUSED: LawnMowerActivity.PAUSED,
    ACTIVITY_RETURNING: LawnMowerActivity.RETURNING,
}


def _normalize_contour_ids(contour_ids: Any) -> list[list[int]]:
    """Validate and normalize contour ids to integer pairs."""
    if not isinstance(contour_ids, list) or not contour_ids:
        raise HomeAssistantError("At least one contour id pair is required.")

    normalized: list[list[int]] = []
    for contour_id in contour_ids:
        if not isinstance(contour_id, (list, tuple)) or len(contour_id) != 2:
            raise HomeAssistantError(
                "Each contour id must be a two-item list such as [1, 0]."
            )

        pair: list[int] = []
        for value in contour_id:
            if isinstance(value, bool):
                raise HomeAssistantError("Contour id values must be integers.")
            try:
                pair.append(int(value))
            except (TypeError, ValueError) as err:
                raise HomeAssistantError("Contour id values must be integers.") from err
        normalized.append(pair)

    return normalized


def _validate_contour_ids(contour_ids: Any) -> list[list[int]]:
    """Wrap contour-id validation for voluptuous entity-service schemas."""
    try:
        return _normalize_contour_ids(contour_ids)
    except HomeAssistantError as err:
        raise vol.Invalid(str(err)) from err


def _validate_preference_mode(preference_mode: Any) -> int:
    """Validate a map preference mode label or integer."""
    try:
        return normalize_mowing_preference_mode(preference_mode)
    except ValueError as err:
        raise vol.Invalid(str(err)) from err


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the mower entity."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    platform = async_get_current_platform()
    platform.async_register_entity_service(
        "start_zone_mowing",
        {vol.Required("zone_ids"): [vol.Coerce(int)]},
        "async_start_zone_mowing",
    )
    platform.async_register_entity_service(
        "start_spot_mowing",
        {vol.Required("spot_ids"): [vol.Coerce(int)]},
        "async_start_spot_mowing",
    )
    platform.async_register_entity_service(
        "start_edge_mowing",
        {vol.Required("contour_ids"): _validate_contour_ids},
        "async_start_edge_mowing",
    )
    platform.async_register_entity_service(
        "switch_current_map",
        {vol.Required("map_index"): vol.Coerce(int)},
        "async_switch_current_map",
    )
    platform.async_register_entity_service(
        "plan_map_preference_mode_update",
        {
            vol.Required(ATTR_PREFERENCE_MODE): _validate_preference_mode,
            vol.Optional(ATTR_EXECUTE, default=False): cv.boolean,
            vol.Optional(ATTR_CONFIRM_PREFERENCE_WRITE, default=False): cv.boolean,
        },
        "async_plan_map_preference_mode_update",
    )
    platform.async_register_entity_service(
        "plan_zone_mowing_preference_update",
        {
            vol.Optional(ATTR_ZONE_ID): vol.Coerce(int),
            vol.Optional(ATTR_EXECUTE, default=False): cv.boolean,
            vol.Optional(ATTR_CONFIRM_PREFERENCE_WRITE, default=False): cv.boolean,
            **MOWING_PREFERENCE_CHANGE_FIELDS,
        },
        "async_plan_zone_mowing_preference_update",
    )
    async_add_entities([DreameLawnMower(coordinator)])


class DreameLawnMower(DreameLawnMowerEntity, LawnMowerEntity):
    """Main mower entity."""

    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
    )
    _attr_name = None

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_mower"

    @property
    def activity(self) -> LawnMowerActivity | None:
        """Return the current mower activity."""
        if self.coordinator.data is None:
            return None
        return ACTIVITY_MAP.get(
            self.coordinator.data.activity,
            LawnMowerActivity.DOCKED,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional mower attributes."""
        snapshot = self.coordinator.data
        capability_evidence = getattr(
            self.coordinator,
            "feature_capability_evidence",
            None,
        )
        observed_features, advertised_features = (
            capability_evidence()
            if capability_evidence is not None
            else (frozenset(), frozenset())
        )
        device = getattr(self.coordinator.client, "device", None)
        vector_map_details = (
            self.coordinator.vector_map_details
            if isinstance(self.coordinator.vector_map_details, dict)
            else None
        )
        zone_entries = current_zone_entries(
            self.coordinator.batch_device_data,
            self.coordinator.app_maps,
            getattr(self.coordinator, "vector_map_details", None),
            selected_map_index=self.coordinator.selected_map_index,
        )
        contour_entries = current_contour_entries(
            self.coordinator.vector_map_details,
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
            selected_map_index=self.coordinator.selected_map_index,
        )
        spot_entries = current_spot_entries(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
            selected_map_index=self.coordinator.selected_map_index,
        )
        maps = map_entries(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )
        selected_map_index = self._selected_map_index(maps)
        active_app_map_index = (
            self.coordinator.app_maps.get("current_map_index")
            if isinstance(self.coordinator.app_maps, dict)
            else None
        )
        current_vector_map = self._current_vector_map_entry(
            vector_map_details,
            active_app_map_index,
        )
        current_zone_preference = self._selected_zone_preference(
            selected_map_index,
            zone_entries,
        )
        current_map_preference = self._selected_map_preference(
            selected_map_index,
            maps,
        )
        available_vector_map_names = self._available_vector_map_names(
            vector_map_details
        )
        return {
            "connection_degraded": getattr(
                self.coordinator,
                "connection_degraded",
                False,
            ),
            "connection_failure_count": (
                getattr(self.coordinator, "connection_failure_count", 0)
            ),
            "connection_last_success_at": (
                self.coordinator.connection_last_success_at.isoformat()
                if getattr(
                    self.coordinator,
                    "connection_last_success_at",
                    None,
                )
                is not None
                else None
            ),
            "connection_degraded_since": (
                self.coordinator.connection_degraded_since.isoformat()
                if getattr(
                    self.coordinator,
                    "connection_degraded_since",
                    None,
                )
                is not None
                else None
            ),
            "connection_recovered_at": (
                self.coordinator.connection_recovered_at.isoformat()
                if getattr(
                    self.coordinator,
                    "connection_recovered_at",
                    None,
                )
                is not None
                else None
            ),
            "connection_retry_after_seconds": (
                getattr(
                    self.coordinator,
                    "connection_retry_after_seconds",
                    None,
                )
            ),
            "connection_last_error": getattr(
                self.coordinator,
                "connection_last_error",
                None,
            ),
            "state": snapshot.mower_state,
            "state_name": snapshot.mower_state_name,
            "task_status": snapshot.mowing_task_status,
            "task_status_name": snapshot.mowing_task_status_name,
            "task_status_source": getattr(snapshot, "task_status_source", None),
            "mowing_session_active": getattr(
                snapshot,
                "mowing_session_active",
                None,
            ),
            "task_resumable": getattr(snapshot, "task_resumable", None),
            "unknown_property_count": len(
                getattr(device, "unknown_properties", {}) or {}
            ),
            "realtime_property_count": len(
                getattr(device, "realtime_properties", {}) or {}
            ),
            "last_realtime_method": snapshot.last_realtime_method,
            "error_code": snapshot.error_code,
            "error_name": snapshot.error_name,
            "error_text": snapshot.error_text,
            "error_display": snapshot.error_display,
            "error_source": getattr(snapshot, "error_source", None),
            "status_notice_code": getattr(snapshot, "status_notice_code", None),
            "status_notice_name": getattr(snapshot, "status_notice_name", None),
            "status_notice_display": getattr(
                snapshot,
                "status_notice_display",
                None,
            ),
            "status_notice_tier": getattr(
                snapshot,
                "status_notice_tier",
                None,
            ),
            "status_notice_source": getattr(
                snapshot,
                "status_notice_source",
                None,
            ),
            "raw_error_code": getattr(snapshot, "raw_error_code", None),
            "realtime_error_code": getattr(snapshot, "realtime_error_code", None),
            "mowing_mode": snapshot.mowing_mode,
            "mowing_mode_name": snapshot.mowing_mode_name,
            "mowed_area": snapshot.mowed_area,
            "mowing_time": snapshot.mowing_time,
            "active_segment_count": getattr(snapshot, "active_segment_count", None),
            "current_zone_id": getattr(snapshot, "current_zone_id", None),
            "current_zone_name": getattr(snapshot, "current_zone_name", None),
            "child_lock": snapshot.child_lock,
            "online": snapshot.online,
            "device_connected": getattr(snapshot, "device_connected", None),
            "cloud_connected": getattr(snapshot, "cloud_connected", None),
            "charging": snapshot.charging,
            "raw_charging": snapshot.raw_charging,
            "started": snapshot.started,
            "raw_started": snapshot.raw_started,
            "returning": snapshot.returning,
            "raw_returning": snapshot.raw_returning,
            "docked": snapshot.docked,
            "raw_docked": snapshot.raw_docked,
            "mapping_available": snapshot.mapping_available,
            "scheduled_mow": snapshot.scheduled_mow,
            "shortcut_task": snapshot.shortcut_task,
            "serial_number": snapshot.serial_number,
            "cloud_update_time": snapshot.cloud_update_time,
            "capabilities": list(snapshot.capabilities),
            "feature_capabilities": resolved_feature_capabilities(
                snapshot,
                descriptor=getattr(self, "_descriptor", None),
                observed=observed_features,
                advertised=advertised_features,
            ),
            "selected_mowing_action": self.coordinator.selected_mowing_action,
            "selected_mowing_action_label": mowing_action_label(
                self.coordinator.selected_mowing_action
            ),
            "selected_map_index": selected_map_index,
            "selected_map_label": self._selected_map_label(maps, selected_map_index),
            "selected_map_matches_active_app_map": (
                selected_map_index == active_app_map_index
                if isinstance(selected_map_index, int)
                and isinstance(active_app_map_index, int)
                else None
            ),
            "selected_map_preference_available": current_map_preference is not None,
            "selected_map_preference": current_map_preference,
            "selected_map_preference_mode": (
                current_map_preference.get("mode_name")
                if isinstance(current_map_preference, dict)
                else None
            ),
            "selected_map_preference_area_count": (
                current_map_preference.get("area_count")
                if isinstance(current_map_preference, dict)
                else None
            ),
            "selected_map_preference_count": (
                current_map_preference.get("preference_count")
                if isinstance(current_map_preference, dict)
                else None
            ),
            "selected_contour_id": self._selected_contour_id(contour_entries),
            "selected_contour_label": self._selected_contour_label(contour_entries),
            "selected_zone_id": self._selected_zone_id(zone_entries),
            "selected_zone_preference_available": current_zone_preference is not None,
            "selected_zone_preference": current_zone_preference,
            "selected_spot_id": self._selected_spot_id(spot_entries),
            "available_map_indices": [entry["map_index"] for entry in maps],
            "available_map_labels": [entry["label"] for entry in maps],
            "available_contour_ids": [
                list(entry["contour_id"]) for entry in contour_entries
            ],
            "available_contour_labels": [entry["label"] for entry in contour_entries],
            "available_zone_ids": [entry["area_id"] for entry in zone_entries],
            "available_spot_ids": [entry["spot_id"] for entry in spot_entries],
            "app_current_map_index": active_app_map_index,
            "app_current_map_label": self._selected_map_label(
                maps,
                active_app_map_index,
            ),
            "app_map_count": (
                self.coordinator.app_maps.get("created_map_count")
                if isinstance(self.coordinator.app_maps, dict)
                else None
            ),
            "app_map_slot_count": (
                self.coordinator.app_maps.get("map_count")
                if isinstance(self.coordinator.app_maps, dict)
                else None
            ),
            "vector_map_available": (
                vector_map_details.get("available")
                if vector_map_details is not None
                else None
            ),
            "available_vector_map_count": self._available_vector_map_count(
                vector_map_details
            ),
            "available_vector_map_names": available_vector_map_names,
            "current_vector_map_id": current_vector_map.get("map_id")
            if current_vector_map
            else None,
            "current_vector_map_name": self._current_vector_map_name(
                current_vector_map
            ),
            "current_vector_map_contour_count": current_vector_map.get("contour_count")
            if current_vector_map
            else None,
            "current_vector_map_has_live_path": current_vector_map.get("has_live_path")
            if current_vector_map
            else None,
            "current_vector_map_mow_path_point_count": current_vector_map.get(
                "mow_path_point_count"
            )
            if current_vector_map
            else None,
            # Deprecated public aliases. Entity registry keys and vendor
            # protocol fields remain unchanged while automations migrate.
            "raw_state": snapshot.state,
            "raw_state_name": snapshot.state_name,
            "raw_task_status": snapshot.task_status,
            "raw_task_status_name": snapshot.task_status_name,
            "cleaning_mode": snapshot.cleaning_mode,
            "cleaning_mode_name": snapshot.cleaning_mode_name,
            "cleaned_area": snapshot.cleaned_area,
            "cleaning_time": snapshot.cleaning_time,
            "scheduled_clean": snapshot.scheduled_clean,
        }

    async def async_start_mowing(self) -> None:
        """Start or resume mowing."""
        action = self.coordinator.selected_mowing_action
        new_session = False
        if action == MOWING_ACTION_EDGE:
            self._ensure_selected_map_matches_active()
            contour_entries = current_contour_entries(
                self.coordinator.vector_map_details,
                self.coordinator.app_maps,
                self.coordinator.batch_device_data,
                selected_map_index=self.coordinator.selected_map_index,
            )
            contour_id = self._selected_contour_id(contour_entries)
            if contour_id is None:
                raise HomeAssistantError(
                    "No current-map edge contour is available to start."
                )
            await self.coordinator.client.async_start_edge_mowing([list(contour_id)])
            new_session = True
        elif action == MOWING_ACTION_ZONE:
            self._ensure_selected_map_matches_active()
            zone_entries = current_zone_entries(
                self.coordinator.batch_device_data,
                self.coordinator.app_maps,
                getattr(self.coordinator, "vector_map_details", None),
                selected_map_index=self.coordinator.selected_map_index,
            )
            zone_id = self._selected_zone_id(zone_entries)
            if zone_id is None:
                raise HomeAssistantError("No current-map zone is available to start.")
            await self.coordinator.client.async_start_zone_mowing([zone_id])
            new_session = True
        elif action == MOWING_ACTION_SPOT:
            self._ensure_selected_map_matches_active()
            spot_entries = current_spot_entries(
                self.coordinator.app_maps,
                self.coordinator.batch_device_data,
                selected_map_index=self.coordinator.selected_map_index,
            )
            spot_id = self._selected_spot_id(spot_entries)
            if spot_id is None:
                raise HomeAssistantError("No current-map spot is available to start.")
            await self.coordinator.client.async_start_spot_mowing([spot_id])
            new_session = True
        else:
            new_session = await self.coordinator.client.async_start_mowing()
        if new_session is True:
            begin_runtime_mission_session(
                getattr(self.coordinator, "runtime_telemetry_cache", None)
            )
        await self.coordinator.async_request_refresh()

    async def async_start_zone_mowing(self, zone_ids: list[int]) -> None:
        """Start mowing for one or more explicit current-map zones."""
        if not zone_ids:
            raise HomeAssistantError("At least one zone id is required.")
        self._ensure_selected_map_matches_active()
        zone_entries = current_zone_entries(
            self.coordinator.batch_device_data,
            self.coordinator.app_maps,
            getattr(self.coordinator, "vector_map_details", None),
            selected_map_index=self.coordinator.selected_map_index,
        )
        normalized = [int(zone_id) for zone_id in zone_ids]
        self._ensure_requested_ids_available(
            normalized,
            available_ids=[int(entry["area_id"]) for entry in zone_entries],
            target_name="Zone",
        )
        await self.coordinator.client.async_start_zone_mowing(normalized)
        begin_runtime_mission_session(
            getattr(self.coordinator, "runtime_telemetry_cache", None)
        )
        await self.coordinator.async_request_refresh()

    async def async_start_spot_mowing(self, spot_ids: list[int]) -> None:
        """Start mowing for one or more explicit current-map spot ids."""
        if not spot_ids:
            raise HomeAssistantError("At least one spot id is required.")
        self._ensure_selected_map_matches_active()
        spot_entries = current_spot_entries(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
            selected_map_index=self.coordinator.selected_map_index,
        )
        normalized = [int(spot_id) for spot_id in spot_ids]
        self._ensure_requested_ids_available(
            normalized,
            available_ids=[int(entry["spot_id"]) for entry in spot_entries],
            target_name="Spot",
        )
        await self.coordinator.client.async_start_spot_mowing(normalized)
        begin_runtime_mission_session(
            getattr(self.coordinator, "runtime_telemetry_cache", None)
        )
        await self.coordinator.async_request_refresh()

    async def async_start_edge_mowing(self, contour_ids: list[list[int]]) -> None:
        """Start mowing for one or more explicit current-map edge contour ids."""
        normalized = _normalize_contour_ids(contour_ids)
        self._ensure_selected_map_matches_active()
        contour_entries = current_contour_entries(
            getattr(self.coordinator, "vector_map_details", None),
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
            selected_map_index=self.coordinator.selected_map_index,
        )
        available = {
            tuple(entry["contour_id"])
            for entry in contour_entries
            if isinstance(entry.get("contour_id"), tuple)
        }
        missing = [
            tuple(contour_id)
            for contour_id in normalized
            if tuple(contour_id) not in available
        ]
        if missing:
            missing_text = ", ".join(str(contour_id) for contour_id in missing)
            available_text = ", ".join(
                str(contour_id) for contour_id in sorted(available)
            )
            raise HomeAssistantError(
                f"Edge contour {missing_text} is not available on the active map. "
                f"Available contour ids: {available_text or 'none'}."
            )
        await self.coordinator.client.async_start_edge_mowing(normalized)
        begin_runtime_mission_session(
            getattr(self.coordinator, "runtime_telemetry_cache", None)
        )
        await self.coordinator.async_request_refresh()

    async def async_switch_current_map(self, map_index: int) -> None:
        """Switch the mower's active app map."""
        normalized_map_index = int(map_index)
        self._ensure_known_map_index(normalized_map_index)
        await self.coordinator.async_switch_current_map(normalized_map_index)

    async def async_plan_zone_mowing_preference_update(
        self,
        zone_id: int | None = None,
        execute: bool = False,
        confirm_preference_write: bool = False,
        **changes: Any,
    ) -> None:
        """Build or execute a scoped preference update for the selected map/zone."""
        requested_changes = preference_change_request(changes)
        _guard_preference_write_request(
            SimpleNamespace(
                data={
                    ATTR_EXECUTE: execute,
                    ATTR_CONFIRM_PREFERENCE_WRITE: confirm_preference_write,
                }
            )
        )
        await async_update_selected_mowing_preference(
            self.coordinator,
            changes=requested_changes,
            zone_id=zone_id,
            execute=execute,
            confirm_write=confirm_preference_write,
        )

    async def async_plan_map_preference_mode_update(
        self,
        preference_mode: Any,
        execute: bool = False,
        confirm_preference_write: bool = False,
    ) -> None:
        """Build or execute a map-scoped preference-mode update."""
        await self.async_plan_zone_mowing_preference_update(
            preference_mode=normalize_mowing_preference_mode(preference_mode),
            execute=execute,
            confirm_preference_write=confirm_preference_write,
        )

    async def async_pause(self) -> None:
        """Pause mowing."""
        await self.coordinator.client.async_pause()
        await self.coordinator.async_request_refresh()

    async def async_dock(self) -> None:
        """Return to base."""
        await self.coordinator.client.async_dock()
        await self.coordinator.async_request_refresh()

    def _selected_zone_id(self, zone_entries: list[dict[str, object]]) -> int | None:
        """Return a valid selected zone id or the first available option."""
        selected = self.coordinator.selected_zone_id
        if selected is not None and any(
            entry["area_id"] == selected for entry in zone_entries
        ):
            return selected
        if zone_entries:
            return int(zone_entries[0]["area_id"])
        return None

    def _selected_contour_id(
        self,
        contour_entries: list[dict[str, object]],
    ) -> tuple[int, int] | None:
        """Return a valid selected contour id or the first available option."""
        selected = self.coordinator.selected_contour_id
        if selected is not None and any(
            entry["contour_id"] == selected for entry in contour_entries
        ):
            return selected
        if contour_entries:
            contour_id = contour_entries[0]["contour_id"]
            if isinstance(contour_id, tuple):
                return contour_id
        return None

    def _selected_contour_label(
        self,
        contour_entries: list[dict[str, object]],
    ) -> str | None:
        """Return the selected contour label when one is available."""
        contour_id = self._selected_contour_id(contour_entries)
        if contour_id is None:
            return None
        return contour_label(contour_id)

    def _selected_spot_id(self, spot_entries: list[dict[str, object]]) -> int | None:
        """Return a valid selected spot id or the first available option."""
        selected = self.coordinator.selected_spot_id
        if selected is not None and any(
            entry["spot_id"] == selected for entry in spot_entries
        ):
            return selected
        if spot_entries:
            return int(spot_entries[0]["spot_id"])
        return None

    def _selected_map_index(self, maps: list[dict[str, object]]) -> int | None:
        """Return the selected app-map index or the active fallback."""
        if not maps:
            return None
        return current_map_index(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
            selected_map_index=self.coordinator.selected_map_index,
        )

    def _selected_map_label(
        self,
        maps: list[dict[str, object]],
        selected_map_index: int | None,
    ) -> str | None:
        """Return the display label for the selected app map."""
        if selected_map_index is None:
            return None
        for entry in maps:
            if entry["map_index"] == selected_map_index:
                return str(entry["label"])
        return None

    def _ensure_selected_map_matches_active(self) -> None:
        """Block scoped zone/spot starts when the mower is on another app map."""
        selected_map_index = self._selected_map_index(
            map_entries(
                self.coordinator.app_maps,
                self.coordinator.batch_device_data,
            )
        )
        active_map_index = (
            self.coordinator.app_maps.get("current_map_index")
            if isinstance(self.coordinator.app_maps, dict)
            else None
        )
        if (
            isinstance(selected_map_index, int)
            and isinstance(active_map_index, int)
            and selected_map_index != active_map_index
        ):
            raise HomeAssistantError(
                "Selected map does not match the active mower map. "
                "Switch the active map in the Dreame app first."
            )

    def _ensure_known_map_index(self, map_index: int) -> None:
        """Validate that a requested map index exists in cached app-map metadata."""
        known_maps = map_entries(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        )
        if any(entry["map_index"] == map_index for entry in known_maps):
            return
        raise HomeAssistantError(f"Map index {map_index} is not available.")

    @staticmethod
    def _ensure_requested_ids_available(
        requested_ids: list[int],
        *,
        available_ids: list[int],
        target_name: str,
    ) -> None:
        """Reject explicit scoped ids that are absent from current-map metadata."""
        missing = [
            target_id for target_id in requested_ids if target_id not in available_ids
        ]
        if not missing:
            return
        missing_text = ", ".join(f"#{target_id}" for target_id in missing)
        available_text = ", ".join(f"#{target_id}" for target_id in available_ids)
        if not available_text:
            available_text = "none"
        subject = target_name if len(missing) == 1 else f"{target_name}s"
        verb = "is" if len(missing) == 1 else "are"
        raise HomeAssistantError(
            f"{subject} {missing_text} {verb} not available on the active map. "
            f"Available {target_name.lower()} ids: {available_text}."
        )

    def _resolve_zone_id(
        self,
        zone_entries: list[dict[str, object]],
        *,
        zone_id: int | None = None,
    ) -> int:
        """Return a valid scoped zone id for a preference plan request."""
        if zone_id is not None:
            target_zone_id = int(zone_id)
            if any(entry["area_id"] == target_zone_id for entry in zone_entries):
                return target_zone_id
            raise HomeAssistantError(
                f"Zone #{target_zone_id} is not available on the selected map."
            )

        selected_zone_id = self._selected_zone_id(zone_entries)
        if selected_zone_id is not None:
            return selected_zone_id
        raise HomeAssistantError(
            "No current-map zone is available for mowing preference planning."
        )

    def _zone_label_for_id(
        self,
        zone_entries: list[dict[str, object]],
        zone_id: int,
    ) -> str | None:
        """Return a stable label for a zone id within the current scope."""
        for entry in zone_entries:
            if entry["area_id"] == zone_id:
                return str(entry["label"])
        return None

    def _selected_zone_preference(
        self,
        selected_map_index: int | None,
        zone_entries: list[dict[str, object]],
    ) -> dict[str, Any] | None:
        """Return a compact preference summary for the selected/current zone."""
        zone_id = self._selected_zone_id(zone_entries)
        if zone_id is None or selected_map_index is None:
            return None

        preference_map = self._batch_preference_map_entry(selected_map_index)
        if preference_map is None:
            return None

        preferences = preference_map.get("preferences")
        if not isinstance(preferences, list):
            return None

        for preference in preferences:
            if not isinstance(preference, dict):
                continue
            if preference.get("area_id") != zone_id:
                continue
            summary = {
                "map_index": selected_map_index,
                "area_id": zone_id,
                "label": self._zone_label_for_id(zone_entries, zone_id),
                "mode": preference_map.get("mode"),
                "mode_name": preference_map.get("mode_name"),
                "reported_version": preference.get("reported_version"),
                "mowing_height_cm": preference.get("mowing_height_cm"),
                "efficient_mode_name": preference.get("efficient_mode_name"),
                "mowing_direction_mode_name": preference.get(
                    "mowing_direction_mode_name"
                ),
                "mowing_direction_method_name": preference.get(
                    "mowing_direction_method_name"
                ),
                "mowing_direction_degrees": preference.get("mowing_direction_degrees"),
                "edge_mowing_auto": preference.get("edge_mowing_auto"),
                "edge_mowing_walk_mode_name": preference.get(
                    "edge_mowing_walk_mode_name"
                ),
                "turning_method_name": preference.get("turning_method_name"),
                "edge_mowing_obstacle_avoidance": preference.get(
                    "edge_mowing_obstacle_avoidance"
                ),
                "cutter_position_name": preference.get("cutter_position_name"),
                "edge_mowing_num": preference.get("edge_mowing_num"),
                "obstacle_avoidance_enabled": preference.get(
                    "obstacle_avoidance_enabled"
                ),
                "obstacle_avoidance_height_cm": preference.get(
                    "obstacle_avoidance_height_cm"
                ),
                "obstacle_avoidance_distance_cm": preference.get(
                    "obstacle_avoidance_distance_cm"
                ),
                "obstacle_avoidance_ai_classes": preference.get(
                    "obstacle_avoidance_ai_classes"
                ),
                "edge_mowing_safe": preference.get("edge_mowing_safe"),
                "edge_cutting_attachment": preference.get(
                    "edge_cutting_attachment"
                ),
            }
            return {
                key: value
                for key, value in summary.items()
                if value not in (None, [], {})
            }
        return None

    def _selected_map_preference(
        self,
        selected_map_index: int | None,
        maps: list[dict[str, object]],
    ) -> dict[str, Any] | None:
        """Return a compact preference summary for the selected/current map."""
        if selected_map_index is None:
            return None

        preference_map = self._batch_preference_map_entry(selected_map_index)
        if preference_map is None:
            return None

        preferences = preference_map.get("preferences")
        summary = {
            "map_index": selected_map_index,
            "label": self._selected_map_label(maps, selected_map_index),
            "mode": preference_map.get("mode"),
            "mode_name": preference_map.get("mode_name"),
            "area_count": preference_map.get("area_count"),
            "preference_count": len(preferences)
            if isinstance(preferences, list)
            else None,
        }
        return {
            key: value for key, value in summary.items() if value not in (None, [], {})
        }

    def _batch_preference_map_entry(
        self,
        map_index: int,
    ) -> dict[str, Any] | None:
        """Return the decoded batch preference entry for one app map index."""
        batch_data = (
            self.coordinator.batch_device_data
            if isinstance(self.coordinator.batch_device_data, dict)
            else None
        )
        preferences = (
            batch_data.get("batch_mowing_preferences")
            if batch_data is not None
            else None
        )
        maps = preferences.get("maps") if isinstance(preferences, dict) else None
        if not isinstance(maps, list):
            return None
        for entry in maps:
            if isinstance(entry, dict) and entry.get("idx") == map_index:
                return entry
        return None

    def _current_vector_map_entry(
        self,
        vector_map_details: dict[str, Any] | None,
        active_app_map_index: int | None,
    ) -> dict[str, Any] | None:
        """Return the vector-map entry for the mower's active app map."""
        if not isinstance(vector_map_details, dict):
            return None

        maps = vector_map_details.get("maps")
        if isinstance(maps, list) and isinstance(active_app_map_index, int):
            for entry in maps:
                if (
                    isinstance(entry, dict)
                    and entry.get("map_index") == active_app_map_index
                ):
                    return entry

        if (
            isinstance(active_app_map_index, int)
            and vector_map_details.get("map_index") == active_app_map_index
        ):
            return vector_map_details
        return None

    def _available_vector_map_count(
        self,
        vector_map_details: dict[str, Any] | None,
    ) -> int | None:
        """Return the known vector-map count when available."""
        if not isinstance(vector_map_details, dict):
            return None

        count = vector_map_details.get("available_map_count")
        if isinstance(count, int):
            return count

        maps = vector_map_details.get("maps")
        if isinstance(maps, list):
            return len([entry for entry in maps if isinstance(entry, dict)])
        return None

    def _available_vector_map_names(
        self,
        vector_map_details: dict[str, Any] | None,
    ) -> list[str]:
        """Return compact names for all cached vector maps."""
        if not isinstance(vector_map_details, dict):
            return []

        names = vector_map_details.get("map_names")
        if isinstance(names, list):
            return [
                name.strip() for name in names if isinstance(name, str) and name.strip()
            ]

        result: list[str] = []
        maps = vector_map_details.get("maps")
        if not isinstance(maps, list):
            return result

        for entry in maps:
            if not isinstance(entry, dict):
                continue
            name = self._current_vector_map_name(entry)
            if name is not None:
                result.append(name)
        return result

    def _current_vector_map_name(
        self,
        current_vector_map: dict[str, Any] | None,
    ) -> str | None:
        """Return a stable display name for the current vector map."""
        if not isinstance(current_vector_map, dict):
            return None

        name = current_vector_map.get("map_name")
        if isinstance(name, str) and name.strip():
            return name.strip()

        map_id = current_vector_map.get("map_id")
        if isinstance(map_id, int):
            return f"Map {map_id}"
        return None
