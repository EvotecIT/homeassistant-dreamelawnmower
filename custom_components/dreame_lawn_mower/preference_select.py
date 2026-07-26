"""User-friendly selectors for the currently editable mowing preference."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.entity import EntityCategory

from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity
from .mowing_preference_control import (
    async_update_selected_active_preference,
    selected_active_preference_attributes,
)


@dataclass(frozen=True, slots=True)
class PreferenceSelectDescription:
    """Describe a discrete mower preference exposed as a select."""

    key: str
    name: str
    icon: str
    options: tuple[tuple[str, int], ...]


PREFERENCE_SELECTS = (
    PreferenceSelectDescription(
        key="efficient_mode",
        name="Selected Mowing Efficiency",
        icon="mdi:speedometer",
        options=(("Standard", 0), ("Efficient", 1)),
    ),
    PreferenceSelectDescription(
        key="mowing_direction_mode",
        name="Selected Mowing Direction Mode",
        icon="mdi:compass-rose",
        options=(("None", 0), ("Mow at angle", 1), ("Checkerboard", 2)),
    ),
    PreferenceSelectDescription(
        key="obstacle_avoidance_height_cm",
        name="Selected Obstacle Height",
        icon="mdi:arrow-expand-vertical",
        options=(("5 cm", 5), ("10 cm", 10), ("15 cm", 15), ("20 cm", 20)),
    ),
    PreferenceSelectDescription(
        key="obstacle_avoidance_distance_cm",
        name="Selected Obstacle Distance",
        icon="mdi:arrow-expand-horizontal",
        options=(("10 cm", 10), ("15 cm", 15), ("20 cm", 20)),
    ),
    PreferenceSelectDescription(
        key="edge_mowing_walk_mode",
        name="Selected Turning Method",
        icon="mdi:turnstile-outline",
        options=(("Lawn care", 0), ("Efficient", 1)),
    ),
)


class DreameLawnMowerPreferenceSelect(DreameLawnMowerEntity, SelectEntity):
    """Expose one preference for the global map or selected custom zone."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: DreameLawnMowerCoordinator,
        description: PreferenceSelectDescription,
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
        """Return whether the selected preference and value are available."""
        return self.coordinator.data is not None and self.current_option is not None

    @property
    def options(self) -> list[str]:
        """Return device-supported values shown in the app."""
        return [label for label, _ in self._preference_description.options]

    @property
    def current_option(self) -> str | None:
        """Return the selected preference as a friendly label."""
        value = selected_active_preference_attributes(self.coordinator).get(
            self._preference_description.key
        )
        for label, encoded in self._preference_description.options:
            if value == encoded:
                return label
        return None

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

    async def async_select_option(self, option: str) -> None:
        """Persist one discrete preference through the guarded write path."""
        values = dict(self._preference_description.options)
        if option not in values:
            raise ValueError(f"Unknown {self._attr_name} option: {option}")
        await async_update_selected_active_preference(
            self.coordinator,
            changes={self._preference_description.key: values[option]},
        )
