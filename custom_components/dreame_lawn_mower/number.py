"""Number entities for Dreame mower configuration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity
from .mowing_preference_control import (
    MOWING_HEIGHT_MAX_CM,
    MOWING_HEIGHT_MIN_CM,
    MOWING_HEIGHT_STEP_CM,
    PREFERENCE_MODE_CUSTOM,
    PREFERENCE_MODE_GLOBAL,
    async_update_selected_mowing_preference,
    mowing_height_limits,
    selected_map_global_preference_attributes,
    selected_map_mowing_height,
    selected_map_preference_mode,
    selected_zone_mowing_height,
    selected_zone_preference_attributes,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dreame mower number entities."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DreameLawnMowerVoiceVolumeNumber(coordinator),
            DreameLawnMowerSelectedMapMowingHeightNumber(coordinator),
            DreameLawnMowerSelectedZoneMowingHeightNumber(coordinator),
        ]
    )


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


class _DreameLawnMowerMowingHeightNumber(
    DreameLawnMowerEntity,
    NumberEntity,
):
    """Shared model-aware cutting-height control."""

    _attr_icon = "mdi:grass"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = MOWING_HEIGHT_MIN_CM
    _attr_native_max_value = MOWING_HEIGHT_MAX_CM
    _attr_native_step = MOWING_HEIGHT_STEP_CM
    _attr_native_unit_of_measurement = "cm"
    _attr_mode = NumberMode.SLIDER

    @property
    def native_min_value(self) -> float:
        """Return the family limit while retaining a reported outlier."""
        minimum, _ = mowing_height_limits(
            getattr(getattr(self, "_descriptor", None), "model", None)
        )
        current = self.native_value
        return min(minimum, current) if current is not None else minimum

    @property
    def native_max_value(self) -> float:
        """Return the family limit while retaining a reported outlier."""
        _, maximum = mowing_height_limits(
            getattr(getattr(self, "_descriptor", None), "model", None)
        )
        current = self.native_value
        return max(maximum, current) if current is not None else maximum

    def _validate_height(self, value: float) -> float:
        normalized = float(value)
        minimum, maximum = mowing_height_limits(
            getattr(getattr(self, "_descriptor", None), "model", None)
        )
        if normalized < minimum or normalized > maximum:
            raise HomeAssistantError(
                f"Mowing height must be between {minimum:g} and "
                f"{maximum:g} cm for this mower."
            )
        steps = round(normalized / MOWING_HEIGHT_STEP_CM)
        if abs(normalized - steps * MOWING_HEIGHT_STEP_CM) > 1e-6:
            raise HomeAssistantError(
                f"Mowing height must use {MOWING_HEIGHT_STEP_CM:g} cm steps."
            )
        return normalized


class DreameLawnMowerSelectedMapMowingHeightNumber(
    _DreameLawnMowerMowingHeightNumber,
):
    """Control the selected map's whole-lawn mowing height."""

    _attr_name = "Selected Map Mowing Height"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_map_mowing_height"
        )

    @property
    def available(self) -> bool:
        """Return whether global cutting height can currently be changed."""
        return (
            self.coordinator.data is not None
            and self.native_value is not None
            and selected_map_preference_mode(self.coordinator) == PREFERENCE_MODE_GLOBAL
        )

    @property
    def native_value(self) -> float | None:
        """Return the whole-lawn mowing height in centimeters."""
        return selected_map_mowing_height(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return selected-map scope and write availability details."""
        attributes = selected_map_global_preference_attributes(self.coordinator)
        attributes["write_available"] = self.available
        if not self.available:
            attributes["write_unavailable_reason"] = (
                "Select Global map preference mode before changing whole-lawn height."
                if selected_map_preference_mode(self.coordinator)
                != PREFERENCE_MODE_GLOBAL
                else "Selected map global mowing preference data is unavailable."
            )
        return attributes

    async def async_set_native_value(self, value: float) -> None:
        """Persist the whole-lawn cutting height through the PRE path."""
        if selected_map_preference_mode(self.coordinator) != PREFERENCE_MODE_GLOBAL:
            raise HomeAssistantError(
                "Select Global map preference mode before changing whole-lawn height."
            )
        await async_update_selected_mowing_preference(
            self.coordinator,
            changes={"mowing_height_cm": self._validate_height(value)},
            global_scope=True,
        )


class DreameLawnMowerSelectedZoneMowingHeightNumber(
    _DreameLawnMowerMowingHeightNumber,
):
    """Control the mowing height for the selected map and zone."""

    _attr_name = "Selected Zone Mowing Height"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_zone_mowing_height"
        )

    @property
    def available(self) -> bool:
        """Return whether the selected zone height can currently be changed."""
        return (
            self.coordinator.data is not None
            and self.native_value is not None
            and selected_map_preference_mode(self.coordinator) == PREFERENCE_MODE_CUSTOM
        )

    @property
    def native_value(self) -> float | None:
        """Return the selected zone mowing height in centimeters."""
        return selected_zone_mowing_height(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return selected map/zone scope and write availability details."""
        attributes = selected_zone_preference_attributes(self.coordinator)
        attributes["write_available"] = self.available
        if not self.available:
            attributes["write_unavailable_reason"] = (
                "Select Custom map preference mode before changing zone height."
                if selected_map_preference_mode(self.coordinator)
                != PREFERENCE_MODE_CUSTOM
                else "Selected zone mowing preference data is unavailable."
            )
        return attributes

    async def async_set_native_value(self, value: float) -> None:
        """Persist the selected zone mowing height through the guarded PRE path."""
        if selected_map_preference_mode(self.coordinator) != PREFERENCE_MODE_CUSTOM:
            raise HomeAssistantError(
                "Select Custom map preference mode before changing zone height."
            )
        await async_update_selected_mowing_preference(
            self.coordinator,
            changes={"mowing_height_cm": self._validate_height(value)},
        )


def _voice_settings_section(value: dict[str, Any] | None) -> dict[str, Any] | None:
    section = value.get("voice_settings") if isinstance(value, dict) else None
    return section if isinstance(section, dict) and section.get("available") else None
