"""Shared position selection for client map consumers."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .position_tracking import MowerPosition, map_position_identity, snapshot_is_docked
from .vector_map import position_within_vector_map


def vector_map_position(client: Any, vector_map: Any) -> MowerPosition | None:
    """Resolve the same geometry-scoped evidence as the interactive overlay."""
    return client._position_tracker.resolve(
        map_index=vector_map.map_index,
        geometry=map_position_identity(vector_map),
        contains=lambda x, y: position_within_vector_map(vector_map, (x, y)),
        snapshot=client._latest_snapshot,
    )


def apply_position_metadata(
    position: MowerPosition | None,
    *,
    snapshot: Any,
    details: dict[str, Any],
    summary: Any,
) -> Any:
    """Keep physical docking distinct from having drawable dock coordinates."""
    details["docked"] = snapshot_is_docked(snapshot)
    details["position_status"] = "unavailable"
    details["position_source"] = None
    details["position_observed_at"] = None
    details["position_x"] = None
    details["position_y"] = None
    details["position_heading"] = None
    if position is not None:
        details.update(position.details())
        details.update(
            position_x=position.x,
            position_y=position.y,
            position_heading=position.heading,
        )
    if summary is not None:
        return replace(
            summary,
            robot_present=position is not None,
            charger_present=position is not None and position.status == "known_dock",
        )
    return summary
