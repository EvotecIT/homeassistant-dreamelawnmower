"""Read-only client entry points for independent mowing map layers."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from .map_visuals import MapRenderStyle
from .mowing_map import MowingMapScene, build_mowing_map_scene, mowing_map_overlay
from .vector_map import parse_batch_vector_map

MAX_SCENE_INPUT_UNITS = 4 * 1024 * 1024
MAX_SCENE_CHUNKS = 1024


def bounded_mowing_map_batch(batch: Mapping[str, Any] | None) -> dict[str, Any]:
    """Bound map parsing by input characters/bytes and chunk count.

    The existing cloud response is already decoded. Do not copy or parse its
    device-sized M_PATH history, or unrelated batch properties, for a background.
    """
    geometry: dict[str, Any] = {}
    size = 0
    for key, value in (batch or {}).items():
        if not isinstance(key, str) or not key.startswith("MAP."):
            continue
        if len(key) > 64 or len(geometry) >= MAX_SCENE_CHUNKS:
            raise ValueError("The current map exceeds the chunk budget.")
        if isinstance(value, (str, bytes)):
            size += len(value)
        elif key != "MAP.info" or not isinstance(value, int):
            continue
        if size > MAX_SCENE_INPUT_UNITS:
            raise ValueError("The current map exceeds the input budget.")
        geometry[key] = value
    return geometry


class _DreameLawnMowerClientMowingMapMixin:
    """Reuse the client transport and session owner; keep HTTP out of the core."""

    async def async_get_mowing_map_scene(
        self, *, map_index: int, style: MapRenderStyle, label_scale: float = 1.0
    ) -> MowingMapScene:
        """Read current-map geometry and build a background off the event loop."""
        return await asyncio.to_thread(
            self._sync_get_mowing_map_scene,
            map_index=map_index,
            style=style,
            label_scale=label_scale,
        )

    def _sync_get_mowing_map_scene(
        self, *, map_index: int, style: MapRenderStyle, label_scale: float
    ) -> MowingMapScene:
        batch = self._sync_get_vector_map_batch_data()
        geometry = bounded_mowing_map_batch(batch)
        vector_map = parse_batch_vector_map(geometry, current_map_index=map_index)
        if vector_map is None:
            raise ValueError("No geometry is available for the current map.")
        return build_mowing_map_scene(vector_map, style=style, label_scale=label_scale)

    def mowing_map_runtime_overlay(self, scene: MowingMapScene) -> dict[str, Any]:
        """Read the existing session cache without requesting mower operations."""
        blob = self._latest_runtime_status_blob
        if (
            blob is not None
            and blob.candidate_runtime_task_id is not None
            and blob.candidate_runtime_task_id != self._runtime_live_task_id
        ):
            blob = None
        return mowing_map_overlay(
            scene,
            blob,
            map_index=self._runtime_live_map_index,
            active=self._runtime_session_active is True,
            track_segments=self._runtime_live_track_segments,
        )
