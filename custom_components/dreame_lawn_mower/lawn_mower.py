"""Lawn mower platform for Dreame lawn mower."""

from __future__ import annotations

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ACTIVITY_DOCKED,
    ACTIVITY_ERROR,
    ACTIVITY_IDLE,
    ACTIVITY_MOWING,
    ACTIVITY_PAUSED,
    ACTIVITY_RETURNING,
    DOMAIN,
)
from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity

ACTIVITY_MAP = {
    ACTIVITY_DOCKED: LawnMowerActivity.DOCKED,
    ACTIVITY_ERROR: LawnMowerActivity.ERROR,
    ACTIVITY_IDLE: LawnMowerActivity.DOCKED,
    ACTIVITY_MOWING: LawnMowerActivity.MOWING,
    ACTIVITY_PAUSED: LawnMowerActivity.PAUSED,
    ACTIVITY_RETURNING: LawnMowerActivity.MOWING,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the mower entity."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
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
    def extra_state_attributes(self) -> dict[str, str | int | bool | None]:
        """Return additional mower attributes."""
        snapshot = self.coordinator.data
        return {
            "state": snapshot.state,
            "state_name": snapshot.state_name,
            "task_status": snapshot.task_status,
            "task_status_name": snapshot.task_status_name,
            "cleaning_mode": snapshot.cleaning_mode,
            "cleaning_mode_name": snapshot.cleaning_mode_name,
            "child_lock": snapshot.child_lock,
            "online": snapshot.online,
            "charging": snapshot.charging,
            "started": snapshot.started,
            "returning": snapshot.returning,
            "docked": snapshot.docked,
            "mapping_available": snapshot.mapping_available,
            "scheduled_clean": snapshot.scheduled_clean,
            "shortcut_task": snapshot.shortcut_task,
            "serial_number": snapshot.serial_number,
            "cloud_update_time": snapshot.cloud_update_time,
            "capabilities": list(snapshot.capabilities),
        }

    async def async_start_mowing(self) -> None:
        """Start or resume mowing."""
        await self.coordinator.client.async_start_mowing()
        await self.coordinator.async_request_refresh()

    async def async_pause(self) -> None:
        """Pause mowing."""
        await self.coordinator.client.async_pause()
        await self.coordinator.async_request_refresh()

    async def async_dock(self) -> None:
        """Return to base."""
        await self.coordinator.client.async_dock()
        await self.coordinator.async_request_refresh()
