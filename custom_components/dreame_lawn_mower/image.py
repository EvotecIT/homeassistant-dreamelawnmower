"""Image helpers for Dreame lawn mower entities."""

from __future__ import annotations

import base64
import zlib
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from .dreame_lawn_mower_client.resources import MAP_FONT, MAP_FONT_LIGHT


@lru_cache(maxsize=2)
def _font_bytes(*, bold: bool) -> bytes:
    """Return bundled font bytes without consulting host font directories."""
    encoded = MAP_FONT if bold else MAP_FONT_LIGHT
    return zlib.decompress(base64.b64decode(encoded), zlib.MAX_WBITS | 32)


@lru_cache(maxsize=32)
def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    """Return a readable font with safe fallbacks for Home Assistant containers."""
    try:
        return ImageFont.truetype(BytesIO(_font_bytes(bold=bold)), size=size)
    except OSError:
        pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    max_width: int,
    line_spacing: int = 8,
) -> int:
    """Draw wrapped text and return the next y coordinate."""
    x, y = position
    current_line = ""
    lines: list[str] = []
    for word in text.split():
        candidate = f"{current_line} {word}".strip()
        width = draw.textbbox((0, 0), candidate, font=font)[2]
        if width <= max_width or not current_line:
            current_line = candidate
            continue
        lines.append(current_line)
        current_line = word
    if current_line:
        lines.append(current_line)

    if not lines:
        lines = [""]

    for line in lines:
        draw.text((x, y), line, fill=fill, font=font)
        bbox = draw.textbbox((x, y), line or " ", font=font)
        y += bbox[3] - bbox[1] + line_spacing
    return y


def png_bytes_to_jpeg(image_bytes: bytes) -> bytes:
    """Convert rendered PNG bytes to JPEG for Home Assistant snapshots."""
    with Image.open(BytesIO(image_bytes)) as image:
        converted = image.convert("RGB")
        output = BytesIO()
        converted.save(output, format="JPEG", quality=90)
        return output.getvalue()


def app_maps_contact_sheet_jpeg(
    *,
    maps: list[dict[str, object]] | tuple[dict[str, object], ...],
    map_count: int | None = None,
    current_map_index: int | None = None,
    width: int = 1280,
) -> bytes:
    """Return a JPEG contact sheet for every rendered app map."""
    title_font = _font(34, bold=True)
    body_font = _font(22)
    small_font = _font(18)
    text = (235, 238, 242)
    muted = (112, 120, 128)
    panel = (248, 250, 252)
    border = (80, 170, 220)
    background = (24, 28, 33)
    gap = 28
    padding = 48
    card_width = (width - padding * 2 - gap) // 2
    card_height = 520
    rows = max((len(maps) + 1) // 2, 1)
    height = max(720, 150 + rows * card_height + max(rows - 1, 0) * gap + padding)
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)

    draw.text((padding, 40), "Dreame app maps", fill=text, font=title_font)
    details = [
        f"Map count: {map_count if map_count is not None else len(maps)}",
        "Current map: "
        f"{current_map_index if current_map_index is not None else 'unknown'}",
    ]
    draw.text(
        (padding, 92),
        "  |  ".join(details),
        fill=(172, 180, 188),
        font=small_font,
    )

    if not maps:
        _draw_wrapped_text(
            draw,
            (padding, 160),
            "No drawable app map payloads were returned.",
            font=body_font,
            fill=(172, 180, 188),
            max_width=width - padding * 2,
        )
    for index, item in enumerate(maps):
        col = index % 2
        row = index // 2
        x = padding + col * (card_width + gap)
        y = 140 + row * (card_height + gap)
        draw.rounded_rectangle(
            (x, y, x + card_width, y + card_height),
            radius=8,
            fill=panel,
            outline=border if item.get("current") else (210, 216, 222),
            width=3 if item.get("current") else 2,
        )
        label = f"Map {item.get('idx', '?')}"
        if item.get("current"):
            label += " - current"
        draw.text((x + 22, y + 20), label, fill=(15, 23, 42), font=body_font)
        summary = item.get("summary")
        if isinstance(summary, dict):
            line = "Areas: {areas}  Spots: {spots}  Path points: {points}".format(
                areas=summary.get("map_area_count", "?"),
                spots=summary.get("spot_count", "?"),
                points=summary.get("trajectory_point_count", "?"),
            )
            draw.text((x + 22, y + 56), line, fill=muted, font=small_font)

        image_bytes = item.get("image_png")
        if not isinstance(image_bytes, bytes):
            draw.text(
                (x + 22, y + 105),
                str(item.get("error") or "Map image unavailable"),
                fill=(127, 29, 29),
                font=small_font,
            )
            continue
        with Image.open(BytesIO(image_bytes)) as map_image:
            preview = map_image.convert("RGB")
            preview.thumbnail((card_width - 44, card_height - 130))
            px = x + (card_width - preview.width) // 2
            py = y + 96 + (card_height - 130 - preview.height) // 2
            image.paste(preview, (px, py))

    output = BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()


def map_placeholder_jpeg(
    *,
    title: str = "Dreame map unavailable",
    detail: str | None = None,
    width: int = 1024,
    height: int = 768,
) -> bytes:
    """Return a valid JPEG placeholder for failed map refreshes."""
    image = Image.new("RGB", (width, height), (28, 32, 36))
    draw = ImageDraw.Draw(image)
    accent = (80, 170, 220)
    text = (235, 238, 242)
    muted = (165, 172, 180)
    title_font = _font(34, bold=True)
    body_font = _font(24)
    footer_font = _font(20)

    draw.rounded_rectangle(
        (96, 96, width - 96, height - 96),
        radius=32,
        outline=accent,
        width=4,
    )
    draw.text((140, 150), title, fill=text, font=title_font)
    _draw_wrapped_text(
        draw,
        (140, 215),
        detail or "No map image was returned by the mower.",
        font=body_font,
        fill=muted,
        max_width=width - 280,
    )
    draw.text(
        (140, height - 170),
        "This placeholder is generated by the Home Assistant integration.",
        fill=muted,
        font=footer_font,
    )

    output = BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()


def map_diagnostics_jpeg(
    *,
    title: str = "Dreame map diagnostics",
    lines: list[str] | tuple[str, ...],
    width: int = 1280,
    height: int = 720,
) -> bytes:
    """Return a valid JPEG diagnostics card for map-data camera previews."""
    image = Image.new("RGB", (width, height), (24, 28, 33))
    draw = ImageDraw.Draw(image)
    accent = (80, 170, 220)
    text = (235, 238, 242)
    muted = (172, 180, 188)
    title_font = _font(34, bold=True)
    body_font = _font(23)
    footer_font = _font(18)

    draw.rounded_rectangle(
        (64, 64, width - 64, height - 64),
        radius=28,
        outline=accent,
        width=4,
    )
    draw.text((105, 108), title, fill=text, font=title_font)

    y = 170
    for line in lines:
        if y > height - 140:
            draw.text((105, y), "...", fill=muted, font=body_font)
            break
        y = _draw_wrapped_text(
            draw,
            (105, y),
            str(line),
            font=body_font,
            fill=muted,
            max_width=width - 210,
            line_spacing=7,
        )

    draw.text(
        (105, height - 105),
        "Structured details are available in entity attributes and diagnostics.",
        fill=muted,
        font=footer_font,
    )

    output = BytesIO()
    image.save(output, format="JPEG", quality=90)
    return output.getvalue()
