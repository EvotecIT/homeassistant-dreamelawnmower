"""Consistent lawn fills, pathways, and markers for local mower maps."""

from __future__ import annotations

from collections.abc import Sequence

from PIL import Image, ImageDraw

from .map_visuals import MapRenderStyle, line_width


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
    """Draw an open connector without joining unrelated endpoints."""
    if len(points) < 2:
        return
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.line(
        points, fill=style.navigation_path, width=line_width(style, 7), joint="curve"
    )
    draw.line(
        points,
        fill=(
            *(min(255, c + 30) for c in style.navigation_path[:3]),
            style.navigation_path[3],
        ),
        width=line_width(style, 3),
        joint="curve",
    )
    image.alpha_composite(layer)
