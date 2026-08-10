"""Select entities for current-map Dreame mower controls."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_MAP_ROTATION,
    CONF_MAP_ROTATIONS,
    DEFAULT_MAP_ROTATION,
    DOMAIN,
    MAP_ROTATION_OPTIONS,
)
from .control_options import (
    MOWING_ACTION_EDGE,
    MOWING_ACTION_LABELS,
    MOWING_ACTION_SPOT,
    MOWING_ACTION_ZONE,
    contour_label,
    current_contour_entries,
    current_maintenance_point_entries,
    current_map_index,
    current_spot_entries,
    current_zone_entries,
    map_entries,
    map_label,
    mowing_action_label,
    spot_label,
    zone_label,
)
from .coordinator import DreameLawnMowerCoordinator
from .device_settings_control import device_settings_section, rain_delay_label
from .dreame_lawn_mower_client.client import (
    VOICE_LANGUAGE_INDEX_TO_LABEL,
    VOICE_LANGUAGE_LABEL_TO_INDEX,
    VOICE_LANGUAGE_LABELS,
)
from .dreame_lawn_mower_client.device_settings import (
    RAIN_DELAY_MAX_HOURS,
    RAIN_DELAY_MIN_HOURS,
)
from .entity import DreameLawnMowerEntity
from .mowing_preference_control import (
    PREFERENCE_MODE_OPTIONS,
    async_update_selected_mowing_preference,
    selected_map_preference_mode,
)
from .preference_select import (
    PREFERENCE_SELECTS,
    DreameLawnMowerPreferenceSelect,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up current-map mower selects."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DreameLawnMowerVoiceLanguageSelect(coordinator),
            DreameLawnMowerRainDelaySelect(coordinator),
            DreameLawnMowerMapSelect(coordinator),
            DreameLawnMowerSelectedMapRotationSelect(coordinator),
            DreameLawnMowerMowingActionSelect(coordinator),
            DreameLawnMowerSelectedMapPreferenceModeSelect(coordinator),
            *(
                DreameLawnMowerPreferenceSelect(coordinator, description)
                for description in PREFERENCE_SELECTS
            ),
            DreameLawnMowerMaintenancePointSelect(coordinator),
            DreameLawnMowerEdgeSelect(coordinator),
            DreameLawnMowerZoneSelect(coordinator),
            DreameLawnMowerSpotSelect(coordinator),
        ]
    )


class DreameLawnMowerSelectEntity(DreameLawnMowerEntity, SelectEntity):
    """Shared base class for current-map selector entities."""


class DreameLawnMowerRainDelaySelect(DreameLawnMowerSelectEntity):
    """Choose how long the mower waits after rain before resuming."""

    _attr_name = "After-Rain Delay"
    _attr_icon = "mdi:weather-rainy"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_rain_delay"
        self._hours_by_label = {
            rain_delay_label(hours): hours
            for hours in range(RAIN_DELAY_MIN_HOURS, RAIN_DELAY_MAX_HOURS + 1)
        }

    @property
    def available(self) -> bool:
        settings = device_settings_section(self.coordinator.device_settings)
        return bool(
            self.coordinator.data is not None
            and settings
            and settings.get("rain_settings_available")
        )

    @property
    def options(self) -> list[str]:
        return list(self._hours_by_label)

    @property
    def current_option(self) -> str | None:
        settings = device_settings_section(self.coordinator.device_settings)
        if settings is None:
            return None
        delay = settings.get("rain_protection_duration_hours")
        return rain_delay_label(int(delay)) if delay is not None else None

    async def async_select_option(self, option: str) -> None:
        delay = self._hours_by_label.get(option)
        if delay is None:
            raise ValueError(f"Unknown after-rain delay option: {option}")
        await self.coordinator.async_set_rain_protection(delay_hours=delay)


class DreameLawnMowerVoiceLanguageSelect(DreameLawnMowerSelectEntity):
    """Choose the mower voice prompt language."""

    _attr_name = "Voice Language"
    _attr_icon = "mdi:translate"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_voice_language"

    @property
    def available(self) -> bool:
        """Return whether cached voice settings are available."""
        return (
            self.coordinator.data is not None
            and _voice_settings_section(self.coordinator.voice_settings) is not None
        )

    @property
    def options(self) -> list[str]:
        """Return the known mower voice languages."""
        return list(VOICE_LANGUAGE_LABELS)

    @property
    def current_option(self) -> str | None:
        """Return the selected mower voice language label."""
        section = _voice_settings_section(self.coordinator.voice_settings)
        if section is None:
            return None
        value = section.get("voice_language_index")
        return VOICE_LANGUAGE_INDEX_TO_LABEL.get(value)

    async def async_select_option(self, option: str) -> None:
        """Persist the selected mower voice language."""
        if option not in VOICE_LANGUAGE_LABEL_TO_INDEX:
            raise ValueError(f"Unknown voice language option: {option}")
        await self.coordinator.client.async_set_voice_language(
            VOICE_LANGUAGE_LABEL_TO_INDEX[option]
        )
        await self.coordinator.async_refresh_voice_settings(force=True)
        self.coordinator.async_update_listeners()


class DreameLawnMowerMapSelect(DreameLawnMowerSelectEntity):
    """Choose the mower's active app map."""

    _attr_name = "Map"
    _attr_icon = "mdi:map-search-outline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_map"

    @property
    def available(self) -> bool:
        """Return whether app-map metadata is available."""
        return bool(
            self.coordinator.data is not None
            and map_entries(
                self.coordinator.app_maps,
                self.coordinator.batch_device_data,
            )
        )

    @property
    def options(self) -> list[str]:
        """Return the known app-map labels."""
        return [
            entry["label"]
            for entry in map_entries(
                self.coordinator.app_maps,
                self.coordinator.batch_device_data,
            )
        ]

    @property
    def current_option(self) -> str | None:
        """Return the selected app-map label."""
        selected_map_index = current_map_index(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
            selected_map_index=self.coordinator.selected_map_index,
        )
        for entry in map_entries(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        ):
            if entry["map_index"] == selected_map_index:
                return entry["label"]
        if selected_map_index >= 0:
            return map_label(selected_map_index)
        return None

    async def async_select_option(self, option: str) -> None:
        """Switch the mower and all map-scoped controls to the selected map."""
        for entry in map_entries(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        ):
            if entry["label"] == option:
                await self.coordinator.async_switch_current_map(entry["map_index"])
                return
        raise ValueError(f"Unknown map option: {option}")


