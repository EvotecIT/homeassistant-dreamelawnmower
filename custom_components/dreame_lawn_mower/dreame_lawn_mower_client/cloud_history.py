"""Shared vendor history request fields and absent-result decoding."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .exceptions import DeviceException, DreameLawnMowerCloudAPIError


def history_params(
    strings: Sequence[str],
    user_id: str | None,
    did: str | None,
    country: str | None,
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


def history_result(
    response: Any,
    strings: Sequence[str],
    *,
    require_response: bool = False,
) -> Any:
    """Decode absent history, optionally requiring a successful vendor envelope."""
    if require_response:
        if not isinstance(response, Mapping):
            raise DeviceException("The history request did not return a response.")
        code = response.get("code")
        if not isinstance(code, int) or isinstance(code, bool):
            raise DeviceException("The history response had an invalid code.")
        if code != 0:
            raise DreameLawnMowerCloudAPIError(code)
    data = response.get("data") if isinstance(response, Mapping) else None
    return data.get(strings[33]) if isinstance(data, Mapping) else None
