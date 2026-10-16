"""Vendor real-scene map: a top-down garden photo drawn under the vector map.

The app asks the mower for ``{"m": "g", "t": "OBJ", "d": {"type": "ARmap"}}``
and receives one stored PNG object name per map slot, for example
``ali_dreame/2026/10/15/IQ000001/-1234_150138416.2026-10-15-11-01-36_275_202_3cm_processed.png``.
The trailing integers before the resolution token are the image origin in
pixels and ``3cm`` is the ground size of one pixel. The app draws the image
rotated by 180 degrees, so pixel ``(u, v)`` lies at map millimetres
``((u - origin_x) * mm, (origin_y - v) * mm)``. Verified on a MOVA LiDAX Ultra
1000 (mova.mower.g2529c), where the lawn contour matched the photo.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from typing import TYPE_CHECKING

from .client_app_reads import async_read_app_action
from .map_objects import map_object_names
from .public_download import MAX_PUBLIC_MAP_BYTES, PublicDownloadError

if TYPE_CHECKING:
    from PIL import Image as PILImage

    from .client import DreameLawnMowerClient
    from .cloud_session import DreameCloudSession

REAL_SCENE_OBJECT_TYPE = "ARmap"
REAL_SCENE_OPACITY = 0.88
MAX_REAL_SCENE_SIDE = 8192
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_RESOLUTION = re.compile(r"^(-?\d+(?:\.\d+)?)(mm|cm|m)$", re.I)
_INTEGER = re.compile(r"^-?\d+$")
_MILLIMETRES = {"mm": 1.0, "cm": 10.0, "m": 1000.0}

CanvasProjection = Callable[[float, float], tuple[float, float]]


@dataclass(frozen=True, slots=True)
class RealSceneImage:
    """One map slot's real-scene PNG and its placement in map millimetres."""

    map_index: int
    object_name: str
    origin_x: float
    origin_y: float
    millimetres_per_pixel: float
    digest: str
    png: bytes = field(repr=False, compare=False)


def parse_real_scene_name(name: str) -> tuple[float, float, float] | None:
    """Return origin and millimetres per pixel encoded in an ARmap object name."""
    stem = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    tokens = [token for token in stem.split("_") if token]
    resolution = next(
        (index for index, token in enumerate(tokens) if _RESOLUTION.match(token)),
        None,
    )
    if resolution is None:
        return None
    value, unit = _RESOLUTION.match(tokens[resolution]).groups()  # type: ignore[union-attr]
    millimetres = float(value) * _MILLIMETRES[unit.lower()]
    numbers: list[str] = []
    for token in reversed(tokens[:resolution]):
        if not _INTEGER.match(token):
            break
        numbers.insert(0, token)
    if len(numbers) < 2 or millimetres <= 0:
        return None
    return float(numbers[0]), float(numbers[1]), millimetres


def real_scene_from_png(
    map_index: int, object_name: str, png: bytes
) -> RealSceneImage | None:
    """Validate a downloaded object and attach its placement metadata."""
    meta = parse_real_scene_name(object_name)
    if meta is None or not png.startswith(_PNG_SIGNATURE):
        return None
    origin_x, origin_y, millimetres = meta
    return RealSceneImage(
        map_index=map_index,
        object_name=object_name,
        origin_x=origin_x,
        origin_y=origin_y,
        millimetres_per_pixel=millimetres,
        digest=hashlib.sha256(png).hexdigest(),
        png=png,
    )


def paint_real_scene(
    canvas: PILImage.Image,
    scene: RealSceneImage,
    to_canvas: CanvasProjection,
    *,
    opacity: float = REAL_SCENE_OPACITY,
) -> bool:
    """Warp the photo onto the canvas with the renderer's own projection.

    ``to_canvas`` maps native map millimetres to final, rotated canvas pixels,
    so mirroring, scale and display rotation always match the vector layers.
    """
    from PIL import Image

    try:
        with Image.open(BytesIO(scene.png)) as source:
            if max(source.size) > MAX_REAL_SCENE_SIDE:
                return False
            photo = source.convert("RGBA")
    except (Image.DecompressionBombError, OSError, ValueError):
        return False
    width, height = photo.size

    def corner(u: float, v: float) -> tuple[float, float]:
        mm = scene.millimetres_per_pixel
        return to_canvas((u - scene.origin_x) * mm, (scene.origin_y - v) * mm)

    x0, y0 = corner(0, 0)
    x1, y1 = corner(width, 0)
    x2, y2 = corner(0, height)
    a, b = (x1 - x0) / width, (x2 - x0) / height
    c, d = (y1 - y0) / width, (y2 - y0) / height
    determinant = a * d - b * c
    if abs(determinant) < 1e-12:
        return False
    # PIL wants the inverse map: source pixel for each canvas pixel.
    inverse = (
        d / determinant,
        -b / determinant,
        (b * y0 - d * x0) / determinant,
        -c / determinant,
        a / determinant,
        (c * x0 - a * y0) / determinant,
    )
    warped = photo.transform(
        canvas.size,
        Image.Transform.AFFINE,
        inverse,
        resample=Image.Resampling.BILINEAR,
        fillcolor=(0, 0, 0, 0),
    )
    if opacity < 1:
        alpha = warped.getchannel("A").point(lambda value: round(value * opacity))
        warped.putalpha(alpha)
    canvas.alpha_composite(warped)
    return True


async def async_fetch_real_scene(
    client: DreameLawnMowerClient,
    map_index: int,
    *,
    current: RealSceneImage | None = None,
    deadline: float | None = None,
) -> RealSceneImage | None:
    """Ask the mower for the slot's photo; download only when its name changes."""
    deadline = min(deadline or float("inf"), time.monotonic() + 60)
    response = await async_read_app_action(
        client,
        {"m": "g", "t": "OBJ", "d": {"type": REAL_SCENE_OBJECT_TYPE}},
        deadline=deadline,
    )
    names = map_object_names(response)
    if not 0 <= map_index < len(names) or not isinstance(names[map_index], str):
        return None
    object_name = names[map_index].strip()
    if not object_name or parse_real_scene_name(object_name) is None:
        return None
    if (
        current is not None
        and current.map_index == map_index
        and current.object_name == object_name
    ):
        return current
    descriptor = client._descriptor

    async def read(cloud: DreameCloudSession) -> bytes | None:
        url = await cloud.async_get_interim_file_url(
            descriptor.did,
            descriptor.model,
            object_name,
            deadline=deadline,
            require_response=True,
        )
        if not url:
            return None
        try:
            return await cloud.async_get_public_file(
                url, deadline=deadline, max_bytes=MAX_PUBLIC_MAP_BYTES
            )
        except PublicDownloadError as err:
            if err.status in (404, 410):
                return None
            raise

    png = await client._async_cloud_read(read)
    return real_scene_from_png(map_index, object_name, png) if png else None
