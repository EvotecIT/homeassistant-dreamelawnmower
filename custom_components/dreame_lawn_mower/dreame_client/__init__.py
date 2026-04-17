"""Bundled reusable Dreame lawn mower client implementation."""

from .client import (
    DreameLawnMowerAuthError,
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    DreameLawnMowerError,
    DreameLawnMowerTwoFactorRequiredError,
)
from .app_protocol import (
    MOWER_STATE_LABELS,
    MOWER_STATE_PROPERTY_KEY,
    mower_state_label,
)
from .map_probe import MAP_PROBE_PROPERTY_KEYS, build_map_probe_payload
from .models import (
    DISPLAY_NAME_ALIASES,
    MODEL_NAME_MAP,
    SUPPORTED_ACCOUNT_TYPES,
    DreameLawnMowerDescriptor,
    DreameLawnMowerMapSummary,
    DreameLawnMowerMapView,
    DreameLawnMowerSnapshot,
    display_name_for_model,
    map_summary_from_map_data,
    map_summary_to_dict,
)

__all__ = [
    "DreameLawnMowerAuthError",
    "DreameLawnMowerClient",
    "DreameLawnMowerConnectionError",
    "DreameLawnMowerDescriptor",
    "DreameLawnMowerError",
    "DreameLawnMowerMapSummary",
    "DreameLawnMowerMapView",
    "DreameLawnMowerSnapshot",
    "DreameLawnMowerTwoFactorRequiredError",
    "DISPLAY_NAME_ALIASES",
    "MODEL_NAME_MAP",
    "MAP_PROBE_PROPERTY_KEYS",
    "MOWER_STATE_LABELS",
    "MOWER_STATE_PROPERTY_KEY",
    "SUPPORTED_ACCOUNT_TYPES",
    "build_map_probe_payload",
    "display_name_for_model",
    "map_summary_from_map_data",
    "map_summary_to_dict",
    "mower_state_label",
]
