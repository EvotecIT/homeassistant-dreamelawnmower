"""Switch entities for mower settings and schedule plans."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DreameLawnMowerCoordinator
from .device_settings_control import device_settings_section
from .entity import DreameLawnMowerEntity
from .preference_switch import (
    AI_CLASS_SWITCHES,
    PREFERENCE_SWITCHES,
    DreameLawnMowerPreferenceAiClassSwitch,
    DreameLawnMowerPreferenceSwitch,
)

VOICE_PROMPT_SWITCHES = (
    (
        "general_prompt_voice",
        "General Prompt Voice",
        0,
        "mdi:message-text-outline",
    ),
    (
        "working_voice",
        "Working Voice",
        1,
        "mdi:robot-mower-outline",
    ),
    (
        "special_status_voice",
        "Special Status Voice",
        2,
        "mdi:information-outline",
    ),
    (
        "fault_voice",
        "Fault Voice",
        3,
        "mdi:alert-circle-outline",
    ),
)

ANTI_THEFT_SWITCHES = (
    (
        "lift_alarm_enabled",
        "Lift Alarm",
        "mdi:alarm-light-outline",
    ),
    (
        "off_map_alarm_enabled",
        "Off-Map Alarm",
        "mdi:map-marker-alert-outline",
    ),
    (
        "real_time_location_enabled",
        "Real-Time Location",
        "mdi:crosshairs-gps",
    ),
    (
        "pin_check_before_power_off_enabled",
        "PIN Check Before Power-Off",
        "mdi:shield-key-outline",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dreame mower switch entities."""
    coordinator: DreameLawnMowerCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            *(
                DreameLawnMowerVoicePromptSwitch(
                    coordinator,
                    key=key,
                    name=name,
                    index=index,
                    icon=icon,
                )
                for key, name, index, icon in VOICE_PROMPT_SWITCHES
            ),
        ]
    )
    known_schedule_plans: set[tuple[int, int]] = set()
    known_setting_switches: set[str] = set()
    known_preference_switches: set[str] = set()

    @callback
    def async_add_schedule_switches() -> None:
        new_entities: list[DreameLawnMowerSchedulePlanSwitch] = []
        for item in schedule_plan_entries(coordinator.schedules):
            key = (item["map_index"], item["plan_id"])
            if key in known_schedule_plans:
                continue
            known_schedule_plans.add(key)
            new_entities.append(
                DreameLawnMowerSchedulePlanSwitch(
                    coordinator,
                    map_index=item["map_index"],
                    plan_id=item["plan_id"],
                )
            )
        if new_entities:
            async_add_entities(new_entities)

    @callback
    def async_add_setting_switches() -> None:
        settings = device_settings_section(coordinator.device_settings)
        if settings is None:
            return
        new_entities: list[SwitchEntity] = []
        if (
            settings.get("charging_settings_available")
            and "charging_period" not in known_setting_switches
        ):
            known_setting_switches.add("charging_period")
            new_entities.append(DreameLawnMowerChargingPeriodSwitch(coordinator))
        if (
            settings.get("rain_settings_available")
            and "rain_protection" not in known_setting_switches
        ):
            known_setting_switches.add("rain_protection")
            new_entities.append(DreameLawnMowerRainProtectionSwitch(coordinator))
        supported = settings.get("anti_theft_supported_settings")
        supported_keys = set(supported) if isinstance(supported, list) else set()
        for key, name, icon in ANTI_THEFT_SWITCHES:
            if key not in supported_keys or key in known_setting_switches:
                continue
            known_setting_switches.add(key)
            new_entities.append(
                DreameLawnMowerAntiTheftSwitch(
                    coordinator,
                    key=key,
                    name=name,
                    icon=icon,
                )
            )
        if new_entities:
            async_add_entities(new_entities)

    @callback
    def async_add_preference_switches() -> None:
        supported_keys = reported_preference_switch_keys(coordinator.batch_device_data)
        new_entities: list[SwitchEntity] = []
        for description in PREFERENCE_SWITCHES:
            if (
                description.key not in supported_keys
                or description.key in known_preference_switches
            ):
                continue
            known_preference_switches.add(description.key)
            new_entities.append(
                DreameLawnMowerPreferenceSwitch(coordinator, description)
            )
        if "obstacle_avoidance_ai" in supported_keys:
            for description in AI_CLASS_SWITCHES:
                unique_key = f"obstacle_avoidance_ai:{description.key}"
                if unique_key in known_preference_switches:
                    continue
                known_preference_switches.add(unique_key)
                new_entities.append(
                    DreameLawnMowerPreferenceAiClassSwitch(
                        coordinator,
                        description,
                    )
                )
        if new_entities:
            async_add_entities(new_entities)

    async_add_schedule_switches()
    async_add_setting_switches()
    async_add_preference_switches()
    entry.async_on_unload(coordinator.async_add_listener(async_add_schedule_switches))
    entry.async_on_unload(coordinator.async_add_listener(async_add_setting_switches))
    entry.async_on_unload(coordinator.async_add_listener(async_add_preference_switches))


