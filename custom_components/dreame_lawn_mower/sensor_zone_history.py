"""Per-zone mowing history read from finished task session summaries."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity

from .control_options import current_zone_entries
from .dreame_lawn_mower_client.zone_mowing_history import (
    ZoneMowingRecord,
    latest_zone_mowing,
    map_revision_hash,
)
from .entity import DreameLawnMowerEntity


def _iso(timestamp: int | None) -> str | None:
    if type(timestamp) is not int or not 0 <= timestamp <= 253402300799:
        return None
    return (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=timestamp)).isoformat()


def selected_map_revision(app_maps: Any, map_index: Any) -> str | None:
    """Require one current verified slot and an unambiguous revision hash."""
    if (
        not isinstance(app_maps, Mapping)
        or app_maps.get("map_list_valid") is not True
        or type(map_index) is not int
        or app_maps.get("current_map_index") != map_index
    ):
        return None
    entries = app_maps.get("maps")
    if not isinstance(entries, list):
        return None
    selected = [
        entry
        for entry in entries
        if isinstance(entry, Mapping)
        and type(entry.get("idx")) is int
        and entry["idx"] == map_index
    ]
    if len(selected) != 1:
        return None
    entry = selected[0]
    if any(
        entry.get(flag) is not True for flag in ("created", "available", "hash_match")
    ):
        return None
    revision = map_revision_hash(entry.get("md5"))
    info = entry.get("info")
    if (
        revision is None
        or not isinstance(info, Mapping)
        or map_revision_hash(info.get("hash")) != revision
    ):
        return None
    matching = [
        item
        for item in entries
        if isinstance(item, Mapping)
        and item.get("created") is True
        and isinstance(item.get("info"), Mapping)
        and map_revision_hash(item["info"].get("hash")) == revision
    ]
    return revision if len(matching) == 1 else None


def zone_mowing_overview(
    zones: list[dict[str, Any]],
    sessions: Mapping[str, tuple[ZoneMowingRecord, ...]] | None,
    *,
    map_hash: str | None = None,
) -> list[dict[str, Any]]:
    """Return the current map's zones, least recently completed first.

    A zone with no completed mowing in the recent sessions comes first, in zone
    order; the others follow from the oldest completion to the newest.
    """
    latest = latest_zone_mowing(sessions or {}, map_hash=map_hash)
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
    """Prioritize unknown zones, then the oldest confirmed mowing completion."""

    _attr_translation_key = "least_recently_mowed_zone"
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
        history = coordinator.zone_mowing_sessions
        return zone_mowing_overview(
            zones,
            history.sessions if history is not None else None,
            map_hash=selected_map_revision(
                coordinator.app_maps, coordinator.selected_map_index
            ),
        )

    @property
    def available(self) -> bool:
        history = self.coordinator.zone_mowing_sessions
        overview = self._overview()
        return (
            self.coordinator.data is not None
            and history is not None
            and history.complete
            and history.attributable
            and bool(overview)
            # Require a confirmed completion on this revision, then prioritize
            # unknown zones so missing history does not permanently exclude them.
            # Unknown is a selection policy, not a claim about their actual dates.
            and any(item["last_completed_at"] is not None for item in overview)
        )

    @property
    def native_value(self) -> str | None:
        overview = self._overview()
        return overview[0]["label"] if self.available and overview else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        overview = self._overview()
        first = overview[0] if overview else {}
        history = self.coordinator.zone_mowing_sessions
        return {
            "zone_id": first.get("zone_id"),
            "last_completed_at": first.get("last_completed_at"),
            "zones": overview,
            "history_complete": history.complete if history is not None else False,
            "history_attributable": history.attributable
            if history is not None
            else False,
            "observed_sessions": len(history.sessions) if history is not None else 0,
            "unknown_zone_ids": [
                item["zone_id"]
                for item in overview
                if item["last_completed_at"] is None
            ],
        }
