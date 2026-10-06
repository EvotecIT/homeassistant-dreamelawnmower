"""Shared device payload for complete and partial map-frame requests."""

from __future__ import annotations

import json
from typing import Any

from .const import MAP_PARAMETER_VALUE, MAP_REQUEST_PARAMETER_FRAME_TYPE
from .device_types import PIID, DreameMowerProperty


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