class DreameLawnMowerVoicePromptSwitch(DreameLawnMowerEntity, SwitchEntity):
    """Expose one prompt category from the mower VOICE flag array."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        *,
        key: str,
        name: str,
        index: int,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._voice_key = key
        self._voice_index = index
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{self._descriptor.unique_id}_{key}"

    @property
    def available(self) -> bool:
        """Return whether cached voice settings are available."""
        return self.coordinator.data is not None and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether the prompt category is enabled."""
        section = _voice_settings_section(self.coordinator.voice_settings)
        if section is None:
            return None
        prompts = section.get("voice_prompts")
        if not isinstance(prompts, list) or len(prompts) <= self._voice_index:
            return None
        return bool(prompts[self._voice_index])

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the prompt category."""
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the prompt category."""
        await self._async_set_state(False)

    async def _async_set_state(self, enabled: bool) -> None:
        section = _voice_settings_section(self.coordinator.voice_settings)
        if section is None:
            raise ValueError("Voice settings are not available.")
        prompts = section.get("voice_prompts")
        if not isinstance(prompts, list) or len(prompts) < 4:
            raise ValueError("Voice prompt settings are not available.")
        updated = [1 if bool(value) else 0 for value in prompts[:4]]
        updated[self._voice_index] = 1 if enabled else 0
        await self.coordinator.client.async_set_voice_prompts(updated)
        await self.coordinator.async_refresh_voice_settings(force=True)
        self.coordinator.async_update_listeners()


