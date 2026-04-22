"""Number entities for Dreame mower configuration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dreame mower number entities."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DreameLawnMowerVoiceVolumeNumber(coordinator)])


class DreameLawnMowerVoiceVolumeNumber(DreameLawnMowerEntity, NumberEntity):
    """Expose the mower voice volume from the app CFG payload."""

    _attr_name = "Voice Volume"
    _attr_icon = "mdi:volume-high"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_voice_volume"

    @property
    def available(self) -> bool:
        """Return whether cached voice settings are available."""
        return self.coordinator.data is not None and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        """Return the configured mower voice volume."""
        section = _voice_settings_section(self.coordinator.voice_settings)
        if section is None:
            return None
        value = section.get("volume")
        return float(value) if isinstance(value, int) else None

    async def async_set_native_value(self, value: float) -> None:
        """Persist the selected mower voice volume."""
        await self.coordinator.client.async_set_voice_volume(round(value))
        await self.coordinator.async_refresh_voice_settings(force=True)
        self.coordinator.async_update_listeners()


def _voice_settings_section(value: dict[str, Any] | None) -> dict[str, Any] | None:
    section = value.get("voice_settings") if isinstance(value, dict) else None
    return section if isinstance(section, dict) and section.get("available") else None
