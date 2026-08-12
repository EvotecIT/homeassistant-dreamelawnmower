"""Mower-native CFG device-setting codecs and write payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

MINUTES_PER_DAY = 24 * 60
RAIN_DELAY_MIN_HOURS = 0
RAIN_DELAY_MAX_HOURS = 24

BATTERY_SETTING_LENGTH = 6
BATTERY_RECHARGE_LEVEL_INDEX = 0
BATTERY_RESUME_LEVEL_INDEX = 1
BATTERY_RESUME_AFTER_CHARGING_INDEX = 2
CHARGING_PERIOD_ENABLED_INDEX = 3
CHARGING_PERIOD_START_INDEX = 4
CHARGING_PERIOD_END_INDEX = 5

RAIN_SETTING_MINIMUM_LENGTH = 2
RAIN_SETTING_LENGTH = 3
RAIN_SETTING_ENABLED_INDEX = 0
RAIN_SETTING_DELAY_INDEX = 1
RAIN_SETTING_SENSITIVITY_INDEX = 2
RAIN_SETTING_DEFAULT_SENSITIVITY = 0

ANTI_THEFT_SETTING_MINIMUM_LENGTH = 3
ANTI_THEFT_LIFT_ALARM_INDEX = 0
ANTI_THEFT_OFF_MAP_ALARM_INDEX = 1
ANTI_THEFT_REAL_TIME_LOCATION_INDEX = 2
ANTI_THEFT_PIN_CHECK_INDEX = 3


def _integer_record(
    value: Any,
    *,
    minimum_length: int,
    fill_to_length: int | None = None,
    fill_value: int = 0,
) -> list[int] | None:
    """Return a validated integer setting record."""
    if isinstance(value, Mapping):
        value = value.get("value")
    if not isinstance(value, Sequence) or isinstance(
        value,
        str | bytes | bytearray,
    ):
        return None
    values = list(value)
    if len(values) < minimum_length:
        return None
    try:
        record = [int(item) for item in values]
    except (TypeError, ValueError):
        return None
    if fill_to_length is not None:
        record.extend([fill_value] * max(0, fill_to_length - len(record)))
    return record


def decode_charging_settings(config: Mapping[str, Any]) -> dict[str, Any] | None:
    """Decode the six-slot BAT record without changing its threshold fields."""
    record = _integer_record(
        config.get("BAT"),
        minimum_length=BATTERY_SETTING_LENGTH,
    )
    if record is None:
        return None
    return {
        "charging_settings_available": True,
        "recharge_battery_level": record[BATTERY_RECHARGE_LEVEL_INDEX],
        "resume_battery_level": record[BATTERY_RESUME_LEVEL_INDEX],
        "resume_after_charging": bool(record[BATTERY_RESUME_AFTER_CHARGING_INDEX]),
        "charging_period_enabled": bool(record[CHARGING_PERIOD_ENABLED_INDEX]),
        "charging_period_start_minutes": record[CHARGING_PERIOD_START_INDEX],
        "charging_period_end_minutes": record[CHARGING_PERIOD_END_INDEX],
        "battery_settings_raw": record,
    }


def decode_rain_settings(config: Mapping[str, Any]) -> dict[str, Any] | None:
    """Decode WRP, filling only the sensitivity omitted by older firmware."""
    record = _integer_record(
        config.get("WRP"),
        minimum_length=RAIN_SETTING_MINIMUM_LENGTH,
        fill_to_length=RAIN_SETTING_LENGTH,
        fill_value=RAIN_SETTING_DEFAULT_SENSITIVITY,
    )
    if record is None:
        return None
    return {
        "rain_settings_available": True,
        "rain_protection_enabled": bool(record[RAIN_SETTING_ENABLED_INDEX]),
        "rain_protection_duration_hours": record[RAIN_SETTING_DELAY_INDEX],
        "rain_sensor_sensitivity": record[RAIN_SETTING_SENSITIVITY_INDEX],
        "rain_protection_raw": record,
    }


def decode_anti_theft_settings(
    config: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Decode the ATA flags reported by the mower without inventing fields."""
    record = _integer_record(
        config.get("ATA"),
        minimum_length=ANTI_THEFT_SETTING_MINIMUM_LENGTH,
    )
    if record is None:
        return None
    supported_settings = [
        "lift_alarm_enabled",
        "off_map_alarm_enabled",
        "real_time_location_enabled",
    ]
    result: dict[str, Any] = {
        "anti_theft_settings_available": True,
        "anti_theft_supported_settings": supported_settings,
        "lift_alarm_enabled": bool(record[ANTI_THEFT_LIFT_ALARM_INDEX]),
        "off_map_alarm_enabled": bool(record[ANTI_THEFT_OFF_MAP_ALARM_INDEX]),
        "real_time_location_enabled": bool(record[ANTI_THEFT_REAL_TIME_LOCATION_INDEX]),
        "anti_theft_settings_raw": record,
    }
    if len(record) > ANTI_THEFT_PIN_CHECK_INDEX:
        supported_settings.append("pin_check_before_power_off_enabled")
        result["pin_check_before_power_off_enabled"] = bool(
            record[ANTI_THEFT_PIN_CHECK_INDEX]
        )
    return result


