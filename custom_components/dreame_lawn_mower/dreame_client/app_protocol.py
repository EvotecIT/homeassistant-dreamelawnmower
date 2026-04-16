"""Small helpers derived from Dreamehome app protocol assets."""

from __future__ import annotations

from typing import Final

MOWER_STATE_PROPERTY_KEY: Final[str] = "2.1"
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


def mower_state_label(value: object, language: str = "en") -> str | None:
    """Return the app-derived mower state label for a raw `2.1` value."""
    if value is None:
        return None

    label_map = MOWER_STATE_LABELS.get(language) or MOWER_STATE_LABELS["en"]
    return label_map.get(str(value))
