"""Mower-native classification for the shared ``2.2`` device-code channel.

The inherited client called this an error property and decoded it with a vacuum
error enum. Mowers also use the channel for alerts, maintenance reminders,
lifecycle notifications, and operating conditions, so numeric overlap with a
vacuum code is not evidence of a mower fault.

The base registry is the mower community's cross-model table. The A2 overrides
come from the g2408 Dreame app plugin fault catalog, whose FAULT/ALERT/INFO
metadata distinguishes hard faults from notifications. Unknown codes remain
visible but are never promoted to hard faults solely because a vacuum enum
happens to contain the same number.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class MowerDeviceCodeTier(StrEnum):
    """User-impact tier assigned by mower-native metadata."""

    ERROR = "error"
    ATTENTION = "attention"
    ALERT = "alert"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class MowerDeviceCodeDefinition:
    """Meaning and impact of one mower device code."""

    name: str
    tier: MowerDeviceCodeTier


def _definitions(
    tier: MowerDeviceCodeTier,
    entries: dict[int, str],
) -> dict[int, MowerDeviceCodeDefinition]:
    return {
        code: MowerDeviceCodeDefinition(name=name, tier=tier)
        for code, name in entries.items()
    }


# Shared mower registry. Model-specific app catalogs override entries below.
_BASE_DEVICE_CODES: Final[dict[int, MowerDeviceCodeDefinition]] = {
    **_definitions(
        MowerDeviceCodeTier.ERROR,
        {
            1: "robot_tilted",
            2: "mower_stuck",
            3: "path_to_station_too_narrow",
            4: "left_drive_wheel_error",
            5: "right_drive_wheel_error",
            6: "blade_height_motor_error",
            7: "blade_disc_blocked",
            8: "blade_disc_side_motor_error",
            9: "bumper_error",
            10: "charging_error",
            11: "battery_temperature_too_high",
            12: "lidar_blocked",
            13: "lidar_temperature_high_without_map",
            14: "lidar_temperature_high_with_map",
            15: "lidar_temperature_too_high",
            16: "lidar_dirty",
            17: "lidar_error",
            18: "location_signal_weak",
            19: "location_lost",
            20: "sensor_error",
            21: "mower_in_no_go_zone",
            22: "mower_outside_map",
            23: "emergency_stop_pressed",
            24: "battery_critically_low",
            25: "map_file_damaged",
            26: "mower_too_far_from_map",
            27: "human_detected",
            37: "path_blocked",
            73: "top_cover_open",
        },
    ),
    **_definitions(
        MowerDeviceCodeTier.ATTENTION,
        {
            28: "blades_worn",
            29: "station_brush_worn",
            30: "maintenance_due",
        },
    ),
    **_definitions(
        MowerDeviceCodeTier.ALERT,
        {
            31: "return_to_station_failed",
            32: "docking_failed",
            33: "positioning_failed_with_map",
            34: "positioning_failed_without_map",
            35: "positioning_error",
            36: "task_start_failed",
            38: "lidar_dirty",
            39: "camera_dirty",
            40: "camera_error",
            41: "camera_blocked",
            42: "charging_paused_battery_too_hot",
            43: "charging_paused_battery_too_cold",
            44: "automatic_boundary_detection_stopped",
            45: "automatic_boundary_side_detection_stopped",
        },
    ),
    **_definitions(
        MowerDeviceCodeTier.INFO,
        {
            0: "no_device_code",
            46: "boundary_detection_completed",
            47: "new_map_created",
            48: "mowing_task_completed",
            49: "some_zones_unreachable",
            50: "mowing_task_started",
            51: "patrol_task_started",
            52: "point_and_go_started",
            53: "scheduled_mowing_started",
            54: "low_battery_returning",
            55: "scheduled_mowing_suspended_low_battery",
            56: "bad_weather_protection_active",
            57: "scheduled_mowing_interrupted_by_rain",
            58: "scheduled_mowing_suspended_by_rain",
            59: "frost_protection_returning",
            60: "scheduled_mowing_suspended_by_frost",
            61: "do_not_disturb_returning",
            62: "scheduled_mowing_suspended_by_do_not_disturb",
            63: "scheduled_mowing_skipped_mower_busy",
            64: "scheduled_mowing_suspended_by_remote_control",
            65: "scheduled_mowing_suspended_by_emergency_stop",
            66: "scheduled_mowing_suspended_top_cover_open",
            67: "scheduled_mowing_suspended_by_fault",
            68: "scheduled_mowing_timed_out",
            69: "station_not_connected_to_working_area",
            70: "resuming_unfinished_task",
            71: "idle_timeout_returning",
            72: "pause_timeout_returning",
        },
    ),
}


# Dreame A2 / g2408 app-plugin differences. In particular, 16 and 59 do not
# mean what the cross-model registry says, and human detection is an attention
# notice rather than a hard fault.
_A2_DEVICE_CODE_OVERRIDES: Final[dict[int, MowerDeviceCodeDefinition]] = {
    **_definitions(
        MowerDeviceCodeTier.ERROR,
        {
            0: "robot_lifted",
            59: "battery_temperature_too_low",
        },
    ),
    **_definitions(
        MowerDeviceCodeTier.ATTENTION,
        {
            27: "human_detected",
            74: "patrol_task_completed",
            75: "maintenance_point_reached",
            76: "maintenance_point_unreachable",
            77: "error_while_going_to_maintenance_point",
        },
    ),
    **_definitions(
        MowerDeviceCodeTier.INFO,
        {
            16: "scheduled_mowing_suspended_low_light",
            47: "task_cancelled",
        },
    ),
}

_A1_DEVICE_CODE_OVERRIDES: Final[dict[int, MowerDeviceCodeDefinition]] = {
    **_definitions(
        MowerDeviceCodeTier.ERROR,
        {
            19: "emergency_stop_pressed",
            73: "robot_lifted",
        },
    ),
    **_definitions(
        MowerDeviceCodeTier.INFO,
        {
            53: "mowing_started",
            70: "mowing_resumed_after_charging",
        },
    ),
}

_MOVA_DEVICE_CODE_OVERRIDES: Final[dict[int, MowerDeviceCodeDefinition]] = {
    **_definitions(
        MowerDeviceCodeTier.ERROR,
        {
            0: "robot_lifted",
            55: "cannot_start_low_battery",
        },
    ),
}

_A2_MODELS: Final[frozenset[str]] = frozenset({"dreame.mower.g2408", "g2408"})
_A1_MODELS: Final[frozenset[str]] = frozenset(
    {
        "dreame.mower.p2255",
        "dreame.mower.g2422",
        "p2255",
        "g2422",
    }
)
_MOVA_MODELS: Final[frozenset[str]] = frozenset(
    {"mova.mower.g2529c", "g2529c"}
)


def mower_device_code(value: object) -> int | None:
    """Return a numeric mower device code when the value is valid."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def mower_device_code_definition(
    value: object,
    *,
    model: str | None = None,
) -> MowerDeviceCodeDefinition | None:
    """Return the mower-native definition for a code and model."""
    code = mower_device_code(value)
    if code is None or code == -1:
        return None

    normalized_model = str(model or "").strip().casefold()
    if normalized_model in _A2_MODELS:
        definition = _A2_DEVICE_CODE_OVERRIDES.get(code)
        if definition is not None:
            return definition
    elif normalized_model in _A1_MODELS:
        definition = _A1_DEVICE_CODE_OVERRIDES.get(code)
        if definition is not None:
            return definition
    elif normalized_model in _MOVA_MODELS:
        definition = _MOVA_DEVICE_CODE_OVERRIDES.get(code)
        if definition is not None:
            return definition

    return _BASE_DEVICE_CODES.get(code)


