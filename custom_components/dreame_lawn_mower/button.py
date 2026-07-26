"""Buttons for Dreame lawn mower."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from homeassistant.components import persistent_notification
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .calendar import schedule_calendar_selection
from .const import DOMAIN
from .control_options import current_maintenance_point_entries
from .coordinator import DreameLawnMowerCoordinator
from .debug import build_debug_payload, sanitize_debug_data
from .dreame_lawn_mower_client.maintenance import MAINTENANCE_ITEMS, MaintenanceItem
from .entity import DreameLawnMowerEntity
from .manual_control import remote_control_block_reason
from .reporting import build_maintenance_point_diagnostics
from .task_status_probe import TASK_STATUS_PROBE_KEYS, task_status_probe_payload

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up mower buttons."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DreameLawnMowerDockWithoutStoppingButton(coordinator),
            DreameLawnMowerCaptureDebugSnapshotButton(coordinator),
            DreameLawnMowerCaptureOperationSnapshotButton(coordinator),
            DreameLawnMowerCaptureMapProbeButton(coordinator),
            DreameLawnMowerCaptureBatchDeviceDataProbeButton(coordinator),
            DreameLawnMowerCaptureTaskStatusProbeButton(coordinator),
            DreameLawnMowerCaptureScheduleProbeButton(coordinator),
            DreameLawnMowerCapturePreferenceProbeButton(coordinator),
            DreameLawnMowerCaptureWeatherProbeButton(coordinator),
            DreameLawnMowerGoToMaintenancePointButton(coordinator),
        ]
        + [
            DreameLawnMowerResetMaintenanceButton(coordinator, item)
            for item in MAINTENANCE_ITEMS
        ]
    )


class DreameLawnMowerDockWithoutStoppingButton(
    DreameLawnMowerEntity,
    ButtonEntity,
):
    """Return the mower to base without ending its current session."""

    _attr_name = "Dock Without Ending Session"
    _attr_icon = "mdi:home-battery"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_dock_without_stopping"
        )

    async def async_press(self) -> None:
        """Dock directly so the current task remains available to resume."""
        await self.coordinator.client.async_dock_without_stopping()
        await self.coordinator.async_request_refresh()


class DreameLawnMowerGoToMaintenancePointButton(
    DreameLawnMowerEntity,
    ButtonEntity,
):
    """Drive the idle mower to a configured maintenance point."""

    _attr_name = "Go to Maintenance Point"
    _attr_icon = "mdi:robot-mower-outline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_go_to_maintenance_point"
        )

    def _point_id(self) -> int | None:
        entries = current_maintenance_point_entries(
            getattr(self.coordinator, "vector_map_details", None),
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
            selected_map_index=self.coordinator.selected_map_index,
        )
        selected = self.coordinator.selected_maintenance_point_id
        for entry in entries:
            if entry["point_id"] == selected:
                return int(entry["point_id"])
        if selected is not None:
            return None
        return int(entries[0]["point_id"]) if entries else None

    def _movement_block_reason(self) -> str | None:
        """Return why maintenance-point movement is unsafe right now."""
        snapshot = self.coordinator.data
        block_reason = remote_control_block_reason(snapshot)
        if block_reason is not None:
            return block_reason
        activity = str(getattr(snapshot, "activity", "") or "").casefold()
        if activity not in {"idle", "docked"}:
            return (
                "The mower must be idle or docked before going to a "
                "maintenance point."
            )
        return None

    @property
    def available(self) -> bool:
        """Allow the movement command only from a known idle state."""
        return (
            self._point_id() is not None and self._movement_block_reason() is None
        )

    async def async_press(self) -> None:
        """Drive to the selected mower-configured maintenance point."""
        await self.coordinator.async_request_refresh()
        block_reason = self._movement_block_reason()
        if block_reason is not None:
            raise ValueError(block_reason)
        app_maps_refreshed_at = self.coordinator.app_maps_refreshed_at
        vector_map_refreshed_at = self.coordinator.vector_map_details_refreshed_at
        await self.coordinator.async_refresh_app_maps(
            force=True,
            source="app_maps_maintenance_point_command",
        )
        await self.coordinator.async_refresh_vector_map_details(
            force=True,
            source="vector_map_maintenance_point_command",
        )
        if (
            self.coordinator.app_maps_refreshed_at == app_maps_refreshed_at
            or self.coordinator.vector_map_details_refreshed_at
            == vector_map_refreshed_at
        ):
            raise ValueError(
                "Fresh map metadata is required before moving to a maintenance point."
            )
        point_id = self._point_id()
        if point_id is None:
            raise ValueError(
                "No maintenance point is configured on the selected mower map."
            )
        await self.coordinator.async_request_refresh()
        block_reason = self._movement_block_reason()
        if block_reason is not None:
            raise ValueError(block_reason)
        await self.coordinator.client.async_go_to_maintenance_point(point_id)
        await self.coordinator.async_request_refresh()


class DreameLawnMowerCaptureDebugSnapshotButton(
    DreameLawnMowerEntity,
    ButtonEntity,
):
    """Capture and log a structured debug snapshot."""

    _attr_name = "Capture Debug Snapshot"
    _attr_icon = "mdi:file-document-refresh-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_capture_debug_snapshot"

    async def async_press(self) -> None:
        """Refresh the mower and emit a sanitized debug payload."""
        await self.coordinator.async_request_refresh()
        payload = build_debug_payload(
            entry_data=self.coordinator.entry.data,
            snapshot=self.coordinator.data,
            device=self.coordinator.client._device,
        )
        _LOGGER.info(
            "Captured Dreame lawn mower debug snapshot for %s: %s",
            self.coordinator.client.descriptor.title,
            json.dumps(payload, sort_keys=True),
        )
        persistent_notification.async_create(
            self.coordinator.hass,
            (
                "Captured a sanitized Dreame lawn mower debug snapshot. Use "
                "Download diagnostics on this config entry, or enable info "
                "logging for this integration to view the JSON payload."
            ),
            title="Dreame Lawn Mower Debug Snapshot",
            notification_id=(
                f"{DOMAIN}_{self.coordinator.entry.entry_id}_debug_snapshot"
            ),
        )


class DreameLawnMowerCaptureOperationSnapshotButton(
    DreameLawnMowerEntity,
    ButtonEntity,
):
    """Capture and log a compact field-test operation snapshot."""

    _attr_name = "Capture Operation Snapshot"
    _attr_icon = "mdi:clipboard-pulse-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_capture_operation_snapshot"
        )

    async def async_press(self) -> None:
        """Capture grouped read-only operation evidence and log it."""
        payload = await self.coordinator.client.async_capture_operation_snapshot(
            label="home_assistant_button",
            include_map_view=True,
            include_firmware=True,
        )
        payload = sanitize_debug_data(payload)
        _LOGGER.info(
            "Captured Dreame lawn mower operation snapshot for %s: %s",
            self.coordinator.client.descriptor.title,
            json.dumps(payload, sort_keys=True),
        )
        await self.coordinator.async_request_refresh()
        persistent_notification.async_create(
            self.coordinator.hass,
            (
                "Captured a sanitized Dreame lawn mower operation snapshot. "
                "Use Download diagnostics, or enable info logging for this "
                "integration to view grouped state, realtime, map, firmware, "
                "and remote-control evidence."
            ),
            title="Dreame Lawn Mower Operation Snapshot",
            notification_id=(
                f"{DOMAIN}_{self.coordinator.entry.entry_id}_operation_snapshot"
            ),
        )


class DreameLawnMowerCaptureMapProbeButton(
    DreameLawnMowerEntity,
    ButtonEntity,
):
    """Capture and log map-source diagnostics."""

    _attr_name = "Capture Map Probe"
    _attr_icon = "mdi:map-search-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_capture_map_probe"

    async def async_press(self) -> None:
        """Probe known read-only map sources and log the structured result."""
        await self.coordinator.async_request_refresh()
        payload = await self.coordinator.client.async_probe_map_sources()
        self.coordinator.last_map_probe_result = build_maintenance_point_diagnostics(
            self.coordinator,
            map_probe_payload=payload,
            captured_at=datetime.now(UTC).isoformat(),
        )
        _LOGGER.info(
            "Captured Dreame lawn mower map probe for %s: %s",
            self.coordinator.client.descriptor.title,
            json.dumps(payload, sort_keys=True),
        )
        persistent_notification.async_create(
            self.coordinator.hass,
            (
                "Captured a Dreame lawn mower map probe. Privacy-safe "
                "maintenance-point evidence is now included in downloaded "
                "diagnostics; enable info logging for the full JSON payload."
            ),
            title="Dreame Lawn Mower Map Probe",
            notification_id=(
                f"{DOMAIN}_{self.coordinator.entry.entry_id}_map_probe"
            ),
        )


class DreameLawnMowerCaptureScheduleProbeButton(
    DreameLawnMowerEntity,
    ButtonEntity,
):
    """Capture and log read-only app schedule diagnostics."""

    _attr_name = "Capture Schedule Probe"
    _attr_icon = "mdi:calendar-search"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_capture_schedule_probe"

    async def async_press(self) -> None:
        """Probe read-only app schedules and log the structured result."""
        payload = await self.coordinator.client.async_get_app_schedules(
            include_raw=False,
        )
        payload = schedule_probe_payload(payload)
        payload.setdefault("captured_at", datetime.now(UTC).isoformat())
        self.coordinator.last_schedule_probe_result = payload
        self.coordinator.async_update_listeners()
        _LOGGER.info(
            "Captured Dreame lawn mower schedule probe for %s: %s",
            self.coordinator.client.descriptor.title,
            json.dumps(payload, sort_keys=True),
        )
        persistent_notification.async_create(
            self.coordinator.hass,
            (
                "Captured a Dreame lawn mower schedule probe. Enable info "
                "logging for this integration to view decoded schedule JSON, "
                "or enable the Last Schedule Probe diagnostic sensor."
            ),
            title="Dreame Lawn Mower Schedule Probe",
            notification_id=(
                f"{DOMAIN}_{self.coordinator.entry.entry_id}_schedule_probe"
            ),
        )


class DreameLawnMowerCaptureBatchDeviceDataProbeButton(
    DreameLawnMowerEntity,
    ButtonEntity,
):
    """Capture and log read-only batch device-data diagnostics."""

    _attr_name = "Capture Batch Device Data Probe"
    _attr_icon = "mdi:database-search"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_capture_batch_device_data_probe"
        )

    async def async_press(self) -> None:
        """Probe read-only batch device data and log the structured result."""
        payload = await self.coordinator.async_refresh_batch_device_data(
            force=True,
            source="batch_device_data_probe",
        )
        if payload is None:
            payload = {
                "captured_at": datetime.now(UTC).isoformat(),
                "source": "batch_device_data_probe",
                "batch_schedule": {"available": False, "errors": ["refresh_failed"]},
                "batch_mowing_preferences": {
                    "available": False,
                    "errors": ["refresh_failed"],
                },
                "batch_ota_info": {"available": False, "errors": ["refresh_failed"]},
            }
        self.coordinator.last_batch_device_data_probe_result = payload
        self.coordinator.async_update_listeners()
        _LOGGER.info(
            "Captured Dreame lawn mower batch device data probe for %s: %s",
            self.coordinator.client.descriptor.title,
            json.dumps(payload, sort_keys=True),
        )
        persistent_notification.async_create(
            self.coordinator.hass,
            (
                "Captured a Dreame lawn mower batch device data probe. Enable "
                "info logging for this integration to view decoded batch "
                "schedule, settings, and OTA JSON, or enable the Last Batch "
                "Device Data Probe diagnostic sensor."
            ),
            title="Dreame Lawn Mower Batch Device Data Probe",
            notification_id=(
                f"{DOMAIN}_{self.coordinator.entry.entry_id}_batch_device_data_probe"
            ),
        )


class DreameLawnMowerCaptureTaskStatusProbeButton(
    DreameLawnMowerEntity,
    ButtonEntity,
):
    """Capture and log read-only app task/status diagnostics."""

    _attr_name = "Capture Task Status Probe"
    _attr_icon = "mdi:clipboard-pulse-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_capture_task_status_probe"
        )

    async def async_press(self) -> None:
        """Probe read-only app task/status properties and log the result."""
        scan = await self.coordinator.client.async_scan_cloud_properties(
            keys=TASK_STATUS_PROBE_KEYS,
            only_values=True,
        )
        payload = task_status_probe_payload(
            scan,
            captured_at=datetime.now(UTC).isoformat(),
            model=self._descriptor.model,
        )
        self.coordinator.last_task_status_probe_result = payload
        self.coordinator.async_update_listeners()
        _LOGGER.info(
            "Captured Dreame lawn mower task status probe for %s: %s",
            self.coordinator.client.descriptor.title,
            json.dumps(payload, sort_keys=True),
        )
        persistent_notification.async_create(
            self.coordinator.hass,
            (
                "Captured a Dreame lawn mower task status probe. Enable info "
                "logging for this integration to view app state/task JSON, "
                "or enable the Last Task Status Probe diagnostic sensor."
            ),
            title="Dreame Lawn Mower Task Status Probe",
            notification_id=(
                f"{DOMAIN}_{self.coordinator.entry.entry_id}_task_status_probe"
            ),
        )


class DreameLawnMowerCapturePreferenceProbeButton(
    DreameLawnMowerEntity,
    ButtonEntity,
):
    """Capture and log read-only app mowing preference diagnostics."""

    _attr_name = "Capture Preference Probe"
    _attr_icon = "mdi:tune-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_capture_preference_probe"
        )

    async def async_press(self) -> None:
        """Probe read-only app mowing preferences and log the result."""
        payload = await self.coordinator.client.async_get_mowing_preferences(
            include_raw=False,
        )
        payload.setdefault("captured_at", datetime.now(UTC).isoformat())
        self.coordinator.last_preference_probe_result = payload
        self.coordinator.async_update_listeners()
        _LOGGER.info(
            "Captured Dreame lawn mower preference probe for %s: %s",
            self.coordinator.client.descriptor.title,
            json.dumps(payload, sort_keys=True),
        )
        persistent_notification.async_create(
            self.coordinator.hass,
            (
                "Captured a Dreame lawn mower preference probe. Enable info "
                "logging for this integration to view decoded preference JSON, "
                "or enable the Last Preference Probe diagnostic sensor."
            ),
            title="Dreame Lawn Mower Preference Probe",
            notification_id=(
                f"{DOMAIN}_{self.coordinator.entry.entry_id}_preference_probe"
            ),
        )


class DreameLawnMowerCaptureWeatherProbeButton(
    DreameLawnMowerEntity,
    ButtonEntity,
):
    """Capture and log read-only weather/rain protection diagnostics."""

    _attr_name = "Capture Weather Probe"
    _attr_icon = "mdi:weather-pouring"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_capture_weather_probe"

    async def async_press(self) -> None:
        """Probe read-only weather/rain protection settings and log the result."""
        payload = await self.coordinator.client.async_get_weather_protection(
            include_raw=False,
        )
        payload.setdefault("captured_at", datetime.now(UTC).isoformat())
        self.coordinator.last_weather_probe_result = payload
        self.coordinator.weather_protection = dict(payload)
        self.coordinator.weather_protection_refreshed_at = datetime.now(UTC)
        self.coordinator.async_update_listeners()
        _LOGGER.info(
            "Captured Dreame lawn mower weather probe for %s: %s",
            self.coordinator.client.descriptor.title,
            json.dumps(payload, sort_keys=True),
        )
        persistent_notification.async_create(
            self.coordinator.hass,
            (
                "Captured a Dreame lawn mower weather probe. Enable info "
                "logging for this integration to view decoded weather/rain "
                "protection JSON, or enable the Last Weather Probe diagnostic "
                "sensor."
            ),
            title="Dreame Lawn Mower Weather Probe",
            notification_id=(
                f"{DOMAIN}_{self.coordinator.entry.entry_id}_weather_probe"
            ),
        )


class DreameLawnMowerResetMaintenanceButton(
    DreameLawnMowerEntity,
    ButtonEntity,
):
    """Reset a live CMS maintenance counter."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        item: MaintenanceItem,
    ) -> None:
        super().__init__(coordinator)
        self._item = item
        self._attr_name = f"Reset {item.name} Maintenance"
        self._attr_icon = item.icon
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_reset_maintenance_{item.key}"
        )

    async def async_press(self) -> None:
        """Reset the selected maintenance counter and refresh cached status."""
        result = await self.coordinator.client.async_plan_maintenance_reset(
            item=self._item.key,
            execute=True,
            confirm_write=True,
        )
        self.coordinator.last_maintenance_reset_result = result
        await self.coordinator.async_refresh_maintenance_status(
            force=True,
            source="maintenance_reset_button",
        )
        self.coordinator.async_update_listeners()
        _LOGGER.info(
            "Reset Dreame lawn mower maintenance counter for %s: %s",
            self._item.name,
            json.dumps(result, sort_keys=True),
        )
        title, message = _maintenance_reset_button_notification(result)
        persistent_notification.async_create(
            self.coordinator.hass,
            message,
            title=title,
            notification_id=(
                f"{DOMAIN}_{self.coordinator.entry.entry_id}_reset_maintenance_"
                f"{self._item.key}"
            ),
        )


def _maintenance_reset_button_notification(
    result: dict[str, object],
) -> tuple[str, str]:
    """Return title and message for a maintenance reset button press."""
    item_name = result.get("item_name") or result.get("item") or "maintenance item"
    previous = result.get("previous_item")
    updated = result.get("updated_item")
    previous_counter = (
        previous.get("used_minutes") if isinstance(previous, dict) else None
    )
    updated_counter = updated.get("used_minutes") if isinstance(updated, dict) else None
    return (
        "Dreame Lawn Mower Maintenance Reset",
        (
            f"Reset {item_name}: counter {previous_counter} -> "
            f"{updated_counter}. Enable the Last Maintenance Reset diagnostic "
            "sensor for full CMS request and response details."
        ),
    )


def schedule_probe_payload(payload: dict[str, object]) -> dict[str, object]:
    """Return schedule probe payload enriched with calendar selection details."""
    enriched = dict(payload)
    enriched.setdefault("schedule_selection", schedule_calendar_selection(payload))
    return enriched
