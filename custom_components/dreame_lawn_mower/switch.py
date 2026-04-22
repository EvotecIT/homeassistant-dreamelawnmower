"""Switch entities for Dreame mower voice prompt settings."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity

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
            DreameLawnMowerVoicePromptSwitch(
                coordinator,
                key=key,
                name=name,
                index=index,
                icon=icon,
            )
            for key, name, index, icon in VOICE_PROMPT_SWITCHES
        ]
    )


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


def _voice_settings_section(value: dict[str, Any] | None) -> dict[str, Any] | None:
    section = value.get("voice_settings") if isinstance(value, dict) else None
    return section if isinstance(section, dict) and section.get("available") else None
