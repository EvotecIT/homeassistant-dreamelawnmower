"""Choose the whole zones a scheduled start should mow from recorded history."""

from __future__ import annotations

from collections.abc import Sequence
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


def due_zone_ids(
    overview: Sequence[dict[str, Any]],
    *,
    rest_days: int,
    max_area: float,
    today: date,
) -> list[int]:
    """Return due zones in overview order, bounded by the area of one start.

    ``overview`` is the least-recently-mowed order: zones without a confirmed
    completion first, then the oldest completion. A zone is due when no
    completion is known, or when ``rest_days`` full days have passed since its
    last completion (0 = once per day). Zones are never split. The first due
    zone is always kept so a zone larger than ``max_area`` is not starved; any
    other zone needs a known area that still fits. ``max_area`` 0 = no limit.
    """
    selected: list[int] = []
    total = 0.0
    for zone in overview:
        zone_id = zone.get("zone_id")
        if type(zone_id) is not int:
            continue
        completed = _completed_on(zone.get("last_completed_at"))
        if completed is not None and completed + timedelta(days=rest_days + 1) > today:
            continue
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
