"""Shared device payload for complete and partial map-frame requests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from .const import MAP_PARAMETER_VALUE, MAP_REQUEST_PARAMETER_FRAME_TYPE
from .device_types import PIID, DreameMowerProperty


@dataclass(frozen=True)
class MapUpdateRequest:
    """Follow-up selected by frame application, before performing network I/O."""

    kind: Literal["base", "full", "missing", "next", "list", "changed"]
    map_id: int | None = None
    frame_id: int | None = None


def map_frame_parameters(parameters: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Retain the existing frame-info encoding for both transport owners."""
    if parameters is None:
        parameters = {MAP_REQUEST_PARAMETER_FRAME_TYPE: "I"}
    return [
        {
            "piid": PIID(DreameMowerProperty.FRAME_INFO),
            MAP_PARAMETER_VALUE: json.dumps(parameters, separators=(",", ":")).replace(
                " ", ""
            ),
        }
    ]