def mower_device_code_name(
    value: object,
    *,
    model: str | None = None,
) -> str | None:
    """Return a stable mower-native name, including an honest unknown label."""
    code = mower_device_code(value)
    if code is None or code == -1:
        return None
    definition = mower_device_code_definition(code, model=model)
    if definition is not None:
        return definition.name
    return f"unknown_mower_device_code_{code}"


def mower_device_code_tier(
    value: object,
    *,
    model: str | None = None,
) -> MowerDeviceCodeTier | None:
    """Return the mower-native impact tier for a known code."""
    definition = mower_device_code_definition(value, model=model)
    return definition.tier if definition is not None else None


def mower_fault_code(
    value: object,
    *,
    model: str | None = None,
) -> int | None:
    """Return an active hard-fault code; unknown codes are not guessed."""
    code = mower_device_code(value)
    definition = mower_device_code_definition(code, model=model)
    if definition is None or definition.tier is not MowerDeviceCodeTier.ERROR:
        return None
    return code


def mower_fault_active(
    value: object,
    *,
    model: str | None = None,
) -> bool | None:
    """Return whether a valid device-code value is a known hard fault."""
    code = mower_device_code(value)
    if code is None:
        return None
    return mower_fault_code(code, model=model) is not None


def mower_status_notice_code(
    value: object,
    *,
    model: str | None = None,
) -> int | None:
    """Return an alert/info/unknown code that must not latch mower ERROR."""
    code = mower_device_code(value)
    if code in (None, -1):
        return None
    definition = mower_device_code_definition(code, model=model)
    if definition is not None and definition.name == "no_device_code":
        return None
    if definition is None or definition.tier is not MowerDeviceCodeTier.ERROR:
        return code
    return None


def mower_status_notice_name(
    value: object,
    *,
    model: str | None = None,
) -> str | None:
    """Return a stable name for a non-fault mower notification."""
    code = mower_status_notice_code(value, model=model)
    return mower_device_code_name(code, model=model) if code is not None else None


# Compatibility exports for callers that only need the shared registry.
MOWER_DEVICE_CODE_NAMES: Final[dict[int, str]] = {
    code: definition.name for code, definition in _BASE_DEVICE_CODES.items()
}
MOWER_FAULT_CODE_NAMES: Final[dict[int, str]] = {
    code: definition.name
    for code, definition in _BASE_DEVICE_CODES.items()
    if definition.tier is MowerDeviceCodeTier.ERROR
}
MOWER_STATUS_NOTICE_CODE_NAMES: Final[dict[int, str]] = {
    code: definition.name
    for code, definition in _BASE_DEVICE_CODES.items()
    if definition.tier is not MowerDeviceCodeTier.ERROR
}
MOWER_INFORMATIONAL_EVENT_CODES: Final[frozenset[int]] = frozenset(
    code
    for code, definition in _BASE_DEVICE_CODES.items()
    if definition.tier is MowerDeviceCodeTier.INFO
)
MOWER_NON_FAULT_CODES: Final[frozenset[int]] = frozenset(
    MOWER_STATUS_NOTICE_CODE_NAMES
)
