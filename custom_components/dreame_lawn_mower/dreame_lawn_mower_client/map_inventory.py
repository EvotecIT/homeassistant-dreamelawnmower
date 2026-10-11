"""Canonical native map inventory identity for caches and view composition."""

from __future__ import annotations

import json
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
