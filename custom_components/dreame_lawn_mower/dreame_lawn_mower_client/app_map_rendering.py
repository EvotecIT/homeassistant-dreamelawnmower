"""Render mower-native app map records without inventing closing edges."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from io import BytesIO
from typing import Any

from .map_drawing import draw_lawn_polygon, draw_navigation_path
from .map_geometry import polygon_label_point, rotate_canvas_point
from .map_visuals import (
    MapRenderStyle,
    line_width,
    map_font,
    map_render_style,
    marker_radius,
    project_dreame_app_point,
)


def render_app_map_payload_png(
    payload: Any,
    *,
    label_scale: float = 1.0,
    style: MapRenderStyle | None = None,
) -> tuple[bytes, int, int]:
    """Render a mower-native app map payload to PNG bytes."""
    return _render_app_map_payload_png(
        payload,
        label_scale=label_scale,
        style=style,
    )


def _render_app_map_payload_png(
    payload: Any,
    *,
    label_scale: float = 1.0,
    style: MapRenderStyle | None = None,
) -> tuple[bytes, int, int]:
    if not isinstance(payload, Mapping):
        raise ValueError("App map payload is missing.")
    style = style or map_render_style()

    all_map_entries = _app_map_coordinate_entries(payload.get("map"), "Zone")
    map_entries = [entry for entry in all_map_entries if not entry["pathway"]]
    pathway_entries = [entry for entry in all_map_entries if entry["pathway"]]
    spot_entries = _app_map_coordinate_entries(payload.get("spot"), "Spot")
    map_polygons = [entry["points"] for entry in map_entries]
    pathway_paths = [entry["points"] for entry in pathway_entries]
    spot_polygons = [entry["points"] for entry in spot_entries]
    trajectories = _app_map_coordinate_sets(payload.get("trajectory"))
    points = _app_map_points(payload.get("point"))
    all_points = [
        point
        for group in [
            *map_polygons,
            *pathway_paths,
            *spot_polygons,
            *trajectories,
            points,
        ]
        for point in group
    ]
    if not all_points:
        raise ValueError("App map payload does not contain drawable coordinates.")

    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_y = max(point[1] for point in all_points)
    span_x = max(max_x - min_x, 1)
    span_y = max(max_y - min_y, 1)
    padding = 48
    canvas = 900
    scale = min((canvas - padding * 2) / span_x, (canvas - padding * 2) / span_y)
    width = max(int(span_x * scale) + padding * 2, 320)
    height = max(int(span_y * scale) + padding * 2, 320)
    native_width, native_height = width, height
    if style.rotation in (90, 270):
        width, height = height, width

    def project(point: tuple[float, float]) -> tuple[int, int]:
        x, y = point
        px, py = project_dreame_app_point(
            x,
            y,
            max_x=max_x,
            min_y=min_y,
            scale=scale,
            padding=padding,
        )
        return rotate_canvas_point(
            (px, py), native_width, native_height, style.rotation
        )

    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (width, height), style.background)
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for path in pathway_paths:
        projected = [project(point) for point in path]
        draw_navigation_path(overlay, projected, style)

    for index, polygon in enumerate(sorted(map_polygons, key=len, reverse=True)):
        projected = [project(point) for point in polygon]
        if len(projected) >= 3:
            draw_lawn_polygon(overlay, projected, index, style)

    if style.spot_area_style != "hidden":
        for polygon in spot_polygons:
            projected = [project(point) for point in polygon]
            if len(projected) >= 3:
                draw.polygon(
                    projected,
                    fill=(
                        style.spot_fill if style.spot_area_style == "filled" else None
                    ),
                    outline=style.spot_outline,
                )
                draw.line(
                    projected + [projected[0]],
                    fill=style.spot_outline,
                    width=line_width(style, 3),
                )

    if style.mowing_path_style != "hidden":
        # Saved app trajectories include mapping/boundary passes. They are not
        # live mowing evidence and must respect the historical-path setting.
        history = Image.new("RGBA", image.size, (0, 0, 0, 0))
        history_draw = ImageDraw.Draw(history)
        subtle = style.mowing_path_style == "subtle"
        history_color = (
            (*style.mow_path[:3], min(style.mow_path[3], 72))
            if subtle
            else style.mow_path
        )
        for trajectory in trajectories:
            projected = [project(point) for point in trajectory]
            if len(projected) >= 2:
                history_draw.line(
                    projected,
                    fill=history_color,
                    width=line_width(style, 1 if subtle else 2),
                    joint="curve",
                )
        overlay.alpha_composite(history)

    for point in points:
        x, y = project(point)
        radius = marker_radius(style, 6)
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=style.point,
        )

    font = _app_map_label_font(label_scale)
    visible_spot_entries = spot_entries if style.spot_area_style != "hidden" else []
    for entry in [*map_entries, *visible_spot_entries]:
        label = entry.get("label")
        polygon = entry.get("points")
        if not isinstance(label, str) or not polygon:
            continue
        center = tuple(
            round(value)
            for value in polygon_label_point([project(point) for point in polygon])
        )
        _draw_app_map_label(draw, center, label, font, style=style)

    image = Image.alpha_composite(image, overlay).convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), width, height


def _app_map_coordinate_entries(
    value: Any,
    label_prefix: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        data = item.get("data") if isinstance(item, Mapping) else item
        points = _app_map_points(data)
        if points:
            result.append(
                {
                    "points": points,
                    "label": _app_map_entry_label(item, label_prefix),
                    "pathway": _is_app_map_pathway(item),
                }
            )
    return result


def _is_app_map_pathway(item: Any) -> bool:
    """Return whether a map record is a navigation path rather than a lawn."""
    if not isinstance(item, Mapping):
        return False
    entry_id = item.get("id")
    return item.get("type") == 1 or (
        isinstance(entry_id, int)
        and not isinstance(entry_id, bool)
        and 200 <= entry_id < 300
    )


def _app_map_coordinate_sets(value: Any) -> list[list[tuple[float, float]]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    result: list[list[tuple[float, float]]] = []
    for item in value:
        data = item.get("data") if isinstance(item, Mapping) else item
        points = _app_map_points(data)
        if points:
            result.append(points)
    return result


def _app_map_entry_label(item: Any, label_prefix: str) -> str:
    if not isinstance(item, Mapping):
        return label_prefix

    name = item.get("name")
    if isinstance(name, str) and name.strip():
        label = name.strip()
    else:
        entry_id = item.get("id")
        label = (
            f"{label_prefix} #{entry_id}"
            if entry_id not in (None, "")
            else label_prefix
        )

    area = _app_map_area_label(item.get("area"))
    return f"{label}\n{area}" if area else label


def _app_map_area_label(value: Any) -> str | None:
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
    ):
        return None
    if value >= 100:
        area = f"{value:.0f}"
    else:
        area = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{area} m²"


def _app_map_polygon_center(
    points: Sequence[tuple[float, float]],
) -> tuple[float, float]:
    return polygon_label_point(points)


def _app_map_label_font(label_scale: float) -> Any:
    size = max(8, int(round(18 * _normalize_app_map_label_scale(label_scale))))
    return map_font(size, bold=True)


def _normalize_app_map_label_scale(label_scale: float) -> float:
    if not isinstance(label_scale, int | float) or math.isnan(float(label_scale)):
        return 1.0
    return max(0.5, min(float(label_scale), 4.0))


def _draw_app_map_label(
    draw: Any,
    center: tuple[int, int],
    label: str,
    font: Any,
    *,
    style: MapRenderStyle,
) -> None:
    for offset_x, offset_y in (
        (-2, 0),
        (2, 0),
        (0, -2),
        (0, 2),
        (-1, -1),
        (1, -1),
        (-1, 1),
        (1, 1),
    ):
        draw.multiline_text(
            (center[0] + offset_x, center[1] + offset_y),
            label,
            fill=style.label_halo,
            font=font,
            anchor="mm",
            align="center",
            spacing=2,
        )
    draw.multiline_text(
        center,
        label,
        fill=style.label,
        font=font,
        anchor="mm",
        align="center",
        spacing=2,
    )


def _app_map_points(value: Any) -> list[tuple[float, float]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    points: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, Sequence) or isinstance(item, str | bytes | bytearray):
            continue
        if len(item) < 2:
            continue
        x, y = item[0], item[1]
        if (
            isinstance(x, int | float)
            and not isinstance(x, bool)
            and isinstance(y, int | float)
            and not isinstance(y, bool)
            and math.isfinite(x)
            and math.isfinite(y)
        ):
            points.append((float(x), float(y)))
    return points