class DreameLawnMowerSelectedMapRotationSelect(DreameLawnMowerSelectEntity):
    """Choose display rotation for the map currently selected on the mower."""

    _attr_name = "Selected Map Display Rotation"
    _attr_icon = "mdi:screen-rotation"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_selected_map_rotation"

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None and self._map_index is not None

    @property
    def options(self) -> list[str]:
        return list(MAP_ROTATION_OPTIONS.values())

    @property
    def current_option(self) -> str | None:
        map_index = self._map_index
        if map_index is None:
            return None
        rotations = self.coordinator.entry.options.get(CONF_MAP_ROTATIONS, {})
        fallback = self.coordinator.entry.options.get(
            CONF_MAP_ROTATION,
            DEFAULT_MAP_ROTATION,
        )
        value = (
            rotations.get(str(map_index), fallback)
            if isinstance(rotations, dict)
            else fallback
        )
        return MAP_ROTATION_OPTIONS.get(value, MAP_ROTATION_OPTIONS[0])

    async def async_select_option(self, option: str) -> None:
        map_index = self._map_index
        if map_index is None:
            raise ValueError("No mower map is selected.")
        rotation = next(
            (value for value, label in MAP_ROTATION_OPTIONS.items() if label == option),
            None,
        )
        if rotation is None:
            raise ValueError(f"Unknown rotation option: {option}")
        rotations = dict(self.coordinator.entry.options.get(CONF_MAP_ROTATIONS, {}))
        rotations[str(map_index)] = rotation
        options = dict(self.coordinator.entry.options)
        options[CONF_MAP_ROTATIONS] = rotations
        self.coordinator.hass.config_entries.async_update_entry(
            self.coordinator.entry,
            options=options,
        )
        self.coordinator.async_update_listeners()

    @property
    def _map_index(self) -> int | None:
        value = current_map_index(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
            selected_map_index=self.coordinator.selected_map_index,
        )
        return value if value >= 0 else None


class DreameLawnMowerMowingActionSelect(DreameLawnMowerSelectEntity):
    """Choose how the main start button should begin mowing."""

    _attr_name = "Mowing Action"
    _attr_icon = "mdi:play-box-multiple-outline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_mowing_action"

    @property
    def options(self) -> list[str]:
        """Return the available mowing actions."""
        return list(MOWING_ACTION_LABELS.values())

    @property
    def current_option(self) -> str:
        """Return the currently selected mowing action."""
        return mowing_action_label(self.coordinator.selected_mowing_action)

    async def async_select_option(self, option: str) -> None:
        """Persist the selected mowing action in coordinator state."""
        for key, label in MOWING_ACTION_LABELS.items():
            if label == option:
                self.coordinator.selected_mowing_action = key
                self.coordinator.async_update_listeners()
                return
        raise ValueError(f"Unknown mowing action option: {option}")


