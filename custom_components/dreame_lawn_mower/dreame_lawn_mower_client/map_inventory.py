"""Canonical native map inventory identity for caches and view composition."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .payload_utils import _json_safe


def _app_map_inventory_identity(
    maps: Sequence[Mapping[str, Any]],
) -> str | None:
    """Return a stable identity only when every created map has MAPI data."""
    if not maps:
        return None
    inventory: list[dict[str, Any]] = []
    for item in maps:
        created = bool(item.get("created"))
        info = item.get("info")
        map_hash = info.get("hash") if isinstance(info, Mapping) else None
        map_size = info.get("size") if isinstance(info, Mapping) else None
        if created and (
            not isinstance(map_hash, str)
            or not map_hash
            or isinstance(map_size, bool)
            or not isinstance(map_size, int)
            or map_size <= 0
        ):
            return None
        inventory.append(
            {
                "idx": _json_safe(item.get("idx"), max_depth=1),
                "current": bool(item.get("current")),
                "created": created,
                "hash": map_hash if created else None,
                "size": map_size if created else None,
            }
        )
    return json.dumps(
        inventory,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


_MAP_HASH = re.compile(r"[0-9a-fA-F]{32}")


def map_revision_hash(value: Any) -> str | None:
    """Accept an explicit map revision, never a zone number as map identity."""
    return (
        value.lower() if isinstance(value, str) and _MAP_HASH.fullmatch(value) else None
    )



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
