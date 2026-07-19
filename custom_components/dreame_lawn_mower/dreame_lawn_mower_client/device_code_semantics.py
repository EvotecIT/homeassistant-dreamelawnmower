"""Classify values from the mower device-code channel."""

from __future__ import annotations

from typing import Final

# User-confirmed fault meanings that require intervention.
MOWER_FAULT_CODE_NAMES: Final[dict[int, str]] = {
    2: "mower_stuck",
    23: "emergency_stop_pressed",
    31: "left_wheel_speed",
}

# Normal operating conditions that can change what the mower does without
# requiring intervention. These remain visible as status notices while the
# mower entity continues to report its real activity.
MOWER_STATUS_NOTICE_CODE_NAMES: Final[dict[int, str]] = {
    53: "rain_detected",
    54: "low_battery",
}

# Mower firmware also sends lifecycle events through property 2.2, which the
# inherited vacuum model calls an error property. Codes 48/50 are task
# finish/start, 61/70 are DND start/end, and 63 is a schedule suspension while
# the mower is already working.
MOWER_INFORMATIONAL_EVENT_CODES: Final[frozenset[int]] = frozenset(
    {48, 50, 61, 63, 70}
)

MOWER_DEVICE_CODE_NAMES: Final[dict[int, str]] = {
    **MOWER_FAULT_CODE_NAMES,
    **MOWER_STATUS_NOTICE_CODE_NAMES,
}
MOWER_NON_FAULT_CODES: Final[frozenset[int]] = frozenset(
    {*MOWER_STATUS_NOTICE_CODE_NAMES, *MOWER_INFORMATIONAL_EVENT_CODES}
)


def mower_device_code(value: object) -> int | None:
    """Return a numeric mower device code when the value is valid."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def mower_fault_code(value: object) -> int | None:
    """Return an active hard-fault code, excluding notices and events."""
    code = mower_device_code(value)
    if code in (None, -1, 0) or code in MOWER_NON_FAULT_CODES:
        return None
    return code


def mower_fault_active(value: object) -> bool | None:
    """Return whether a valid device-code value represents a hard fault."""
    code = mower_device_code(value)
    if code is None:
        return None
    return mower_fault_code(code) is not None


def mower_status_notice_code(value: object) -> int | None:
    """Return a known non-fault operating-condition code."""
    code = mower_device_code(value)
    return code if code in MOWER_STATUS_NOTICE_CODE_NAMES else None