class DreameLawnMowerSelectedMapPreferenceModeSelect(DreameLawnMowerSelectEntity):
    """Choose whether the selected map uses global or custom zone preferences."""

    _attr_name = "Selected Map Preference Mode"
    _attr_icon = "mdi:tune-variant"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_map_preference_mode"
        )

    @property
    def available(self) -> bool:
        """Return whether selected-map preference metadata is available."""
        return self.coordinator.data is not None and self.current_option is not None

    @property
    def options(self) -> list[str]:
        """Return supported mower preference modes."""
        return list(PREFERENCE_MODE_OPTIONS)

    @property
    def current_option(self) -> str | None:
        """Return the selected map preference mode."""
        return selected_map_preference_mode(self.coordinator)

    async def async_select_option(self, option: str) -> None:
        """Persist the selected map preference mode through the PREP path."""
        if option not in PREFERENCE_MODE_OPTIONS:
            raise ValueError(f"Unknown preference mode option: {option}")
        await async_update_selected_mowing_preference(
            self.coordinator,
            changes={"preference_mode": option.casefold()},
        )


class DreameLawnMowerMaintenancePointSelect(DreameLawnMowerSelectEntity):
    """Choose a configured maintenance point for the action button."""

    _attr_name = "Maintenance Point"
    _attr_icon = "mdi:map-marker-wrench"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_maintenance_point"

    def _entries(self) -> list[dict[str, Any]]:
        return current_maintenance_point_entries(
            getattr(self.coordinator, "vector_map_details", None),
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
            selected_map_index=self.coordinator.selected_map_index,
        )

    @property
    def available(self) -> bool:
        """Return whether the selected map contains maintenance points."""
        return self.coordinator.data is not None and bool(self._entries())

    @property
    def options(self) -> list[str]:
        """Return configured maintenance-point labels."""
        return [entry["label"] for entry in self._entries()]

    @property
    def current_option(self) -> str | None:
        """Return the selected maintenance-point label."""
        entries = self._entries()
        selected = self.coordinator.selected_maintenance_point_id
        for entry in entries:
            if entry["point_id"] == selected:
                return entry["label"]
        return entries[0]["label"] if entries else None

    async def async_select_option(self, option: str) -> None:
        """Store the selected maintenance point in coordinator state."""
        for entry in self._entries():
            if entry["label"] == option:
                self.coordinator.selected_maintenance_point_id = entry["point_id"]
                self.coordinator.async_update_listeners()
                return
        raise ValueError(f"Unknown maintenance point option: {option}")


class DreameLawnMowerEdgeSelect(DreameLawnMowerSelectEntity):
    """Choose a current-map contour to use with the start action."""

    _attr_name = "Edge"
    _attr_icon = "mdi:vector-polyline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_edge"

    @property
    def available(self) -> bool:
        """Return whether the current map has selectable contours."""
        return bool(
            self.coordinator.data is not None
            and current_contour_entries(
                getattr(self.coordinator, "vector_map_details", None),
                self.coordinator.app_maps,
                self.coordinator.batch_device_data,
                selected_map_index=self.coordinator.selected_map_index,
            )
        )

    @property
    def options(self) -> list[str]:
        """Return the current map's selectable contours."""
        return [
            entry["label"]
            for entry in current_contour_entries(
                getattr(self.coordinator, "vector_map_details", None),
                self.coordinator.app_maps,
                self.coordinator.batch_device_data,
                selected_map_index=self.coordinator.selected_map_index,
            )
        ]

    @property
    def current_option(self) -> str | None:
        """Return the selected contour label."""
        contour_id = self.coordinator.selected_contour_id
        if contour_id is None:
            options = current_contour_entries(
                getattr(self.coordinator, "vector_map_details", None),
                self.coordinator.app_maps,
                self.coordinator.batch_device_data,
                selected_map_index=self.coordinator.selected_map_index,
            )
            return options[0]["label"] if options else None
        return contour_label(contour_id)

    async def async_select_option(self, option: str) -> None:
        """Persist the selected contour in coordinator state."""
        for entry in current_contour_entries(
            getattr(self.coordinator, "vector_map_details", None),
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
            selected_map_index=self.coordinator.selected_map_index,
        ):
            if entry["label"] == option:
                self.coordinator.selected_contour_id = entry["contour_id"]
                self.coordinator.selected_mowing_action = MOWING_ACTION_EDGE
                self.coordinator.async_update_listeners()
                return
        raise ValueError(f"Unknown edge option: {option}")


