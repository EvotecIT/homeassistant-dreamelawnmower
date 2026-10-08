"""Shared app-map rendering and map-source selection from fetched data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .client_map_helpers import (
    _app_map_view_details,
    _app_map_view_summary,
    _app_maps_view_metadata,
    _map_view_current_app_map_index,
    _map_view_has_live_path,
    _render_app_map_payload_png,
    _select_app_map_payload,
)
from .models import DreameLawnMowerMapView

if TYPE_CHECKING:
    from .client_maps import _DreameLawnMowerClientMapsMixin
    from .map_visuals import MapRenderStyle


def app_map_view(
    client: _DreameLawnMowerClientMapsMixin,
    app_maps: dict[str, Any],
    *,
    legacy_error: str | None,
    legacy_reason: str,
    label_scale: float,
    style: MapRenderStyle | None,
) -> DreameLawnMowerMapView:
    source = "app_action_map"
    selected = _select_app_map_payload(app_maps)
    if selected is None:
        error = legacy_error or "No app-map payload was returned."
        return DreameLawnMowerMapView(
            source=source,
            error=error,
            app_maps=_app_maps_view_metadata(app_maps),
            diagnostics=client._safe_map_diagnostics(
                source=source,
                reason=legacy_reason,
            ),
        )
    payload = selected.get("payload")
    image_png, width, height = _render_app_map_payload_png(
        payload,
        label_scale=label_scale,
        style=style,
    )
    return DreameLawnMowerMapView(
        source=source,
        summary=_app_map_view_summary(selected, payload, width, height),
        image_png=image_png,
        details={
            **_app_map_view_details(selected, payload),
            "render_rotation": style.rotation if style else 0,
        },
        app_maps=_app_maps_view_metadata(app_maps),
        diagnostics=client._safe_map_diagnostics(
            source=source,
            reason="app_action_map_rendered",
        ),
    )


def preferred_map_view(
    client: _DreameLawnMowerClientMapsMixin,
    app_view: DreameLawnMowerMapView,
    vector_view: DreameLawnMowerMapView,
) -> DreameLawnMowerMapView | None:
    """Prefer live data, retaining known app-slot identity before legacy fallback."""
    if _map_view_has_live_path(vector_view) or (
        isinstance(vector_view.details, Mapping)
        and vector_view.details.get("position_status")
        in {"current", "known_dock", "last_known"}
    ):
        return vector_view

    if app_view.available and app_view.image_png is not None:
        return client._with_runtime_position_details(app_view, vector_view)

    if vector_view.available and vector_view.image_png is not None:
        return vector_view

    if _map_view_current_app_map_index(app_view) is not None:
        # Legacy snapshots cannot establish app-slot identity. Preserve the
        # current-map error instead of showing an unverifiable older lawn.
        return app_view

    return None
