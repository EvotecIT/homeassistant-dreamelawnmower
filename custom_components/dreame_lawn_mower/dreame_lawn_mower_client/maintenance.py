"""CMS maintenance counter helpers for Dreame lawn mowers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

CMS_GET_REQUEST: dict[str, str] = {"m": "g", "t": "CMS"}
MAINTENANCE_WARNING_PERCENT = 20.0


@dataclass(frozen=True, slots=True)
class MaintenanceItem:
    """Known CMS maintenance counter metadata."""

    key: str
    name: str
    index: int
    total_minutes: int
    icon: str
    aliases: tuple[str, ...] = ()
    warning_percent: float = MAINTENANCE_WARNING_PERCENT

    @property
    def total_hours(self) -> float:
        """Return the configured maintenance interval in hours."""
        return round(self.total_minutes / 60, 1)


MAINTENANCE_ITEMS: tuple[MaintenanceItem, ...] = (
    MaintenanceItem(
        key="blade",
        name="Blade",
        index=0,
        total_minutes=6000,
        icon="mdi:scissors-cutting",
        aliases=("blades", "knife", "knives"),
    ),
    MaintenanceItem(
        key="brush",
        name="Cleaning Brush",
        index=1,
        total_minutes=30000,
        icon="mdi:brush",
        aliases=("cleaning_brush", "cleaning brush"),
    ),
    MaintenanceItem(
        key="robot",
        name="Robot Maintenance",
        index=2,
        total_minutes=3600,
        icon="mdi:robot-mower",
        aliases=("maintenance", "robot_maintenance", "robot maintenance"),
    ),
)

MAINTENANCE_ITEM_BY_KEY = {item.key: item for item in MAINTENANCE_ITEMS}
_MAINTENANCE_ITEM_ALIASES = {
    alias: item
    for item in MAINTENANCE_ITEMS
    for alias in (item.key, *item.aliases)
}


def normalize_maintenance_item(value: str | MaintenanceItem) -> MaintenanceItem:
    """Return known maintenance item metadata for a key or alias."""
    if isinstance(value, MaintenanceItem):
        return value
    key = str(value).strip().lower().replace("-", "_")
    try:
        return _MAINTENANCE_ITEM_ALIASES[key]
    except KeyError as err:
        allowed = ", ".join(item.key for item in MAINTENANCE_ITEMS)
        raise ValueError(
            f"Unknown maintenance item {value!r}; expected {allowed}."
        ) from err


def build_cms_set_request(values: Sequence[Any]) -> dict[str, Any]:
    """Build the vendor CMS set request while preserving unknown extra slots."""
    normalized = _counter_values(values)
    if len(normalized) < len(MAINTENANCE_ITEMS):
        raise ValueError("CMS data must include blade, brush, and robot counters.")
    return {"m": "s", "t": "CMS", "d": {"value": normalized}}


def reset_cms_counter(
    values: Sequence[Any],
    item: str | MaintenanceItem,
) -> list[int]:
    """Return CMS counters with one known maintenance counter reset to zero."""
    normalized = _counter_values(values)
    target = normalize_maintenance_item(item)
    if target.index >= len(normalized):
        raise ValueError(f"CMS data does not contain the {target.key} counter.")
    normalized[target.index] = 0
    return normalized


def cms_values_from_app_data(data: Any) -> list[int] | None:
    """Extract CMS counter values from direct CMS or CFG app-action data."""
    if _looks_like_counter_values(data):
        return _counter_values(data)
    if isinstance(data, Mapping):
        value = data.get("value")
        if _looks_like_counter_values(value):
            return _counter_values(value)
        cfg_value = data.get("CMS")
        if _looks_like_counter_values(cfg_value):
            return _counter_values(cfg_value)
    return None


def maintenance_status_from_app_data(
    data: Any,
    *,
    source: str,
) -> dict[str, Any]:
    """Return normalized maintenance state from CMS or CFG app-action data."""
    values = cms_values_from_app_data(data)
    if values is None:
        return {
            "source": source,
            "available": False,
            "items": [],
            "raw_cms": None,
        }
    return maintenance_status_from_cms(values, source=source)


def maintenance_status_from_cms(
    values: Sequence[Any],
    *,
    source: str = "cms",
) -> dict[str, Any]:
    """Return normalized maintenance item summaries from CMS counters."""
    counters = _counter_values(values)
    items = [
        _maintenance_item_status(counters, item)
        for item in MAINTENANCE_ITEMS
        if item.index < len(counters)
    ]
    due_items = [item["key"] for item in items if item.get("due")]
    warning_items = [item["key"] for item in items if item.get("warning")]
    return {
        "source": source,
        "available": bool(items),
        "items": items,
        "raw_cms": counters,
        "extra_values": counters[len(MAINTENANCE_ITEMS) :],
        "due": bool(due_items),
        "due_items": due_items,
        "warning": bool(warning_items),
        "warning_items": warning_items,
    }


def maintenance_item_status(
    status: Mapping[str, Any] | None,
    item: str | MaintenanceItem,
) -> dict[str, Any] | None:
    """Return one item summary from a normalized maintenance status payload."""
    if not isinstance(status, Mapping):
        return None
    target = normalize_maintenance_item(item)
    items = status.get("items")
    if not isinstance(items, Sequence) or isinstance(items, str | bytes | bytearray):
        return None
    for entry in items:
        if isinstance(entry, Mapping) and entry.get("key") == target.key:
            return dict(entry)
    return None


def maintenance_status_attributes(status: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return compact, non-secret attributes for maintenance entities."""
    if not isinstance(status, Mapping):
        return {}
    attributes = {
        "source": status.get("source"),
        "available": status.get("available"),
        "raw_cms": status.get("raw_cms"),
        "extra_values": status.get("extra_values"),
        "due": status.get("due"),
        "due_items": status.get("due_items"),
        "warning": status.get("warning"),
        "warning_items": status.get("warning_items"),
    }
    return {key: value for key, value in attributes.items() if value is not None}


def _maintenance_item_status(
    values: Sequence[int],
    item: MaintenanceItem,
) -> dict[str, Any]:
    used_minutes = int(values[item.index])
    remaining_minutes = max(item.total_minutes - used_minutes, 0)
    remaining_percent = max(
        0.0,
        round((remaining_minutes / item.total_minutes) * 100, 1),
    )
    due = used_minutes >= item.total_minutes
    warning = due or remaining_percent <= item.warning_percent
    status = "due" if due else "replace_soon" if warning else "normal"
    return {
        "key": item.key,
        "name": item.name,
        "index": item.index,
        "used_minutes": used_minutes,
        "used_hours": round(used_minutes / 60, 1),
        "remaining_minutes": remaining_minutes,
        "remaining_hours": round(remaining_minutes / 60, 1),
        "total_minutes": item.total_minutes,
        "total_hours": item.total_hours,
        "remaining_percent": remaining_percent,
        "warning_percent": item.warning_percent,
        "status": status,
        "warning": warning,
        "due": due,
    }


def _looks_like_counter_values(value: Any) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return False
    if len(value) < len(MAINTENANCE_ITEMS):
        return False
    return all(isinstance(item, int) and not isinstance(item, bool) for item in value)


def _counter_values(values: Sequence[Any]) -> list[int]:
    if not isinstance(values, Sequence) or isinstance(values, str | bytes | bytearray):
        raise ValueError("CMS counters must be a sequence of integers.")
    result: list[int] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("CMS counters must be integers.")
        result.append(value)
    return result
