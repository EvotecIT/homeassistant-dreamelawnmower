"""Automation events for repeated mower person-detection announcements."""

from datetime import UTC, datetime

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import DreameLawnMowerCoordinator
from .dreame_lawn_mower_client.device_code_semantics import (
    supports_operational_human_detection,
)
from .dreame_lawn_mower_client.notice_events import MowerNoticeEventCursor
from .entity import DreameLawnMowerEntity
from .runtime_data import DreameLawnMowerConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DreameLawnMowerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Expose person detection on models with confirmed operational notices."""
    coordinator = entry.runtime_data
    if supports_operational_human_detection(coordinator.client.descriptor.model):
        async_add_entities([DreameLawnMowerPersonDetectionEvent(coordinator)])


class DreameLawnMowerPersonDetectionEvent(DreameLawnMowerEntity, EventEntity):
    """Publish each new announcement while retaining condition sensors separately."""

    _attr_translation_key = "person_detection"
    _attr_device_class = EventDeviceClass.MOTION
    _attr_event_types = ["human_detected"]

    def __init__(self, coordinator: DreameLawnMowerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_person_detection"
        self._cursor = MowerNoticeEventCursor()

    async def async_added_to_hass(self) -> None:
        """Skip cached startup observations before listening for new occurrences."""
        self._cursor.new_events(self.coordinator.data.notification_events)
        await super().async_added_to_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Keep every buffered occurrence when MQTT callbacks were coalesced."""
        if self.available:
            # Restore availability before emitting occurrences so automations can
            # ignore the restored last event without dropping a fresh detection.
            if (
                (state := self.hass.states.get(self.entity_id)) is not None
                and state.state == STATE_UNAVAILABLE
            ):
                self.async_write_ha_state()
            for event in self._cursor.new_events(
                self.coordinator.data.notification_events
            ):
                self._trigger_event(
                    event.name,
                    {
                        "code": event.code,
                        "severity": event.tier,
                        "occurrence": f"{event.stream_id}:{event.sequence}",
                        "observed_at": datetime.fromtimestamp(
                            event.received_at, UTC
                        ).isoformat(),
                    },
                )
                self.async_write_ha_state()
        self.async_write_ha_state()
