"""Shared member contract for the legacy device's cooperating mixins."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from abc import abstractmethod
    from collections.abc import Callable, Generator
    from threading import RLock, Timer

    from .device_action_plan import ActionDelay, ActionRequest, PropertyRequest
    from .device_info import DreameMowerDeviceInfo
    from .device_status import DreameMowerDeviceStatus
    from .device_types import (
        DirtyData,
        DreameMowerAction,
        DreameMowerAIProperty,
        DreameMowerAutoSwitchProperty,
        DreameMowerCleaningMode,
        DreameMowerDeviceCapability,
        DreameMowerProperty,
        DreameMowerStatus,
        DreameMowerStrAIProperty,
        DreameMowerTaskStatus,
    )
    from .map_manager import DreameMapMowerMapManager
    from .notice_events import MowerNoticeEventBuffer
    from .protocol import DreameMowerProtocol


class _DreameMowerDeviceContext:
    """Describe the state and methods supplied by the assembled device.

    Construction, cached getters, callbacks and command execution remain in
    their existing owners. This base adds no defaults or runtime method bodies.
    Vendor property values remain opaque until their owning decoder projects
    them; the cache keys and resource lifetimes are explicit.
    """

    status: DreameMowerDeviceStatus
    capability: DreameMowerDeviceCapability
    _protocol: DreameMowerProtocol
    _map_manager: DreameMapMowerMapManager | None
    property_mapping: dict[DreameMowerProperty, dict[str, int]]
    action_mapping: dict[DreameMowerAction, dict[str, int]]
    _read_write_properties: list[DreameMowerProperty]
    _default_properties: list[DreameMowerProperty]
    _discarded_properties: list[DreameMowerProperty]

    info: DreameMowerDeviceInfo | None
    available: bool
    disconnected: bool
    _ready: bool
    _update_running: bool
    _remote_control: bool
    _consumable_change: bool
    _previous_cleaning_mode: DreameMowerCleaningMode | None
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

    _state_lock: RLock
    _update_timer_lock: RLock
    _update_timer: Timer | None
    _native_update_scheduler: Callable[[Any, float, bool], None] | None
    _native_message_receiver: Callable[[dict[str, Any]], None] | None
    _native_connected_receiver: Callable[[], None] | None
    _mqtt_generation: int
    _notice_events: MowerNoticeEventBuffer
    _fresh_task_state: dict[str, Any]
    _update_callback: Callable[[], None] | None
    _error_callback: Callable[[Exception], None] | None
    _property_update_callback: dict[int, list[Callable[[Any], None]]]
    _pending_property_callbacks: list[tuple[Callable[[Any], None], Any]]

    data: dict[int, Any]
    unknown_properties: dict[int, dict[str, Any]]
    realtime_properties: dict[str, dict[str, Any]]
    last_realtime_message: dict[str, Any] | None
    _dirty_data: dict[int, DirtyData]
    _dirty_auto_switch_data: dict[str, DirtyData]
    _dirty_ai_data: dict[str, DirtyData] | None
    auto_switch_data: dict[str, Any] | None
    ai_data: dict[str, Any] | None

    if TYPE_CHECKING:
        # These methods are implemented by the assembled device or another
        # mixin. Abstract declarations exist only for static checking; they
        # neither make runtime instances abstract nor install fallback methods.
        @abstractmethod
        def schedule_update(
            self, wait: float | None = None,
            force_request_properties: bool = False,
        ) -> None: ...

        @property
        @abstractmethod
        def _map_update_interval(self) -> float: ...

        @property
        @abstractmethod
        def _update_interval(self) -> float: ...

        @property
        @abstractmethod
        def device_connected(self) -> bool: ...

        @abstractmethod
        def update(
            self, force_request_properties: bool = False,
            *, deadline: float | None = None,
        ) -> None: ...

        @abstractmethod
        def get_property(
            self,
            prop: (
                DreameMowerProperty
                | DreameMowerAutoSwitchProperty
                | DreameMowerStrAIProperty
                | DreameMowerAIProperty
            ),
        ) -> Any: ...

        @abstractmethod
        def get_auto_switch_property(
            self, prop: DreameMowerAutoSwitchProperty,
        ) -> int | None: ...

        @abstractmethod
        def get_ai_property(
            self, prop: DreameMowerStrAIProperty | DreameMowerAIProperty,
        ) -> bool | None: ...

        @abstractmethod
        def _update_status(
            self, task_status: DreameMowerTaskStatus, status: DreameMowerStatus,
        ) -> None: ...

        @abstractmethod
        def _update_property(
            self, prop: DreameMowerProperty, value: Any,
        ) -> Any: ...

        @abstractmethod
        def _property_changed(self) -> None: ...

        @abstractmethod
        def _restore_go_to_zone_plan(
            self, stop: bool = False,
        ) -> Generator[ActionDelay | ActionRequest | PropertyRequest, Any]: ...

        @abstractmethod
        def _set_auto_switch_property_plan(
            self, prop: DreameMowerAutoSwitchProperty, value: int,
        ) -> Generator[PropertyRequest, Any, Any]: ...

        @abstractmethod
        def call_action(
            self,
            action: DreameMowerAction,
            parameters: dict[str, Any] | list[Any] | None = None,
            *,
            enforce_availability: bool = True,
        ) -> dict[str, Any] | None: ...

        @abstractmethod
        def update_map_data_async(self, parameters: dict[str, Any]) -> None: ...
