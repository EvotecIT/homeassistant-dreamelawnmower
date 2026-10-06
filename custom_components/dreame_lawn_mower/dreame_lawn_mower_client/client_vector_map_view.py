"""Shared vector-map decoding and rendering from already-fetched cloud data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .client_map_helpers import _coordinate_path_length_m
from .client_position import apply_position_metadata, vector_map_position
from .models import DreameLawnMowerMapView
from .vector_map import (
    filter_runtime_track_segments,
    parse_batch_vector_map,
    render_vector_map_png,
    vector_map_to_details,
    vector_map_to_summary,
)

if TYPE_CHECKING:
    from .client import DreameLawnMowerClient
    from .map_visuals import MapRenderStyle


def vector_map_details(
    batch_data: Mapping[str, Any] | None,
    current_map_index: int | None,
) -> dict[str, Any]:
    vector_map = parse_batch_vector_map(batch_data, current_map_index=current_map_index)
    if vector_map is None:
        return {
            "available": False,
            "source": "batch_vector_map",
            "error": "No vector map data returned by the batch map path.",
        }
    details = vector_map_to_details(vector_map)
    details["available"] = True
    details["source"] = "batch_vector_map"
    return details


def vector_map_view(
    client: DreameLawnMowerClient,
    batch_data: Mapping[str, Any] | None,
    *,
    current_map_index: int | None,
    label_scale: float,
    style: MapRenderStyle | None,
) -> DreameLawnMowerMapView:
    """Decode and render while the caller owns the device state lock."""
    source = "batch_vector_map"
    vector_map = parse_batch_vector_map(
        batch_data,
        current_map_index=current_map_index,
    )
    if vector_map is None:
        return DreameLawnMowerMapView(
            source=source,
            error="No vector map data returned by the batch map path.",
            diagnostics=client._safe_map_diagnostics(
                source=source,
                reason="batch_vector_map_empty",
            ),
        )

    client._expire_runtime_live_tracking()
    runtime_blob = client._latest_runtime_status_blob
    if client._runtime_session_active is False:
        vector_map.mow_paths = ()
    summary = vector_map_to_summary(vector_map)
    details = vector_map_to_details(vector_map)
    details["render_rotation"] = style.rotation if style else 0
    runtime_context_matches = client._runtime_live_map_index == vector_map.map_index
    runtime_track_segments = (
        filter_runtime_track_segments(
            vector_map,
            client._runtime_live_track_segments,
        )
        if runtime_context_matches
        else ()
    )
    runtime_track_point_count = sum(len(segment) for segment in runtime_track_segments)
    runtime_pose_x = getattr(runtime_blob, "candidate_runtime_pose_x", None)
    runtime_pose_y = getattr(runtime_blob, "candidate_runtime_pose_y", None)
    position = vector_map_position(client, vector_map)
    runtime_position = (position.x, position.y) if position is not None else None
    runtime_position_valid = position is not None and position.status == "current"
    summary = apply_position_metadata(
        position, snapshot=client._latest_snapshot, details=details, summary=summary
    )
    if runtime_pose_x is not None and runtime_pose_y is not None:
        details["runtime_pose_x"] = runtime_pose_x
        details["runtime_pose_y"] = runtime_pose_y
        details["runtime_position_valid"] = runtime_position_valid
        details["runtime_heading_deg"] = getattr(
            runtime_blob,
            "candidate_runtime_heading_deg",
            None,
        )
        details["runtime_region_id"] = getattr(
            runtime_blob,
            "candidate_runtime_region_id",
            None,
        )
        details["runtime_position_updated_at"] = getattr(
            runtime_blob,
            "received_at",
            None,
        )
    if runtime_track_point_count:
        details["runtime_track_segment_count"] = len(runtime_track_segments)
        details["runtime_track_point_count"] = runtime_track_point_count
        details["runtime_track_length_m"] = round(
            sum(
                _coordinate_path_length_m(segment) for segment in runtime_track_segments
            ),
            2,
        )
        details["has_live_path"] = True
        if summary is not None:
            summary = replace(
                summary,
                path_point_count=summary.path_point_count + runtime_track_point_count,
            )
    try:
        image_png = render_vector_map_png(
            vector_map,
            label_scale=label_scale,
            runtime_track_segments=runtime_track_segments,
            runtime_position=runtime_position,
            position_status=(
                position.status if position is not None else "unavailable"
            ),
            style=style,
        )
    except Exception as err:  # noqa: BLE001 - diagnostics path
        return DreameLawnMowerMapView(
            source=source,
            summary=summary,
            details=details,
            error=f"Failed to render vector map data: {err}",
            diagnostics=client._safe_map_diagnostics(
                source=source,
                reason="batch_vector_map_render_failed",
            ),
        )

    if image_png is None:
        return DreameLawnMowerMapView(
            source=source,
            summary=summary,
            details=details,
            error="Vector map renderer did not produce an image.",
            diagnostics=client._safe_map_diagnostics(
                source=source,
                reason="batch_vector_map_render_empty",
            ),
        )

    return DreameLawnMowerMapView(
        source=source,
        summary=summary,
        image_png=image_png,
        details=details,
        diagnostics=client._safe_map_diagnostics(
            source=source,
            reason="batch_vector_map_rendered",
        ),
    )
