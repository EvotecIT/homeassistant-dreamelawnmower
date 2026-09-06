"""Small geometry operations shared by both mower map renderers."""

from __future__ import annotations

import heapq
import math
from collections.abc import Sequence

Point = tuple[float, float]


def rotate_canvas_point(
    point: Point, width: int, height: int, rotation: int
) -> tuple[int, int]:
    """Rotate geometry clockwise before drawing upright text and markers."""
    x, y = point
    if rotation == 90:
        x, y = height - 1 - y, x
    elif rotation == 180:
        x, y = width - 1 - x, height - 1 - y
    elif rotation == 270:
        x, y = y, width - 1 - x
    return round(x), round(y)


def polygon_label_point(points: Sequence[Point]) -> Point:
    """Find a point with useful interior clearance, including concave lawns.

    Search cells by their upper bound on boundary clearance. The pixel-space
    tolerance keeps rendering bounded without depending on a geometry package.
    Degenerate polygons use an actual boundary point instead of an invented
    off-map centroid.
    """
    if not points:
        return 0.0, 0.0
    min_x, max_x = min(p[0] for p in points), max(p[0] for p in points)
    min_y, max_y = min(p[1] for p in points), max(p[1] for p in points)
    width, height = max_x - min_x, max_y - min_y
    if min(width, height) <= 0:
        return points[0]
    tolerance = max(max(width, height) / 256, 0.5)
    best_x, best_y = points[0]
    best_distance = 0.0
    queue: list[tuple[float, float, float, float, float]] = []

    def add(x: float, y: float, half: float) -> None:
        distance = _signed_boundary_distance((x, y), points)
        upper = distance + half * math.sqrt(2)
        heapq.heappush(queue, (-upper, x, y, half, distance))

    add((min_x + max_x) / 2, (min_y + max_y) / 2, max(width, height) / 2)
    # A bounded best-first search also handles thin or highly detailed outlines.
    for _ in range(4096):
        if not queue:
            break
        negative_upper, x, y, half, distance = heapq.heappop(queue)
        if distance > best_distance:
            best_x, best_y, best_distance = x, y, distance
        if -negative_upper - best_distance <= tolerance:
            continue
        half /= 2
        for dx, dy in ((-half, -half), (-half, half), (half, -half), (half, half)):
            add(x + dx, y + dy, half)
    return best_x, best_y


def _signed_boundary_distance(point: Point, polygon: Sequence[Point]) -> float:
    """Return positive distance inside an even-odd polygon and negative outside."""
    x, y = point
    inside = False
    minimum = math.inf
    ax, ay = polygon[-1]
    for bx, by in polygon:
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        fraction = (
            max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length))
            if length
            else 0
        )
        minimum = min(
            minimum, (x - ax - fraction * dx) ** 2 + (y - ay - fraction * dy) ** 2
        )
        ax, ay = bx, by
    return math.sqrt(minimum) * (1 if inside else -1)
