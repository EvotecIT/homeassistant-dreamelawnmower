"""Buttons for Dreame lawn mower."""

from __future__ import annotations

import json
import logging

from homeassistant.components import persistent_notification
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DreameLawnMowerCoordinator
from .debug import build_debug_payload
from .entity import DreameLawnMowerEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up mower buttons."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DreameLawnMowerCaptureDebugSnapshotButton(coordinator)])


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
        _LOGGER.warning(
            "Captured Dreame lawn mower debug snapshot for %s: %s",
            self.coordinator.client.descriptor.title,
            json.dumps(payload, sort_keys=True),
        )
        persistent_notification.async_create(
            self.coordinator.hass,
            (
                "Captured a sanitized Dreame lawn mower debug snapshot. "
                "Check the Home Assistant logs for the JSON payload or use "
                "Download diagnostics on this config entry."
            ),
            title="Dreame Lawn Mower Debug Snapshot",
            notification_id=(
                f"{DOMAIN}_{self.coordinator.entry.entry_id}_debug_snapshot"
            ),
        )
