"""Local permission and enable entities for the UI-configured automation."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory, UnitOfArea, UnitOfTime
from homeassistant.util import dt as dt_util

from .entity import DreameLawnMowerEntity
from .mowing_automation_config import MOWING_TARGET_OPTIONS


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


class _RotationSetting(DreameLawnMowerEntity):
    """A wizard setting that dashboards can change without a reload."""

    _attr_entity_category = EntityCategory.CONFIG
    _setting: str

    def __init__(self, coordinator: Any, runtime: Any) -> None:
        super().__init__(coordinator)
        self.runtime = runtime
        self._attr_unique_id = f"{self._descriptor.unique_id}_{self._setting}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.runtime.bind(self._binding, self)

    _binding: str

    @property
    def available(self) -> bool:
        return True


class MowingTargetSelect(_RotationSetting, SelectEntity):
    """Whole lawn, or due zones in rotation."""

    _attr_translation_key = "mowing_target"
    _attr_icon = "mdi:texture-box"
    _attr_options = MOWING_TARGET_OPTIONS
    _setting = "mowing_target"
    _binding = "target"
    entity_description = SelectEntityDescription(key="mowing_target")

    @property
    def current_option(self) -> str:
        return str(self.runtime.config["mowing_target"])

    async def async_select_option(self, option: str) -> None:
        await self.runtime.async_set_rotation(mowing_target=option)


class RotationRestDaysNumber(_RotationSetting, NumberEntity):
    """Days a zone rests after a completion (0 = once a day)."""

    _attr_translation_key = "rotation_rest_days"
    _attr_icon = "mdi:calendar-refresh"
    _attr_native_min_value = 0
    _attr_native_max_value = 30
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_mode = NumberMode.BOX
    _setting = "rotation_rest_days"
    _binding = "rest_days"
    entity_description = NumberEntityDescription(key="rotation_rest_days")

    @property
    def native_value(self) -> int:
        return int(self.runtime.config["rotation_rest_days"])

    async def async_set_native_value(self, value: float) -> None:
        await self.runtime.async_set_rotation(rotation_rest_days=int(value))


class RotationMaxAreaNumber(_RotationSetting, NumberEntity):
    """Whole zones are added while their total area fits (0 = no limit)."""

    _attr_translation_key = "rotation_max_area"
    _attr_icon = "mdi:texture-box"
    _attr_native_min_value = 0
    _attr_native_max_value = 100000
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfArea.SQUARE_METERS
    _attr_mode = NumberMode.BOX
    _setting = "rotation_max_area"
    _binding = "max_area"
    entity_description = NumberEntityDescription(key="rotation_max_area")

    @property
    def native_value(self) -> float:
        return float(self.runtime.config["rotation_max_area"])

    async def async_set_native_value(self, value: float) -> None:
        await self.runtime.async_set_rotation(rotation_max_area=value)
