"""Consistent lawn fills, pathways, and markers for local mower maps."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from math import hypot

from PIL import Image, ImageDraw

from .map_visuals import MapRenderStyle, line_width, map_render_style


def render_legacy_navigation_paths(
    paths, color, layer_size, dimensions, stroke_scale, scale
) -> Image.Image:
    """Adapt legacy line records to the shared road painter in output pixels."""
    image = Image.new("RGBA", layer_size, (0, 0, 0, 0))
    style = replace(
        map_render_style(),
        navigation_path=color,
        stroke_scale=stroke_scale * scale,
    )
    for path in paths:
        point = path.to_img(dimensions)
        draw_navigation_path(
            image,
            [
                (point.x0 * scale, point.y0 * scale),
                (point.x1 * scale, point.y1 * scale),
            ],
            style,
        )
    return image


def draw_lawn_polygon(
    image: Image.Image,
    points: Sequence[tuple[int, int]],
    index: int,
    style: MapRenderStyle,
) -> None:
    """Draw one lawn with an optional decorative, polygon-clipped stripe fill."""
    fill = style.zone_fills[index % len(style.zone_fills)]
    outline = style.zone_outlines[index % len(style.zone_outlines)]
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.polygon(points, fill=fill)
    if style.zone_pattern == "striped":
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).polygon(points, fill=255)
        stripes = Image.new("RGBA", image.size, (0, 0, 0, 0))
        stripe_draw = ImageDraw.Draw(stripes)
        step = max(12, round(max(image.size) / 50))
        skew = image.height // 5
        for x in range(-skew, image.width + skew, step):
            stripe_draw.line(
                [(x, 0), (x - skew, image.height)],
                fill=(255, 255, 255, 150),
                width=max(4, round(step * 0.4)),
            )
        clipped = Image.new("RGBA", image.size, (0, 0, 0, 0))
        clipped.paste(stripes, (0, 0), mask)
        layer.alpha_composite(clipped)
    ImageDraw.Draw(layer).line(
        [*points, points[0]], fill=outline, width=line_width(style, 2), joint="curve"
    )
    image.alpha_composite(layer)


def draw_navigation_path(
    image: Image.Image,
    points: Sequence[tuple[int, int]],
    style: MapRenderStyle,
) -> None:
    """Draw a schematic road, not a measured corridor or a cut-coverage mask.

    Width is a presentation choice in image pixels. Keep the recorded open
    centerline and carry dash spacing across vertices, including duplicates.
    """
    if len(points) < 2:
        return
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    edge = style.navigation_path
    surface = (*(min(255, c + 24) for c in edge[:3]), edge[3])
    marking = (*(min(255, c + 85) for c in edge[:3]), edge[3])
    for width, color in (
        (line_width(style, 15), edge),
        (line_width(style, 11), surface),
    ):
        draw.line(points, fill=color, width=width, joint="curve")
        radius = (width - 1) / 2
        for x, y in (points[0], points[-1]):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)

    dash = line_width(style, 7)
    period = dash + line_width(style, 6)
    distance = 0.0
    for start, end in zip(points, points[1:], strict=False):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = hypot(dx, dy)
        offset = 0.0
        while offset < length:
            phase = (distance + offset) % period
            painted = phase < dash
            step = min(length - offset, (dash if painted else period) - phase)
            if step < 1e-9:
                break
            if painted:
                draw.line(
                    [
                        (
                            start[0] + dx * offset / length,
                            start[1] + dy * offset / length,
                        ),
                        (
                            start[0] + dx * (offset + step) / length,
                            start[1] + dy * (offset + step) / length,
                        ),
                    ],
                    fill=marking,
                    width=line_width(style, 2),
                )
            offset += step
        distance += length
    image.alpha_composite(layer)
