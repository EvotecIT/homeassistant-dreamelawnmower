"""Conservative link diagnostics from the mower heartbeat property."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .wire_values import normalize_byte_array


@dataclass(frozen=True, slots=True)
class MowerLinkSignals:
    """Observed link readings; absent/placeholder signals have no dBm value."""

    wifi_signal_dbm: int | None = None
    cellular_signal_dbm: int | None = None
    cellular_module_installed: bool | None = None
    cellular_plan_valid: bool | None = None


def decode_link_signals(
    value: object, *, property_key: str | None,
) -> MowerLinkSignals:
    """Read only validated 1.1 heartbeats, never a same-sized 1.4 pose.

    The proven 20/22-byte frames share signal offsets 17 and 18. This
    qualification establishes no task-state semantics for other frame shapes.
    """
    if (
        property_key != "1.1"
        or not isinstance(value, (list, tuple, bytes))
        or len(value) not in (20, 22)
        or any(type(item) is not int or not 0 <= item <= 255 for item in value)
        or value[0] != 0xCE
        or value[-1] != 0xCE
    ):
        return MowerLinkSignals()
    cellular = value[18]
    return MowerLinkSignals(
        wifi_signal_dbm=_dbm(value[17]),
        cellular_signal_dbm=_dbm(cellular),
        cellular_module_installed=cellular != 127,
        cellular_plan_valid=cellular >= 128 if cellular != 127 else None,
    )


def link_signals_from_properties(properties: object) -> MowerLinkSignals:
    """Project the canonical heartbeat cache without reading the device again."""
    entry = properties.get("1.1") if isinstance(properties, Mapping) else None
    value = entry.get("value") if isinstance(entry, Mapping) else entry
    return decode_link_signals(normalize_byte_array(value), property_key="1.1")


def _dbm(raw: int) -> int | None:
    # 128 is a placeholder, 127 means an absent cellular module, and zero
    # carries no Wi-Fi reading. None of these values is a measured 0 dBm.
    return raw - 256 if raw > 128 else None
