"""Shared cached-state access for the legacy device's cooperating mixins."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .device_types import (
    DreameMowerAIProperty,
    DreameMowerAutoSwitchProperty,
    DreameMowerProperty,
    DreameMowerState,
    DreameMowerStateOld,
    DreameMowerStatus,
    DreameMowerStrAIProperty,
    DreameMowerTaskStatus,
)

if TYPE_CHECKING:
    from threading import Timer

    from .device_info import DreameMowerDeviceInfo
    from .device_status import DreameMowerDeviceStatus
    from .device_types import (
        DreameMowerAction,
        DreameMowerDeviceCapability,
    )
    from .map_manager import DreameMapMowerMapManager
    from .protocol import DreameMowerProtocol


class _DreameMowerDeviceContext:
    """Describe members initialized by DreameMowerDevice for all its mixins.

    This base owns cached property access, listener notification and shared
    declarations. Device
    construction and property/action mapping values remain in the assembled
    device; no defaults are supplied here.
    """

    status: DreameMowerDeviceStatus
    capability: DreameMowerDeviceCapability
    _protocol: DreameMowerProtocol
    _map_manager: DreameMapMowerMapManager | None
    property_mapping: dict[DreameMowerProperty, dict[str, int]]
    _read_write_properties: list[DreameMowerProperty]
    action_mapping: dict[DreameMowerAction, dict[str, int]]

    info: DreameMowerDeviceInfo | None
    available: bool
    disconnected: bool
    _ready: bool
    _update_running: bool
    _remote_control: bool
    _consumable_change: bool
    _last_settings_request: float
    _last_map_list_request: float
    _last_map_request: float
    _last_change: float
    _last_update_failed: float | None
    _cleaning_history_update: float
    _map_select_time: float | None
    _update_fail_count: int
    _discard_timeout: float
    _restore_timeout: float
    _update_timer: Timer | None
    _update_callback: Callable[[], None] | None
    _error_callback: Callable[[Exception], None] | None
    _property_update_callback: dict[int, list[Callable[[Any], None]]]
    data: dict[int, Any]
    auto_switch_data: dict[str, Any] | None
    ai_data: dict[str, Any] | None

    def get_property(
        self,
        prop: (
            DreameMowerProperty
            | DreameMowerAutoSwitchProperty
            | DreameMowerStrAIProperty
            | DreameMowerAIProperty
        ),
    ) -> Any:
        """Get a device property from memory"""
        if isinstance(prop, DreameMowerAutoSwitchProperty):
            return self.get_auto_switch_property(prop)
        if isinstance(prop, DreameMowerStrAIProperty) or isinstance(
            prop, DreameMowerAIProperty
        ):
            return self.get_ai_property(prop)
        if prop is not None and prop.value in self.data:
            return self.data[prop.value]
        return None

    def get_auto_switch_property(
        self, prop: DreameMowerAutoSwitchProperty
    ) -> int | None:
        """Get a device auto switch property from memory"""
        if self.capability.auto_switch_settings and self.auto_switch_data:
            if prop is not None and prop.name in self.auto_switch_data:
                return int(self.auto_switch_data[prop.name])
        return None

    def get_ai_property(
        self, prop: DreameMowerStrAIProperty | DreameMowerAIProperty
    ) -> bool | None:
        """Get a device AI property from memory"""
        if self.capability.ai_detection and self.ai_data:
            if prop is not None and prop.name in self.ai_data:
                return bool(self.ai_data[prop.name])
        return None

    def _update_status(
        self, task_status: DreameMowerTaskStatus, status: DreameMowerStatus
    ) -> None:
        """Update cached status for rendering before sending the device action."""
        if task_status is not DreameMowerTaskStatus.COMPLETED:
            new_state = DreameMowerState.MOWING
            self._update_property(DreameMowerProperty.STATE, new_state.value)

        self._update_property(DreameMowerProperty.STATUS, status.value)
        self._update_property(DreameMowerProperty.TASK_STATUS, task_status.value)


    def _update_property(self, prop: DreameMowerProperty, value: Any) -> Any:
        """Update device property on memory and notify listeners."""
        if prop in self.property_mapping:
            if (
                not self.capability.new_state
                and prop == DreameMowerProperty.STATE
                and int(value) > 18
                and value in DreameMowerState._value2member_map_
            ):
                state_name = DreameMowerState(value).name
                if state_name in DreameMowerStateOld.__members__:
                    old_state = DreameMowerStateOld[state_name]
                    value = int(old_state)
            current_value = self.get_property(prop)
            if current_value != value:
                did = prop.value
                self.data[did] = value
                if did in self._property_update_callback:
                    for callback in self._property_update_callback[did]:
                        callback(current_value)

                self._property_changed()
                return current_value if current_value is not None else value
        return None


    def _property_changed(self) -> None:
        """Call external listener when a property changed"""
        if self._update_callback:
            self._update_callback()

    @property
    def device_connected(self) -> bool:
        """Return connection status of the device."""
        return self._protocol.connected

    @property
    def cloud_connected(self) -> bool:
        """Return connection status of the device."""
        cloud = self._protocol.cloud
        return cloud is not None and cloud.connected
