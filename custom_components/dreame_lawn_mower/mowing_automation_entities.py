"""Local permission and enable entities for the UI-configured automation."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.util import dt as dt_util

from .entity import DreameLawnMowerEntity


class MowingPermissionSensor(DreameLawnMowerEntity, BinarySensorEntity):
    """Expose evaluated garden permission without writing mower settings."""

    _attr_name = "Mowing permitted"
    _attr_icon = "mdi:sprout"
    _attr_available = True
    _unrecorded_attributes = frozenset({"evaluated_at"})
    entity_description = BinarySensorEntityDescription(key="mowing_permission")

    def __init__(self, coordinator: Any, runtime: Any) -> None:
        super().__init__(coordinator)
        self.runtime = runtime
        self._attr_unique_id = f"{self._descriptor.unique_id}_mowing_permission"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.runtime.bind("permission", self)

    @property
    def is_on(self) -> bool:
        return bool(self.runtime.permitted)

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "reason": self.runtime.reason,
            "evaluated_at": dt_util.utcnow().isoformat(),
        }


class MowingAutomationSwitch(DreameLawnMowerEntity, SwitchEntity):
    """Enable HA scheduling; turning off revokes pending starts and recovery."""

    _attr_name = "Mowing automation"
    _attr_icon = "mdi:calendar-check"
    entity_description = SwitchEntityDescription(key="mowing_automation")

    def __init__(self, coordinator: Any, runtime: Any) -> None:
        super().__init__(coordinator)
        self.runtime = runtime
        self._attr_unique_id = f"{self._descriptor.unique_id}_mowing_automation"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.runtime.bind("enabled", self)

    @property
    def is_on(self) -> bool:
        return bool(self.runtime.enabled)

    @property
    def available(self) -> bool:
        return True

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.runtime.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.runtime.async_set_enabled(False)
