"""Shared vector-map decoding and rendering from already-fetched cloud data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .app_map_geometry import parse_app_map_geometry
from .client_map_helpers import (
    _app_map_view_details,
    _app_maps_view_metadata,
    _coordinate_path_length_m,
    _select_app_map_payload,
)
from .client_position import apply_position_metadata, vector_map_position
from .models import DreameLawnMowerMapView
from .position_tracking import map_position_identity
from .vector_map import (
    DreameLawnMowerVectorMap,
    filter_runtime_track_segments,
    parse_batch_vector_map,
    render_vector_map_png,
    vector_map_to_details,
    vector_map_to_summary,
)

if TYPE_CHECKING:
    from .client_maps import _DreameLawnMowerClientMapsMixin
    from .map_visuals import MapRenderStyle


def vector_map_details(
    batch_data: Mapping[str, Any] | None,
    current_map_index: int | None,
    *,
    client: _DreameLawnMowerClientMapsMixin | None = None,
    app_maps: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    vector_map = parse_batch_vector_map(batch_data, current_map_index=current_map_index)
    source = "batch_vector_map"
    if vector_map is None:
        vector_map = parse_app_map_geometry(app_maps, current_map_index)
        source = "app_action_map"
    if vector_map is None:
        return {
            "available": False,
            "source": "batch_vector_map",
            "error": "No vector map data returned by the batch map path.",
        }
    details = (
        dict(geometry_map_view(
            client, vector_map, source=source, render_image=False, app_maps=app_maps,
        ).details or {}) if client is not None else vector_map_to_details(vector_map)
    )
    if source == "app_action_map":
        # Native geometry is usable without claiming batch vector availability.
        details.update(available_map_count=0, available_maps=[], maps=[])
        if client is None:
            details["has_live_path"] = False
    else:
        for entry in details.get("maps", []):
            if entry.get("map_index") == vector_map.map_index:
                entry.update({
                    key: value for key, value in details.items()
                    if key.startswith("runtime_")
                })
    details["available"] = True
    details["source"] = source
    return details


def vector_map_view(
    client: _DreameLawnMowerClientMapsMixin,
    batch_data: Mapping[str, Any] | None,
    *,
    current_map_index: int | None,
    label_scale: float,
    style: MapRenderStyle | None,
    app_maps: Mapping[str, Any] | None = None,
) -> DreameLawnMowerMapView:
    """Decode and render while the caller owns the device state lock."""
    source = "batch_vector_map"
    vector_map = parse_batch_vector_map(
        batch_data,
        current_map_index=current_map_index,
    )
    if vector_map is None:
        vector_map = parse_app_map_geometry(app_maps, current_map_index)
        source = "app_action_map" if vector_map is not None else source
    if vector_map is None:
        return DreameLawnMowerMapView(
            source=source,
            error="No vector map data returned by the batch map path.",
            diagnostics=client._safe_map_diagnostics(
                source=source,
                reason="batch_vector_map_empty",
            ),
        )

    return geometry_map_view(
        client, vector_map, source=source, label_scale=label_scale, style=style,
        app_maps=app_maps,
    )


def geometry_map_view(
    client: _DreameLawnMowerClientMapsMixin,
    vector_map: DreameLawnMowerVectorMap,
    *,
    source: str,
    label_scale: float = 1.0,
    style: MapRenderStyle | None = None,
    render_image: bool = True,
    app_maps: Mapping[str, Any] | None = None,
) -> DreameLawnMowerMapView:
    """Share position validation, metrics and rendering across map transports."""
    client._expire_runtime_live_tracking()
    client._bind_runtime_live_geometry(
        vector_map.map_index, map_position_identity(vector_map),
    )
    runtime_blob = client._latest_runtime_status_blob
    if client._runtime_session_active is False:
        vector_map.mow_paths = ()
    summary = vector_map_to_summary(vector_map)
    details = vector_map_to_details(vector_map)
    app_metadata = None
    if source == "app_action_map" and app_maps is not None:
        selected = _select_app_map_payload(app_maps)
        if selected is not None:
            details = {
                **_app_map_view_details(selected, selected.get("payload")),
                **details,
            }
        app_metadata = _app_maps_view_metadata(app_maps)
    if source == "app_action_map":
        # Saved native trajectories include mapping/boundary passes. Only the
        # validated runtime segments below can establish a live mowing trail.
        details["has_live_path"] = False
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
    runtime_pose_x = getattr(runtime_blob, "candidate_runtime_pose_x", None)
    runtime_pose_y = getattr(runtime_blob, "candidate_runtime_pose_y", None)
    position = vector_map_position(client, vector_map)
    runtime_position = (position.x, position.y) if position is not None else None
    runtime_position_valid = position is not None and position.status == "current"
    if source == "app_action_map" and not runtime_position_valid:
        runtime_track_segments = ()
    runtime_track_point_count = sum(len(segment) for segment in runtime_track_segments)
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
    if not render_image:
        return DreameLawnMowerMapView(
            source=source, summary=summary, details=details, app_maps=app_metadata,
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
            app_maps=app_metadata,
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
            app_maps=app_metadata,
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
        app_maps=app_metadata,
        diagnostics=client._safe_map_diagnostics(
            source=source,
            reason="batch_vector_map_rendered",
        ),
    )
