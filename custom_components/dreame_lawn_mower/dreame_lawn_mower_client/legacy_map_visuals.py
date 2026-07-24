"""Presentation bridge for the legacy current-map renderer."""

from __future__ import annotations

import math
from io import BytesIO

from PIL import Image

from .map_json_renderer import DreameMowerMapDataJsonRenderer
from .map_renderer import DreameMowerMapRenderer
from .map_renderer_types import MapRendererColorScheme
from .map_types import MapData
from .map_visuals import MapRenderStyle


def render_legacy_map_png(
    map_data: MapData,
    *,
    label_scale: float = 1.0,
    style: MapRenderStyle | None = None,
) -> bytes:
    """Render legacy map data with shared presentation settings and metadata."""
    metadata_renderer = DreameMowerMapDataJsonRenderer()
    metadata_renderer.render_map(map_data)

    renderer = _legacy_renderer(style=style, label_scale=label_scale)
    image_png = renderer.render_map(map_data)
    return metadata_renderer.embed_map_data(image_png)


def _legacy_renderer(
    *,
    style: MapRenderStyle | None,
    label_scale: float,
) -> DreameMowerMapRenderer:
    """Configure the historical renderer from the shared presentation contract."""
    renderer = DreameMowerMapRenderer(cache=False)
    if style is not None:
        renderer.color_scheme = _legacy_color_scheme(style)
        renderer.presentation_stroke_scale = style.stroke_scale
        renderer.presentation_marker_scale = style.marker_scale
        if style.marker_image:
            renderer.presentation_marker_image = _marker_image(style.marker_image)
    renderer.presentation_label_scale = _normalize_label_scale(label_scale)
    return renderer


def _legacy_color_scheme(style: MapRenderStyle) -> MapRendererColorScheme:
    """Translate the shared visual language to the historical pixel renderer."""
    zone_fills = style.zone_fills or (style.background,)
    zone_outlines = style.zone_outlines or (style.boundary,)
    zone_pairs = tuple(
        [
            zone_fills[index % len(zone_fills)],
            zone_outlines[index % len(zone_outlines)],
        ]
        for index in range(max(len(zone_fills), len(zone_outlines)))
    )
    while len(zone_pairs) < 4:
        zone_pairs += (zone_pairs[0],)

    return MapRendererColorScheme(
        floor=style.background,
        outside=style.background,
        wall=style.boundary,
        passive_segment=zone_fills[0],
        hidden_segment=zone_fills[min(1, len(zone_fills) - 1)],
        new_segment=zone_fills[0],
        cleaned_area=style.mow_path,
        dirty_area=zone_fills[0],
        clean_area=zone_fills[min(1, len(zone_fills) - 1)],
        second_clean_area=style.navigation_path,
        no_go=style.forbidden_fill,
        no_go_outline=style.forbidden_outline,
        virtual_wall=style.forbidden_outline,
        pathway=style.navigation_path,
        active_area=style.spot_fill,
        active_area_outline=style.spot_outline,
        active_point=style.spot_fill,
        active_point_outline=style.spot_outline,
        path=style.mow_path,
        segment=zone_pairs[:4],
        obstacle_bg=style.point,
        icon_background=style.label_halo,
        text=style.label,
        text_stroke=style.label_halo,
        dark=_is_dark(style.background),
    )


def _marker_image(value: bytes) -> Image.Image:
    with Image.open(BytesIO(value)) as marker:
        if marker.width > 512 or marker.height > 512:
            raise ValueError("Custom map marker dimensions exceed 512 pixels.")
        marker.load()
        return marker.convert("RGBA").copy()


def _normalize_label_scale(value: float) -> float:
    if not isinstance(value, int | float) or not math.isfinite(float(value)):
        return 1.0
    return max(0.5, min(float(value), 4.0))


def _is_dark(color: tuple[int, int, int, int]) -> bool:
    red, green, blue, _alpha = color
    return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue) < 128