def decode_device_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    """Decode the CFG settings this integration owns."""
    result: dict[str, Any] = {}
    charging = decode_charging_settings(config)
    if charging is not None:
        result.update(charging)
    rain = decode_rain_settings(config)
    if rain is not None:
        result.update(rain)
    anti_theft = decode_anti_theft_settings(config)
    if anti_theft is not None:
        result.update(anti_theft)
    if "WRF" in config:
        try:
            result["weather_switch_enabled"] = bool(int(config["WRF"]))
        except (TypeError, ValueError):
            pass
    return result


def validate_time_of_day(minutes: int, label: str) -> int:
    """Return minutes since midnight after validating the device range."""
    try:
        normalized = int(minutes)
    except (TypeError, ValueError) as err:
        raise ValueError(
            f"Charging period {label} must be a number of minutes."
        ) from err
    if not 0 <= normalized < MINUTES_PER_DAY:
        raise ValueError(
            f"Charging period {label} must be between 0 and "
            f"{MINUTES_PER_DAY - 1} minutes."
        )
    return normalized


def validate_rain_delay(delay_hours: int) -> int:
    """Return a whole-hour after-rain delay supported by the mower."""
    try:
        normalized = int(delay_hours)
    except (TypeError, ValueError) as err:
        raise ValueError("After-rain delay must be a number of hours.") from err
    if not RAIN_DELAY_MIN_HOURS <= normalized <= RAIN_DELAY_MAX_HOURS:
        raise ValueError(
            f"After-rain delay must be between {RAIN_DELAY_MIN_HOURS} and "
            f"{RAIN_DELAY_MAX_HOURS} hours."
        )
    return normalized


def build_charging_period_request(
    *,
    enabled: bool,
    start_minutes: int,
    end_minutes: int,
) -> dict[str, Any]:
    """Build the narrow BAT charging-period setter used by the app."""
    start = validate_time_of_day(start_minutes, "start")
    end = validate_time_of_day(end_minutes, "end")
    if start == end:
        raise ValueError("Charging period start and end times must differ.")
    return {
        "m": "s",
        "t": "BAT",
        "d": {
            "type": "charging",
            "value": [int(bool(enabled)), start, end],
        },
    }


def build_rain_protection_request(
    *,
    enabled: bool,
    delay_hours: int,
    sensitivity: int,
) -> dict[str, Any]:
    """Build WRP while preserving the mower's existing sensitivity."""
    delay = validate_rain_delay(delay_hours)
    return {
        "m": "s",
        "t": "WRP",
        "d": {
            "value": int(bool(enabled)),
            "time": delay,
            "sen": int(sensitivity),
        },
    }


def build_anti_theft_settings_request(
    current_record: Sequence[int],
    *,
    lift_alarm_enabled: bool | None = None,
    off_map_alarm_enabled: bool | None = None,
    real_time_location_enabled: bool | None = None,
    pin_check_before_power_off_enabled: bool | None = None,
) -> dict[str, Any]:
    """Build one full ATA record while preserving unsupported future slots."""
    record = _integer_record(
        current_record,
        minimum_length=ANTI_THEFT_SETTING_MINIMUM_LENGTH,
    )
    if record is None:
        raise ValueError("Anti-theft settings require at least three values.")
    changes = (
        (ANTI_THEFT_LIFT_ALARM_INDEX, lift_alarm_enabled),
        (ANTI_THEFT_OFF_MAP_ALARM_INDEX, off_map_alarm_enabled),
        (ANTI_THEFT_REAL_TIME_LOCATION_INDEX, real_time_location_enabled),
        (ANTI_THEFT_PIN_CHECK_INDEX, pin_check_before_power_off_enabled),
    )
    for index, value in changes:
        if value is None:
            continue
        if index >= len(record):
            raise ValueError(
                "PIN check before power-off is not reported by this mower."
            )
        record[index] = int(bool(value))
    return {"m": "s", "t": "ATA", "d": {"value": record}}
