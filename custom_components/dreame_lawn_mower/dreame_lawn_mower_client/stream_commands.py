"""Wire parameters shared by synchronous and native stream commands."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def stream_action_parameters(
    piid: int | None, parameters: Mapping[str, Any] | None, *,
    session: Any = None, include_session: bool = True,
) -> list[dict[str, Any]]:
    """Preserve the vendor session ordering and compact stream JSON payload."""
    payload: dict[str, Any] = {"session": session} if include_session else {}
    if parameters:
        payload.update(parameters)
    return [{
        "piid": piid,
        "value": json.dumps(payload, separators=(",", ":")).replace(" ", ""),
    }]
