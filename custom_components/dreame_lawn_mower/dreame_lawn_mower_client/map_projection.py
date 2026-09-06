"""One pixel projection for vector backgrounds and independent live overlays."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .map_geometry import rotate_canvas_point


@dataclass(frozen=True, slots=True)
class MapProjection:
    """Describe the native-coordinate to clockwise-rotated canvas transform.

    Native X is mirrored by the map renderer. Keeping this transform alongside
    a background prevents frontends from estimating bounds from image pixels.
    """

    max_x: float
    min_y: float
    scale: float
    padding: int
    native_width: int
    native_height: int
    rotation: int = 0

    @property
    def width(self) -> int:
        """Return the final canvas width after display rotation."""
        return self.native_height if self.rotation in (90, 270) else self.native_width

    @property
    def height(self) -> int:
        """Return the final canvas height after display rotation."""
        return self.native_width if self.rotation in (90, 270) else self.native_height

    def point(self, x: float, y: float) -> tuple[int, int]:
        """Project a native point using the renderer's exact rounding rule."""
        return rotate_canvas_point(
            (
                (self.max_x - x) * self.scale + self.padding,
                (y - self.min_y) * self.scale + self.padding,
            ),
            self.native_width,
            self.native_height,
            self.rotation,
        )


def vector_map_projection(
    x1: float, y1: float, x2: float, y2: float, *, rotation: int = 0
) -> MapProjection:
    """Build the bounded projection shared by vector PNGs and mowing scenes."""
    if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
        raise ValueError("Map bounds must be finite.")
    if x2 < x1 or y2 < y1 or rotation not in (0, 90, 180, 270):
        raise ValueError("Invalid map bounds or rotation.")
    width, height = max(x2 - x1, 1), max(y2 - y1, 1)
    padding = 40
    scale = min((2048 - 2 * padding) / width, (2048 - 2 * padding) / height)
    scale = max(scale, 400 / max(width, height, 1))
    return MapProjection(
        max_x=x2,
        min_y=y1,
        scale=scale,
        padding=padding,
        native_width=int(width * scale) + 2 * padding,
        native_height=int(height * scale) + 2 * padding,
        rotation=rotation,
    )
