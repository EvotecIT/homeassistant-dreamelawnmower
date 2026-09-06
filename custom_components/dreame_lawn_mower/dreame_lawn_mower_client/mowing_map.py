"""Private, bounded map backgrounds and current-session overlay contracts."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from .map_projection import MapProjection, vector_map_projection
from .map_visuals import MapRenderStyle
from .models import DreameLawnMowerStatusBlob
from .vector_map import DreameLawnMowerVectorMap, render_vector_map_png

MAX_SCENE_POINTS = 50_000
MAX_BACKGROUND_BYTES = 4 * 1024 * 1024
MAX_TRAIL_POINTS = 4096
POSITION_MAX_AGE_SECONDS = 90


@dataclass(frozen=True, slots=True)
class MowingMapScene:
    """An immutable background whose revision excludes all runtime telemetry."""

    revision: str
    map_index: int
    map_id: int
    name: str
    image_png: bytes
    projection: MapProjection
    bounds: tuple[int, int, int, int]

    def contains(self, x: Any, y: Any) -> bool:
        """Accept finite native coordinates inside this map's bounding box."""
        return (
            _finite(x)
            and _finite(y)
            and self.bounds[0] <= x <= self.bounds[2]
            and self.bounds[1] <= y <= self.bounds[3]
        )


def build_mowing_map_scene(
    vector_map: DreameLawnMowerVectorMap,
    *,
    style: MapRenderStyle,
    label_scale: float = 1.0,
) -> MowingMapScene:
    """Render static geometry once; never label historical paths as coverage."""
    boundary = vector_map.boundary
    if boundary is None:
        raise ValueError("The current map has no drawable boundary.")
    shapes = (
        *vector_map.zones,
        *vector_map.paths,
        *vector_map.forbidden_areas,
        *vector_map.spot_areas,
    )
    if sum(len(shape.points) for shape in shapes) > MAX_SCENE_POINTS:
        raise ValueError("The current map exceeds the interactive geometry budget.")
    # M_PATH records do not establish current-session cutting coverage. Keep
    # them out of the immutable background and use observed runtime tracks only.
    background = replace(vector_map, mow_paths=(), maps={})
    image_png = render_vector_map_png(
        background, style=replace(style, zone_pattern="solid"), label_scale=label_scale
    )
    if not image_png or len(image_png) > MAX_BACKGROUND_BYTES:
        raise ValueError("The current map background is unavailable or too large.")
    projection = vector_map_projection(
        boundary.x1, boundary.y1, boundary.x2, boundary.y2, rotation=style.rotation
    )
    identity = f"{vector_map.map_index}:{vector_map.map_id}:{style.rotation}:".encode()
    revision = hashlib.sha256(identity + image_png).hexdigest()
    return MowingMapScene(
        revision,
        vector_map.map_index,
        vector_map.map_id,
        vector_map.name,
        image_png,
        projection,
        (boundary.x1, boundary.y1, boundary.x2, boundary.y2),
    )


def mowing_map_overlay(
    scene: MowingMapScene,
    blob: DreameLawnMowerStatusBlob | None,
    *,
    map_index: int | None,
    active: bool,
    track_segments: Sequence[Sequence[tuple[int, int]]] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project fresh, identity-matched telemetry without fetching or rendering.

    A movement trail is not a cut-area mask. Missing, stale, out-of-bounds or
    differently scoped evidence deliberately produces no live marker or trail.
    """
    result: dict[str, Any] = {
        "position": None,
        "trail": [],
        "updated_at": None,
        "position_status": "unavailable",
        "coverage_available": False,
        "trail_kind": "observed_movement",
        "max_age_seconds": POSITION_MAX_AGE_SECONDS,
    }
    if map_index != scene.map_index:
        result["position_status"] = "map_mismatch"
        return result
    if blob is None or not blob.frame_valid:
        return result
    try:
        updated = datetime.fromisoformat(blob.received_at or "")
        if updated.tzinfo is None:
            return result
        age = ((now or datetime.now(UTC)) - updated).total_seconds()
    except (ValueError, TypeError, OverflowError):
        return result
    if not -5 <= age <= POSITION_MAX_AGE_SECONDS:
        result["position_status"] = "stale"
        return result
    result["updated_at"] = updated.isoformat()
    x, y = blob.candidate_runtime_pose_x, blob.candidate_runtime_pose_y
    if scene.contains(x, y):
        px, py = scene.projection.point(x, y)
        heading = blob.candidate_runtime_heading_deg
        result["position"] = {
            "x": px,
            "y": py,
            # The native X mirror reverses yaw. Screen angles are clockwise
            # from up, matching an upright SVG marker's rotation convention.
            "heading": ((270 - heading + scene.projection.rotation) % 360)
            if _finite(heading)
            else None,
        }
        result["position_status"] = "current"
    else:
        result["position_status"] = "out_of_bounds"
    if active:
        # Retain recent segments, never joining across invalid points or gaps.
        remaining = MAX_TRAIL_POINTS
        retained: list[list[list[int]]] = []
        for segment in reversed(track_segments[-64:]):
            if remaining < 2:
                break
            part: list[list[int]] = []
            parts: list[list[list[int]]] = []
            for point in segment[-remaining:]:
                if len(point) == 2 and scene.contains(*point):
                    part.append(list(scene.projection.point(*point)))
                else:
                    if len(part) >= 2:
                        parts.append(part)
                    part = []
            if len(part) >= 2:
                parts.append(part)
            remaining -= sum(len(part) for part in parts)
            retained[0:0] = parts
        result["trail"] = retained
    return result


def _finite(value: Any) -> bool:
    """Reject booleans, missing values and non-finite numeric telemetry."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )
