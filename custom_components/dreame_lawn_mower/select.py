"""Select entities for current-map Dreame mower controls."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .control_options import (
    MOWING_ACTION_EDGE,
    MOWING_ACTION_LABELS,
    MOWING_ACTION_SPOT,
    MOWING_ACTION_ZONE,
    contour_label,
    current_contour_entries,
    map_entries,
    current_spot_entries,
    current_zone_entries,
    current_map_index,
    map_label,
    mowing_action_label,
    spot_label,
    zone_label,
)
from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up current-map mower selects."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DreameLawnMowerMapSelect(coordinator),
            DreameLawnMowerMowingActionSelect(coordinator),
            DreameLawnMowerEdgeSelect(coordinator),
            DreameLawnMowerZoneSelect(coordinator),
            DreameLawnMowerSpotSelect(coordinator),
        ]
    )


class DreameLawnMowerSelectEntity(DreameLawnMowerEntity, SelectEntity):
    """Shared base class for current-map selector entities."""


class DreameLawnMowerMapSelect(DreameLawnMowerSelectEntity):
    """Choose which app map the zone and spot selectors should target."""

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
        """Persist the selected app-map scope in coordinator state."""
        for entry in map_entries(
            self.coordinator.app_maps,
            self.coordinator.batch_device_data,
        ):
            if entry["label"] == option:
                self.coordinator.selected_map_index = entry["map_index"]
                self.coordinator.async_update_listeners()
                return
        raise ValueError(f"Unknown map option: {option}")


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
                self.coordinator.vector_map_details,
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
                self.coordinator.vector_map_details,
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
                self.coordinator.vector_map_details,
                self.coordinator.app_maps,
                self.coordinator.batch_device_data,
                selected_map_index=self.coordinator.selected_map_index,
            )
            return options[0]["label"] if options else None
        return contour_label(contour_id)

    async def async_select_option(self, option: str) -> None:
        """Persist the selected contour in coordinator state."""
        for entry in current_contour_entries(
            self.coordinator.vector_map_details,
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
                selected_map_index=self.coordinator.selected_map_index,
            )
            return options[0]["label"] if options else None
        return zone_label(zone_id)

    async def async_select_option(self, option: str) -> None:
        """Persist the selected zone in coordinator state."""
        for entry in current_zone_entries(
            self.coordinator.batch_device_data,
            self.coordinator.app_maps,
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