class DreameLawnMowerChargingPeriodSwitch(DreameLawnMowerEntity, SwitchEntity):
    """Enable the mower-native custom charging window."""

    _attr_name = "Charging Period"
    _attr_icon = "mdi:battery-clock"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_charging_period"

    @property
    def available(self) -> bool:
        settings = device_settings_section(self.coordinator.device_settings)
        return bool(
            self.coordinator.data is not None
            and settings
            and settings.get("charging_settings_available")
        )

    @property
    def is_on(self) -> bool | None:
        settings = device_settings_section(self.coordinator.device_settings)
        if settings is None or not settings.get("charging_settings_available"):
            return None
        return bool(settings.get("charging_period_enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_charging_period(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_charging_period(enabled=False)


class DreameLawnMowerRainProtectionSwitch(DreameLawnMowerEntity, SwitchEntity):
    """Enable mower-native rain detection and return-to-dock protection."""

    _attr_name = "Rain Protection"
    _attr_icon = "mdi:weather-rainy"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_rain_protection"

    @property
    def available(self) -> bool:
        settings = device_settings_section(self.coordinator.device_settings)
        return bool(
            self.coordinator.data is not None
            and settings
            and settings.get("rain_settings_available")
        )

    @property
    def is_on(self) -> bool | None:
        settings = device_settings_section(self.coordinator.device_settings)
        if settings is None or not settings.get("rain_settings_available"):
            return None
        return bool(settings.get("rain_protection_enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_rain_protection(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_rain_protection(enabled=False)


class DreameLawnMowerAntiTheftSwitch(DreameLawnMowerEntity, SwitchEntity):
    """Expose one anti-theft flag explicitly reported by the mower."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        *,
        key: str,
        name: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._setting_key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{self._descriptor.unique_id}_{key}"

    @property
    def available(self) -> bool:
        """Return whether this exact ATA field remains reported."""
        settings = device_settings_section(self.coordinator.device_settings)
        supported = settings.get("anti_theft_supported_settings") if settings else None
        return bool(
            self.coordinator.data is not None
            and isinstance(supported, list)
            and self._setting_key in supported
        )

    @property
    def is_on(self) -> bool | None:
        """Return the confirmed cached ATA value."""
        settings = device_settings_section(self.coordinator.device_settings)
        value = settings.get(self._setting_key) if settings else None
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable this anti-theft setting."""
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable this anti-theft setting."""
        await self._async_set_state(False)

    async def _async_set_state(self, enabled: bool) -> None:
        await self.coordinator.async_set_anti_theft_settings(
            **{self._setting_key: enabled}
        )


class DreameLawnMowerSchedulePlanSwitch(DreameLawnMowerEntity, SwitchEntity):
    """Expose one mower-native schedule plan as a standard HA switch."""

    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        *,
        map_index: int,
        plan_id: int,
    ) -> None:
        super().__init__(coordinator)
        self._map_index = map_index
        self._plan_id = plan_id
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_schedule_{map_index}_{plan_id}"
        )

    @property
    def name(self) -> str:
        item = self._entry
        if item is None:
            return f"Map {self._map_index + 1} Schedule {self._plan_id + 1}"
        map_label = item.get("map_label") or f"Map {self._map_index + 1}"
        plan_name = item.get("name") or f"Schedule {self._plan_id + 1}"
        return f"{map_label} {plan_name}"

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None and self._entry is not None

    @property
    def is_on(self) -> bool | None:
        item = self._entry
        return bool(item["enabled"]) if item is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        item = self._entry
        if item is None:
            return {
                "schedule_control": True,
                "map_index": self._map_index,
                "plan_id": self._plan_id,
            }
        return dict(item)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_state(False)

    async def _async_set_state(self, enabled: bool) -> None:
        await self.coordinator.async_set_schedule_plan_enabled(
            map_index=self._map_index,
            plan_id=self._plan_id,
            enabled=enabled,
        )

    @property
    def _entry(self) -> dict[str, Any] | None:
        for item in schedule_plan_entries(self.coordinator.schedules):
            if (
                item["map_index"] == self._map_index
                and item["plan_id"] == self._plan_id
            ):
                return item
        return None


def schedule_plan_entries(value: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten decoded schedules into stable, user-facing plan controls."""
    if not isinstance(value, Mapping):
        return []
    result: list[dict[str, Any]] = []
    for schedule in value.get("schedules") or ():
        if not isinstance(schedule, Mapping) or not schedule.get("available"):
            continue
        map_index = schedule.get("idx")
        if not isinstance(map_index, int) or isinstance(map_index, bool):
            continue
        map_label = schedule.get("name") or schedule.get("label")
        if not isinstance(map_label, str) or not map_label.strip():
            map_label = f"Map {map_index + 1}"
        for plan in schedule.get("plans") or ():
            if not isinstance(plan, Mapping):
                continue
            plan_id = plan.get("plan_id")
            if not isinstance(plan_id, int) or isinstance(plan_id, bool):
                continue
            starts: list[str] = []
            days: list[str] = []
            task_count = 0
            for week in plan.get("weeks") or ():
                if not isinstance(week, Mapping):
                    continue
                tasks = [
                    task
                    for task in week.get("tasks") or ()
                    if isinstance(task, Mapping)
                ]
                if tasks:
                    day = week.get("week_day_name")
                    if isinstance(day, str):
                        days.append(day)
                task_count += len(tasks)
                starts.extend(
                    task["start_time"]
                    for task in tasks
                    if isinstance(task.get("start_time"), str)
                )
            name = plan.get("name")
            result.append(
                {
                    "schedule_control": True,
                    "map_index": map_index,
                    "map_label": map_label,
                    "plan_id": plan_id,
                    "name": name.strip() if isinstance(name, str) else "",
                    "enabled": bool(plan.get("enabled")),
                    "version": schedule.get("version"),
                    "weekdays": days,
                    "start_times": starts,
                    "task_count": task_count,
                }
            )
    return result


def reported_preference_switch_keys(
    value: Mapping[str, Any] | None,
) -> set[str]:
    """Return boolean preference fields actually reported by at least one map."""
    preferences = (
        value.get("batch_mowing_preferences") if isinstance(value, Mapping) else None
    )
    maps = preferences.get("maps") if isinstance(preferences, Mapping) else None
    result: set[str] = set()
    if not isinstance(maps, list):
        return result
    switch_keys = {description.key for description in PREFERENCE_SWITCHES}
    for map_entry in maps:
        entries = (
            map_entry.get("preferences") if isinstance(map_entry, Mapping) else None
        )
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            result.update(
                key for key in switch_keys if isinstance(entry.get(key), bool)
            )
            if isinstance(entry.get("obstacle_avoidance_ai"), int):
                result.add("obstacle_avoidance_ai")
    return result


def _voice_settings_section(value: dict[str, Any] | None) -> dict[str, Any] | None:
    section = value.get("voice_settings") if isinstance(value, dict) else None
    return section if isinstance(section, dict) and section.get("available") else None
