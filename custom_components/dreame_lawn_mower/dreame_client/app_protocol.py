"""Small helpers derived from Dreamehome app protocol assets."""

from __future__ import annotations

from typing import Final

MOWER_RAW_STATUS_PROPERTY_KEY: Final[str] = "1.1"
MOWER_STATE_PROPERTY_KEY: Final[str] = "2.1"
MOWER_ERROR_PROPERTY_KEY: Final[str] = "2.2"
MOWER_PROPERTY_HINTS: Final[dict[str, str]] = {
    MOWER_RAW_STATUS_PROPERTY_KEY: "raw_status_blob",
    MOWER_STATE_PROPERTY_KEY: "mower_state",
    MOWER_ERROR_PROPERTY_KEY: "mower_error",
}
MOWER_STATE_LABELS: Final[dict[str, dict[str, str]]] = {
    "en": {
        "1": "Working",
        "2": "Standby",
        "3": "Working",
        "4": "Paused",
        "5": "Returning Charge",
        "6": "Charging",
        "11": "Mapping",
        "13": "Charging Completed",
        "14": "Upgrading",
    },
    "zh": {
        "1": "割草中",
        "2": "待机中",
        "3": "暂停",
        "4": "暂停",
        "5": "正在回充",
        "6": "正在充电",
        "11": "正在建图",
        "13": "充电完成",
        "14": "正在升级",
    },
}


def _clean_label(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    text = text.replace("_", " ").replace("wheell", "wheel")
    return text.capitalize()


def mower_state_label(value: object, language: str = "en") -> str | None:
    """Return the app-derived mower state label for a raw `2.1` value."""
    if value is None:
        return None

    label_map = MOWER_STATE_LABELS.get(language) or MOWER_STATE_LABELS["en"]
    return label_map.get(str(value))


def mower_error_label(value: object) -> str | None:
    """Return a cleaned mower error label for a raw `2.2` value."""
    if value is None:
        return None

    try:
        code = int(value)
    except (TypeError, ValueError):
        return None
    if code in (-1, 0):
        return "No error"

    try:
        from .const import ERROR_CODE_TO_ERROR_NAME
        from .types import DreameMowerErrorCode

        enum_value = DreameMowerErrorCode(code)
        return _clean_label(ERROR_CODE_TO_ERROR_NAME.get(enum_value))
    except (ImportError, ValueError):
        return None
