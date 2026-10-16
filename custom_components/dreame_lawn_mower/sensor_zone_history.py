"""Per-zone mowing history read from finished task session summaries."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import SensorEntity

from .control_options import current_zone_entries
from .dreame_lawn_mower_client.session_photos import (
    ZoneMowingRecord,
    latest_zone_mowing,
)
from .entity import DreameLawnMowerEntity


def _iso(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def zone_mowing_overview(
    zones: list[dict[str, Any]],
    sessions: Mapping[str, tuple[ZoneMowingRecord, ...]] | None,
) -> list[dict[str, Any]]:
    """Return the current map's zones, least recently completed first.

    A zone with no completed mowing in the recent sessions comes first, in zone
    order; the others follow from the oldest completion to the newest.
    """
    latest = latest_zone_mowing(sessions or {})
    overview: list[dict[str, Any]] = []
    for zone in zones:
        zone_id = zone.get("area_id")
        if not isinstance(zone_id, int):
            continue
        records = latest.get(zone_id, {})
        completed = records.get("completed")
        last = records.get("latest")
        overview.append(
            {
                "zone_id": zone_id,
                "label": zone.get("label"),
                "last_completed_at": _iso(
                    completed.ended_at if completed is not None else None
                ),
                "last_status": last.status if last is not None else None,
                "last_session_at": _iso(
                    (last.ended_at or last.started_at) if last is not None else None
                ),
                "_order": (
                    completed is not None,
                    (completed.ended_at or 0) if completed is not None else 0,
                    zone_id,
                ),
            }
        )
    overview.sort(key=lambda item: item.pop("_order"))
    return overview


class DreameLawnMowerLeastRecentlyMowedZoneSensor(DreameLawnMowerEntity, SensorEntity):
    """Name the zone whose last completed mowing is the oldest."""

    _attr_name = "Least Recently Mowed Zone"
    _attr_icon = "mdi:calendar-clock-outline"

    def __init__(self, coordinator: Any) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._descriptor.unique_id}_least_recently_mowed_zone"

    def _overview(self) -> list[dict[str, Any]]:
        coordinator = self.coordinator
        zones = current_zone_entries(
            coordinator.batch_device_data,
            coordinator.app_maps,
            getattr(coordinator, "vector_map_details", None),
            selected_map_index=coordinator.selected_map_index,
        )
        return zone_mowing_overview(
            zones, getattr(coordinator, "zone_mowing_sessions", None)
        )

    @property
    def available(self) -> bool:
        return (
            self.coordinator.data is not None
            and getattr(self.coordinator, "zone_mowing_sessions", None) is not None
            and bool(self._overview())
        )

    @property
    def native_value(self) -> str | None:
        overview = self._overview()
        return overview[0]["label"] if overview else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        overview = self._overview()
        first = overview[0] if overview else {}
        return {
            "zone_id": first.get("zone_id"),
            "last_completed_at": first.get("last_completed_at"),
            "zones": overview,
        }
