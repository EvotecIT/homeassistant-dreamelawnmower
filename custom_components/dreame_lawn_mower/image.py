"""Image helpers for Dreame lawn mower entities."""

from __future__ import annotations

from io import BytesIO

from PIL import Image


def png_bytes_to_jpeg(image_bytes: bytes) -> bytes:
    """Convert rendered PNG bytes to JPEG for Home Assistant snapshots."""
    with Image.open(BytesIO(image_bytes)) as image:
        converted = image.convert("RGB")
        output = BytesIO()
        converted.save(output, format="JPEG", quality=90)
        return output.getvalue()
