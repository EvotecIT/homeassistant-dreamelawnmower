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
from .coordinator import DreameLawnMowerCoordinator
from .debug import build_debug_payload, sanitize_debug_data
from .entity import DreameLawnMowerEntity

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
            DreameLawnMowerCaptureDebugSnapshotButton(coordinator),
            DreameLawnMowerCaptureOperationSnapshotButton(coordinator),
            DreameLawnMowerCaptureMapProbeButton(coordinator),
            DreameLawnMowerCaptureScheduleProbeButton(coordinator),
        ]
    )


class DreameLawnMowerCaptureDebugSnapshotButton(
    DreameLawnMowerEntity,
    ButtonEntity,
):
    """Capture and log a structured debug snapshot."""

    _attr_name = "Capture Debug Snapshot"
    _attr_icon = "mdi:file-document-refresh-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

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
        _LOGGER.info(
            "Captured Dreame lawn mower map probe for %s: %s",
            self.coordinator.client.descriptor.title,
            json.dumps(payload, sort_keys=True),
        )
        persistent_notification.async_create(
            self.coordinator.hass,
            (
                "Captured a Dreame lawn mower map probe. Enable info logging "
                "for this integration to view the JSON payload."
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


def schedule_probe_payload(payload: dict[str, object]) -> dict[str, object]:
    """Return schedule probe payload enriched with calendar selection details."""
    enriched = dict(payload)
    enriched.setdefault("schedule_selection", schedule_calendar_selection(payload))
    return enriched
