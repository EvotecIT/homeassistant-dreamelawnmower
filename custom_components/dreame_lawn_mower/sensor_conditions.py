"""Last mower conditions for dashboards and automations."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.helpers.entity import EntityCategory

from .coordinator import DreameLawnMowerCoordinator
from .entity import DreameLawnMowerEntity

_DESCRIPTIONS = {
    "error": SensorEntityDescription(
        key="last_mower_error",
        name="Last Mower Error",
        icon="mdi:alert-circle-check-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    "notification": SensorEntityDescription(
        key="last_mower_notification",
        name="Last Mower Notification",
        icon="mdi:bell-check-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
}


class DreameLawnMowerConditionSensor(DreameLawnMowerEntity, SensorEntity):
    """Expose the latest fault or actionable notice after it clears."""

    def __init__(
        self, coordinator: DreameLawnMowerCoordinator, *, kind: str
    ) -> None:
        super().__init__(coordinator)
        self._kind = kind
        self.entity_description = _DESCRIPTIONS[kind]
        self._attr_unique_id = (
            f"{self._descriptor.unique_id}_{self.entity_description.key}"
        )

    @property
    def available(self) -> bool:
        """Keep recorded conditions readable while the mower is offline."""
        return True

    @property
    def native_value(self) -> str:
        """Return the latest message, or none until a condition is observed."""
        event = self._event()
        return event["message"] if event else "none"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose safe event details and a bounded recent-notification list."""
        event = self._event()
        if event is None:
            return {"recent": []} if self._kind == "notification" else {}
        attributes = {
            key: value
            for key, value in event.items()
            if key != "message" and value is not None
        }
        if self._kind == "notification":
            attributes["recent"] = self.coordinator.mower_condition_history.recent()
        return attributes

    def _event(self) -> dict[str, Any] | None:
        history = self.coordinator.mower_condition_history
        return history.latest(severity="error" if self._kind == "error" else None)
