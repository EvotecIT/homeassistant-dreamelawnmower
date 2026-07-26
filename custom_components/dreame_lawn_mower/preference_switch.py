"""User-friendly switches for the currently editable mowing preference."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory

from .coordinator import DreameLawnMowerCoordinator
from .dreame_lawn_mower_client.mowing_preferences import OBSTACLE_AI_CLASSES
from .entity import DreameLawnMowerEntity
from .mowing_preference_control import (
    async_update_selected_active_preference,
    selected_active_preference_attributes,
)


@dataclass(frozen=True, slots=True)
class PreferenceSwitchDescription:
    """Describe a boolean mower preference exposed as a switch."""

    key: str
    name: str
    icon: str


PREFERENCE_SWITCHES = (
    PreferenceSwitchDescription(
        key="edge_mowing_auto",
        name="Selected Automatic Edge Cutting",
        icon="mdi:vector-polyline-plus",
    ),
    PreferenceSwitchDescription(
        key="edge_mowing_safe",
        name="Selected Safe Edge Cutting",
        icon="mdi:shield-check-outline",
    ),
    PreferenceSwitchDescription(
        key="edge_cutting_attachment",
        name="Selected EdgeMaster",
        icon="mdi:content-cut",
    ),
    PreferenceSwitchDescription(
        key="edge_mowing_obstacle_avoidance",
        name="Selected Edge Obstacle Avoidance",
        icon="mdi:shield-tree-outline",
    ),
    PreferenceSwitchDescription(
        key="obstacle_avoidance_enabled",
        name="Selected Lidar Obstacle Recognition",
        icon="mdi:radar",
    ),
)

AI_CLASS_SWITCHES = (
    PreferenceSwitchDescription(
        key="people",
        name="Selected Avoid People",
        icon="mdi:account-alert-outline",
    ),
    PreferenceSwitchDescription(
        key="animals",
        name="Selected Avoid Animals",
        icon="mdi:paw",
    ),
    PreferenceSwitchDescription(
        key="objects",
        name="Selected Avoid Objects",
        icon="mdi:shape-outline",
    ),
)


class DreameLawnMowerPreferenceSwitch(DreameLawnMowerEntity, SwitchEntity):
    """Expose one boolean preference for the active map preference scope."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        description: PreferenceSwitchDescription,
    ) -> None:
        super().__init__(coordinator)
        self._preference_description = description
        self._attr_name = description.name
        self._attr_icon = description.icon
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_selected_{description.key}"
        )

    @property
    def available(self) -> bool:
        """Return whether the selected preference value is available."""
        return self.coordinator.data is not None and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        """Return the current boolean preference value."""
        value = selected_active_preference_attributes(self.coordinator).get(
            self._preference_description.key
        )
        return value if isinstance(value, bool) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Describe which map preference record this control edits."""
        attributes = selected_active_preference_attributes(self.coordinator)
        return {
            key: attributes[key]
            for key in (
                "preference_scope",
                "selected_map_index",
                "selected_map_label",
                "selected_zone_id",
                "selected_zone_label",
            )
            if key in attributes
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable this preference."""
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable this preference."""
        await self._async_set_state(False)

    async def _async_set_state(self, enabled: bool) -> None:
        await async_update_selected_active_preference(
            self.coordinator,
            changes={self._preference_description.key: enabled},
        )


class DreameLawnMowerPreferenceAiClassSwitch(DreameLawnMowerPreferenceSwitch):
    """Toggle one class in the obstacle-recognition bit mask."""

    @property
    def is_on(self) -> bool | None:
        """Return whether this obstacle class is selected."""
        attributes = selected_active_preference_attributes(self.coordinator)
        classes = attributes.get("obstacle_avoidance_ai_classes")
        if isinstance(classes, list):
            return self._preference_description.key in classes
        mask = attributes.get("obstacle_avoidance_ai")
        bit = next(
            (
                value
                for value, name in OBSTACLE_AI_CLASSES
                if name == self._preference_description.key
            ),
            None,
        )
        return bool(mask & bit) if isinstance(mask, int) and bit is not None else None

    async def _async_set_state(self, enabled: bool) -> None:
        attributes = selected_active_preference_attributes(self.coordinator)
        classes = attributes.get("obstacle_avoidance_ai_classes")
        if isinstance(classes, list):
            updated = [str(value) for value in classes]
        else:
            mask = attributes.get("obstacle_avoidance_ai")
            if not isinstance(mask, int):
                raise ValueError("Obstacle-recognition classes are unavailable.")
            updated = [
                name for bit, name in OBSTACLE_AI_CLASSES if mask & bit
            ]
        key = self._preference_description.key
        if enabled and key not in updated:
            updated.append(key)
        elif not enabled and key in updated:
            updated.remove(key)
        await async_update_selected_active_preference(
            self.coordinator,
            changes={"obstacle_avoidance_ai_classes": updated},
        )
