"""Shared member declarations for the legacy device's cooperating mixins."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
