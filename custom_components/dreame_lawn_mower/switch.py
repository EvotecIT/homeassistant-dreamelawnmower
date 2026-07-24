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
                DreameLawnMowerPreferenceSwitch(coordinator, description)
                for description in PREFERENCE_SWITCHES
            ),
            *(
                DreameLawnMowerPreferenceAiClassSwitch(coordinator, description)
                for description in AI_CLASS_SWITCHES
            ),
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

    async_add_schedule_switches()
    entry.async_on_unload(coordinator.async_add_listener(async_add_schedule_switches))


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


def _voice_settings_section(value: dict[str, Any] | None) -> dict[str, Any] | None:
    section = value.get("voice_settings") if isinstance(value, dict) else None
    return section if isinstance(section, dict) and section.get("available") else None
