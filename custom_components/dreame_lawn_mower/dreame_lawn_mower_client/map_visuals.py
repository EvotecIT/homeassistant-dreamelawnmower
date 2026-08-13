"""Shared visual language for locally rendered mower maps."""

from __future__ import annotations

import base64
import math
import zlib
from dataclasses import dataclass, replace
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .resources import MAP_FONT, MAP_FONT_LIGHT

Color = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class MapRenderStyle:
    """Colors and dimensions shared by every locally rendered map source."""

    name: str
    background: Color
    boundary: Color
    zone_fills: tuple[Color, ...]
    zone_outlines: tuple[Color, ...]
    forbidden_fill: Color
    forbidden_outline: Color
    spot_fill: Color
    spot_outline: Color
    navigation_path: Color
    mow_path: Color
    live_path: Color
    current_position: Color
    point: Color
    label: Color
    label_halo: Color
    stroke_scale: float = 1.0
    marker_scale: float = 1.0
    marker_image: bytes | None = None
    spot_area_style: str = "filled"
    mowing_path_style: str = "detailed"


_EMERALD = MapRenderStyle(
    name="emerald",
    background=(239, 246, 241, 255),
    boundary=(45, 92, 72, 255),
    zone_fills=(
        (128, 203, 156, 210),
        (116, 191, 201, 210),
        (233, 190, 125, 210),
        (215, 151, 166, 210),
        (205, 199, 117, 210),
        (168, 150, 207, 210),
    ),
    zone_outlines=(
        (53, 137, 88, 255),
        (43, 125, 141, 255),
        (179, 126, 54, 255),
        (166, 86, 108, 255),
        (145, 138, 43, 255),
        (112, 88, 165, 255),
    ),
    forbidden_fill=(210, 65, 70, 120),
    forbidden_outline=(190, 43, 49, 235),
    spot_fill=(85, 153, 223, 125),
    spot_outline=(42, 112, 182, 235),
    navigation_path=(120, 135, 129, 200),
    mow_path=(31, 112, 71, 210),
    live_path=(16, 132, 214, 245),
    current_position=(255, 128, 32, 255),
    point=(39, 55, 48, 255),
    label=(25, 50, 40, 255),
    label_halo=(255, 255, 255, 215),
)

MAP_RENDER_STYLES: dict[str, MapRenderStyle] = {
    "emerald": _EMERALD,
    "dark": replace(
        _EMERALD,
        name="dark",
        background=(25, 31, 29, 255),
        boundary=(132, 193, 163, 255),
        navigation_path=(148, 159, 154, 220),
        mow_path=(109, 207, 151, 225),
        label=(235, 244, 239, 255),
        label_halo=(15, 20, 18, 225),
        point=(225, 235, 229, 255),
    ),
    "midnight": replace(
        _EMERALD,
        name="midnight",
        background=(15, 25, 44, 255),
        boundary=(111, 181, 230, 255),
        navigation_path=(124, 146, 177, 220),
        mow_path=(55, 205, 157, 230),
        live_path=(62, 185, 255, 255),
        label=(238, 246, 255, 255),
        label_halo=(7, 14, 27, 230),
        point=(225, 238, 255, 255),
    ),
    "high_contrast": replace(
        _EMERALD,
        name="high_contrast",
        background=(255, 255, 255, 255),
        boundary=(0, 0, 0, 255),
        navigation_path=(55, 55, 55, 255),
        mow_path=(0, 102, 41, 255),
        live_path=(0, 87, 184, 255),
        current_position=(230, 75, 0, 255),
        label=(0, 0, 0, 255),
        label_halo=(255, 255, 255, 255),
        point=(0, 0, 0, 255),
        stroke_scale=1.35,
        marker_scale=1.2,
    ),
}