class DreameLawnMowerZoneSelect(DreameLawnMowerSelectEntity):
    """Choose a current-map zone to use with the start action."""

    _attr_name = "Zone"
    _attr_icon = "mdi:texture-box"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_zone"

    @property
    def available(self) -> bool:
        """Return whether the current map has selectable zones."""
        return bool(
            self.coordinator.data is not None
            and current_zone_entries(
                self.coordinator.batch_device_data,
                self.coordinator.app_maps,
                self.coordinator.vector_map_details,
                selected_map_index=self.coordinator.selected_map_index,
            )
        )

    @property
    def options(self) -> list[str]:
        """Return the current map's selectable zones."""
        return [
            entry["label"]
            for entry in current_zone_entries(
                self.coordinator.batch_device_data,
                self.coordinator.app_maps,
                self.coordinator.vector_map_details,
                selected_map_index=self.coordinator.selected_map_index,
            )
        ]

    @property
    def current_option(self) -> str | None:
        """Return the selected zone label."""
        zone_id = self.coordinator.selected_zone_id
        if zone_id is None:
            options = current_zone_entries(
                self.coordinator.batch_device_data,
                self.coordinator.app_maps,
                self.coordinator.vector_map_details,
                selected_map_index=self.coordinator.selected_map_index,
            )
            return options[0]["label"] if options else None
        for entry in current_zone_entries(
            self.coordinator.batch_device_data,
            self.coordinator.app_maps,
            getattr(self.coordinator, "vector_map_details", None),
            selected_map_index=self.coordinator.selected_map_index,
        ):
            if entry["area_id"] == zone_id:
                return str(entry["label"])
        return zone_label(zone_id)

    async def async_select_option(self, option: str) -> None:
        """Persist the selected zone in coordinator state."""
        for entry in current_zone_entries(
            self.coordinator.batch_device_data,
            self.coordinator.app_maps,
            self.coordinator.vector_map_details,
            selected_map_index=self.coordinator.selected_map_index,
        ):
            if entry["label"] == option:
                self.coordinator.selected_zone_id = entry["area_id"]
                self.coordinator.selected_mowing_action = MOWING_ACTION_ZONE
                self.coordinator.async_update_listeners()
                return
        raise ValueError(f"Unknown zone option: {option}")


class DreameLawnMowerSpotSelect(DreameLawnMowerSelectEntity):
    """Choose a current-map spot area to use with the start action."""

    _attr_name = "Spot"
    _attr_icon = "mdi:map-marker-radius-outline"

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_spot"

    @property
    def available(self) -> bool:
        """Return whether the current map has selectable spots."""
        return bool(
            self.coordinator.data is not None
            and current_spot_entries(
                self.coordinator.app_maps,
                self.coordinator.batch_device_data,
                selected_map_index=self.coordinator.selected_map_index,
            )
        )

    @property
    def options(self) -> list[str]:
        """Return the current map's selectable spots."""
        return [
            entry["label"]
            for entry in current_spot_entries(
                self.coordinator.app_maps,
                self.coordinator.batch_device_data,
                selected_map_index=self.coordinator.selected_map_index,
            )
        ]

    @property
    def current_option(self) -> str | None:
        """Return the selected spot label."""
        spot_id = self.coordinator.selected_spot_id
        if spot_id is None:
            options = current_spot_entries(
                self.coordinator.app_maps,
                self.coordinator.batch_device_data,
                selected_map_index=self.coordinator.selected_map_index,
            )
            return options[0]["label"] if options else None
        return spot_label(spot_id)

    async def async_select_option(self, option: str) -> None:
        """Persist the selected spot in coordinator state."""
        for entry in current_spot_entries(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
            selected_map_index=self.coordinator.selected_map_index,
        ):
            if entry["label"] == option:
                self.coordinator.selected_spot_id = entry["spot_id"]
                self.coordinator.selected_mowing_action = MOWING_ACTION_SPOT
                self.coordinator.async_update_listeners()
                return
        raise ValueError(f"Unknown spot option: {option}")


def _voice_settings_section(value: dict[str, Any] | None) -> dict[str, Any] | None:
    section = value.get("voice_settings") if isinstance(value, dict) else None
    return section if isinstance(section, dict) and section.get("available") else None
