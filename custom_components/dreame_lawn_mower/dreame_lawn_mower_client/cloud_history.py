"""Shared vendor history request fields and absent-result decoding."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def history_params(
    strings: Sequence[str],
    user_id: str | None,
    did: str | None,
    country: str,
    key: str,
    kind: str,
    limit: int,
    time_start: int,
) -> dict[str, Any]:
    parts = key.split(".")
    field = "eiid" if kind == "event" else "aiid" if kind == "action" else "piid"
    return {
        "uid": str(user_id),
        "did": str(did),
        "from": time_start or 1687019188,
        "limit": limit,
        "siid": parts[0],
        strings[21]: country,
        strings[42]: 3,
        field: parts[1],
    }


def history_result(response: Any, strings: Sequence[str]) -> Any:
    data = response.get("data") if isinstance(response, Mapping) else None
    return data.get(strings[33]) if isinstance(data, Mapping) else None
