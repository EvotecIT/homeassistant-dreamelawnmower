"""Shared member declarations for the legacy device's cooperating mixins."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from threading import Timer

    from .device_info import DreameMowerDeviceInfo
    from .device_status import DreameMowerDeviceStatus
    from .device_types import (
        DreameMowerAction,
        DreameMowerDeviceCapability,
        DreameMowerProperty,
    )
    from .protocol import DreameMowerProtocol


class _DreameMowerDeviceContext:
    """Describe members initialized by DreameMowerDevice for all its mixins.

    This base owns declarations only. Device construction and property/action
    mapping values remain in the assembled device; no defaults are supplied here.
    """

    status: DreameMowerDeviceStatus
    capability: DreameMowerDeviceCapability
    _protocol: DreameMowerProtocol
    property_mapping: dict[DreameMowerProperty, dict[str, int]]
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
