"""Choose the whole zones a scheduled start should mow from recorded history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util


def _completed_on(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        instant = datetime.fromisoformat(value)
    except ValueError:
        return None
    if instant.tzinfo is None:
        return None
    return dt_util.as_local(instant).date()


def zone_rest_days(
    zone_id: int, rest_days: int, overrides: Mapping[int, int] | None
) -> int:
    """A zone's own rest days, or the automation default."""
    return (overrides or {}).get(zone_id, rest_days)


def zone_due_on(
    zone: Mapping[str, Any], *, rest_days: int, overrides: Mapping[int, int] | None
) -> date | None:
    """Local date a zone is due again; None when no completion is known (due now).

    The scheduled start, its preview and the per-zone table all use this.
    """
    zone_id = zone.get("zone_id")
    completed = _completed_on(zone.get("last_completed_at"))
    if type(zone_id) is not int or completed is None:
        return None
    return completed + timedelta(days=zone_rest_days(zone_id, rest_days, overrides) + 1)


def due_zone_ids(
    overview: Sequence[Mapping[str, Any]],
    *,
    rest_days: int,
    max_area: float,
    today: date,
    zone_rest_days_by_id: Mapping[int, int] | None = None,
) -> list[int]:
    """Return due zones, most overdue first, bounded by the area of one start.

    ``overview`` is the least-recently-mowed order. A zone is due when no
    completion is known, or when its rest days have passed since its last
    completion (0 = once per day); a zone may override the default rest days.
    Zones without a completion come first, then the earliest due date; on the
    same due date the zone with fewer rest days leads, then overview order.
    With one rest-days value for every zone this is the overview order.
    Zones are never split. The first due zone is always kept so a zone larger
    than ``max_area`` is not starved; any other zone needs a known area that
    still fits. ``max_area`` 0 = no limit.
    """
    due: list[tuple[bool, date, int, int, Mapping[str, Any]]] = []
    for position, zone in enumerate(overview):
        zone_id = zone.get("zone_id")
        if type(zone_id) is not int:
            continue
        due_on = zone_due_on(zone, rest_days=rest_days, overrides=zone_rest_days_by_id)
        if due_on is not None and due_on > today:
            continue
        due.append(
            (
                due_on is not None,
                due_on or date.min,
                zone_rest_days(zone_id, rest_days, zone_rest_days_by_id),
                position,
                zone,
            )
        )
    due.sort(key=lambda item: item[:4])
    selected: list[int] = []
    total = 0.0
    for *_, zone in due:
        zone_id = zone["zone_id"]
        area = zone.get("area_m2")
        known = (
            float(area)
            if isinstance(area, int | float) and not isinstance(area, bool) and area > 0
            else None
        )
        if max_area > 0 and selected and (known is None or total + known > max_area):
            continue
        selected.append(zone_id)
        if max_area > 0 and known is None:
            break
        total += known or 0.0
    return selected


def rotation_zones(
    overview: Sequence[Mapping[str, Any]],
    *,
    rest_days: int,
    overrides: Mapping[int, int] | None,
) -> list[dict[str, Any]]:
    """Per-zone rest days and next due date, for dashboards."""
    result: list[dict[str, Any]] = []
    for zone in overview:
        zone_id = zone.get("zone_id")
        if type(zone_id) is not int:
            continue
        due_on = zone_due_on(zone, rest_days=rest_days, overrides=overrides)
        result.append(
            {
                "zone_id": zone_id,
                "label": zone.get("label"),
                "area_m2": zone.get("area_m2"),
                "rest_days": zone_rest_days(zone_id, rest_days, overrides),
                "custom_rest_days": zone_id in (overrides or {}),
                "last_completed_at": zone.get("last_completed_at"),
                "due_on": due_on.isoformat() if due_on else None,
            }
        )
    return result