def map_render_style(
    name: str | None = None,
    *,
    stroke_scale: float = 1.0,
    marker_scale: float = 1.0,
    marker_image: bytes | None = None,
    spot_area_style: str = "filled",
    mowing_path_style: str = "detailed",
) -> MapRenderStyle:
    """Return a normalized preset with optional safe presentation overrides."""
    preset = MAP_RENDER_STYLES.get(str(name or "").lower(), _EMERALD)
    return replace(
        preset,
        stroke_scale=_finite_scale(
            preset.stroke_scale * _finite_scale(stroke_scale, 0.5, 3.0),
            0.5,
            3.0,
        ),
        marker_scale=_finite_scale(
            preset.marker_scale * _finite_scale(marker_scale, 0.5, 3.0),
            0.5,
            3.0,
        ),
        marker_image=marker_image,
        spot_area_style=_choice(
            spot_area_style,
            {"hidden", "outline", "filled"},
            "filled",
        ),
        mowing_path_style=_choice(
            mowing_path_style,
            {"hidden", "subtle", "detailed"},
            "detailed",
        ),
    )


def line_width(style: MapRenderStyle, base: int) -> int:
    """Scale a renderer line width while keeping it visible."""
    return max(1, int(round(base * style.stroke_scale)))


def marker_radius(style: MapRenderStyle, base: int) -> int:
    """Scale a renderer marker radius while keeping it visible."""
    return max(2, int(round(base * style.marker_scale)))


@lru_cache(maxsize=2)
def _font_bytes(*, bold: bool) -> bytes:
    encoded = MAP_FONT if bold else MAP_FONT_LIGHT
    return zlib.decompress(base64.b64decode(encoded), zlib.MAX_WBITS | 32)


@lru_cache(maxsize=64)
def map_font(size: int, *, bold: bool = True) -> ImageFont.ImageFont:
    """Load the bundled Unicode-capable map font in every HA environment."""
    try:
        return ImageFont.truetype(BytesIO(_font_bytes(bold=bold)), size=max(8, size))
    except OSError:
        try:
            return ImageFont.load_default(size=max(8, size))
        except TypeError:
            return ImageFont.load_default()


def draw_position_marker(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    style: MapRenderStyle,
) -> None:
    """Draw a built-in marker or a validated custom image at a map position."""
    px, py = position
    radius = marker_radius(style, 7)
    if style.marker_image:
        try:
            with Image.open(BytesIO(style.marker_image)) as marker:
                if marker.width > 512 or marker.height > 512:
                    raise ValueError("Custom map marker dimensions exceed 512 pixels.")
                marker.load()
                marker_rgba = marker.convert("RGBA")
                side = max(18, radius * 4)
                marker_rgba.thumbnail((side, side), Image.Resampling.LANCZOS)
                image.alpha_composite(
                    marker_rgba,
                    (px - marker_rgba.width // 2, py - marker_rgba.height // 2),
                )
                return
        except (Image.DecompressionBombError, OSError, ValueError):
            pass
    draw.ellipse(
        (px - radius, py - radius, px + radius, py + radius),
        fill=style.current_position,
        outline=style.label_halo,
        width=line_width(style, 2),
    )
    inner = max(2, radius // 3)
    draw.ellipse(
        (px - inner, py - inner, px + inner, py + inner),
        fill=style.label_halo,
    )


def load_map_marker(www_root: Path, configured: Any) -> bytes | None:
    """Load a constrained custom marker from Home Assistant's www directory."""
    if not isinstance(configured, str) or not configured.strip():
        return None
    relative = configured.strip()
    if relative.startswith("/local/"):
        relative = relative.removeprefix("/local/")
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        return None
    root = www_root.resolve()
    candidate = (root / relative_path).resolve()
    if root not in candidate.parents or candidate.suffix.lower() not in {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
    }:
        return None
    try:
        if not candidate.is_file() or candidate.stat().st_size > 1024 * 1024:
            return None
        return candidate.read_bytes()
    except OSError:
        return None


def _finite_scale(value: Any, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = 1.0
    if not math.isfinite(result):
        result = 1.0
    return min(max(result, minimum), maximum)


def _choice(value: Any, choices: set[str], fallback: str) -> str:
    """Return a supported presentation choice or its safe fallback."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in choices else fallback
