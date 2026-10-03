"""Zone labels from verified mower-native map records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def app_map_zone_metadata(zone_records: Sequence[Any]) -> list[dict[str, Any]]:
    """Summarize lawn records without exposing their coordinates."""
    result = []
    for record in zone_records:
        if not isinstance(record, Mapping):
            continue
        zone_id = record.get("id")
        if type(zone_id) is not int or not 0 < zone_id < 200:
            continue
        name = record.get("name")
        result.append(
            {
                "zone_id": zone_id,
                "name": name.strip()[:200]
                if isinstance(name, str) and name.strip()
                else None,
            }
        )
    return result


def verified_app_map_zone_names(
    app_maps: Any,
    map_index: int | None,
) -> dict[int, str | None]:
    """Use labels only from one created, hash-verified matching native map.

    Callers retain their existing zone membership. An explicit empty name
    overrides a stale cloud name, while unavailable native names use fallback.
    """
    if not isinstance(app_maps, Mapping) or app_maps.get("map_list_valid") is not True:
        return {}
    maps = app_maps.get("maps")
    if not isinstance(maps, Sequence) or isinstance(maps, str | bytes | bytearray):
        return {}
    candidates = [
        entry
        for entry in maps
        if isinstance(entry, Mapping)
        and type(entry.get("idx")) is int
        and entry["idx"] == map_index
    ]
    if len(candidates) != 1:
        return {}
    entry = candidates[0]
    if any(
        entry.get(flag) is not True for flag in ("created", "available", "hash_match")
    ):
        return {}
    summary = entry.get("summary")
    zones = summary.get("zones") if isinstance(summary, Mapping) else None
    if not isinstance(zones, list):
        return {}
    result: dict[int, str | None] = {}
    for zone in zones:
        if not isinstance(zone, Mapping):
            return {}
        zone_id, name = zone.get("zone_id"), zone.get("name")
        if (
            type(zone_id) is not int
            or not 0 < zone_id < 200
            or zone_id in result
            or (name is not None and (not isinstance(name, str) or len(name) > 200))
        ):
            return {}
        result[zone_id] = name.strip() or None if name else None
    return result
