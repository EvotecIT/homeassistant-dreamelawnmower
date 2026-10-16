"""Read-only client entry points for independent mowing map layers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .app_map_geometry import parse_app_map_geometry
from .client_position import vector_map_position
from .client_tracking import _DreameLawnMowerClientTrackingMixin
from .exceptions import DreameLawnMowerConnectionError
from .map_visuals import MapRenderStyle
from .mowing_map import MowingMapScene, build_mowing_map_scene, mowing_map_overlay
from .position_tracking import snapshot_is_docked
from .vector_map import _parse_drawable_batch_vector_map, with_charger
from .zone_mowing_history import reported_charger_for_map

if TYPE_CHECKING:
    from .models import DreameLawnMowerSnapshot
    from .vector_map import DreameLawnMowerVectorMap


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


class _DreameLawnMowerClientMowingMapMixin(_DreameLawnMowerClientTrackingMixin):
    """Reuse transport and live tracking owners for cached map overlays."""

    _latest_snapshot: DreameLawnMowerSnapshot | None

    if TYPE_CHECKING:
        # Implemented by the concrete client's map owner.
        def _sync_get_vector_map_batch_data(self) -> Mapping[str, Any] | None: ...

        def _sync_get_app_maps(
            self, chunk_size: int = 400, include_payload: bool = False,
            include_objects: bool = True, include_object_urls: bool = False,
        ) -> dict[str, Any]: ...

    def _sync_get_mowing_map_scene(
        self, *, map_index: int, style: MapRenderStyle, label_scale: float
    ) -> MowingMapScene:
        generation = self._runtime_geometry_generation
        batch_error = None
        try:
            batch = self._sync_get_vector_map_batch_data()
        except DreameLawnMowerConnectionError as err:
            batch = None
            batch_error = err
        app_maps = None
        geometry = bounded_mowing_map_batch(batch)
        if _parse_drawable_batch_vector_map(
            geometry, current_map_index=map_index,
        ) is None:
            try:
                app_maps = self._sync_get_app_maps(
                    include_payload=True, include_objects=False,
                )
            except Exception as app_error:
                if batch_error is not None:
                    raise batch_error from app_error
                raise
        if batch_error is not None and parse_app_map_geometry(
            app_maps, map_index,
        ) is None:
            raise batch_error
        return self._mowing_map_scene_from_batch(
            batch, map_index=map_index, style=style, label_scale=label_scale,
            app_maps=app_maps,
            geometry_generation=generation,
        )

    def _mowing_map_scene_from_batch(
        self, batch: Mapping[str, Any] | None, *, map_index: int,
        style: MapRenderStyle, label_scale: float,
        geometry_generation: int,
        app_maps: Mapping[str, Any] | None = None,
    ) -> MowingMapScene:
        """Observe a fresh frame before rendering its cacheable background."""
        vector_map = with_charger(
            _mowing_scene_geometry(batch, map_index, app_maps),
            reported_charger_for_map(
                self._charger_history, app_maps, map_index,
            ),
        )
        vector_map_position(
            self, vector_map, expected_generation=geometry_generation,
        )
        return build_mowing_map_scene(vector_map, style=style, label_scale=label_scale)

    def mowing_map_runtime_overlay(self, scene: MowingMapScene) -> dict[str, Any]:
        """Read the existing session cache without requesting mower operations."""
        with self._runtime_geometry_lock:
            return self._mowing_map_runtime_overlay(scene)

    def _mowing_map_runtime_overlay(self, scene: MowingMapScene) -> dict[str, Any]:
        self._expire_runtime_live_tracking()
        if self._runtime_live_geometry_identity not in (None, scene.position_identity):
            return mowing_map_overlay(
                scene, None, map_index=self._runtime_live_map_index, active=False,
                docked=snapshot_is_docked(self._latest_snapshot),
            )
        position = self._position_tracker.resolve(
            map_index=scene.map_index,
            geometry=scene.position_identity,
            contains=scene.contains,
            snapshot=self._latest_snapshot,
            allow_geometry_change=False,
        )
        position = self._place_docked_on_station(
            position, map_index=scene.map_index,
            geometry=scene.position_identity, charger=scene.charger,
        )
        blob = self._latest_runtime_status_blob
        # The tracker owns geometry binding, including negative decisions.
        # A raw packet must not resurrect a rejected position in the projector.
        if position is None or not getattr(self._latest_snapshot, "available", False):
            blob = None
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
            retained_position=position,
            docked=snapshot_is_docked(self._latest_snapshot),
        )


def mowing_scene_from_batch(
    batch: Mapping[str, Any] | None,
    *,
    map_index: int,
    style: MapRenderStyle,
    label_scale: float,
    app_maps: Mapping[str, Any] | None = None,
) -> MowingMapScene:
    """Build a bounded scene using the common synchronous/native render policy."""
    vector_map = _mowing_scene_geometry(batch, map_index, app_maps)
    return build_mowing_map_scene(vector_map, style=style, label_scale=label_scale)


def _mowing_scene_geometry(
    batch: Mapping[str, Any] | None, map_index: int,
    app_maps: Mapping[str, Any] | None,
) -> DreameLawnMowerVectorMap:
    """Select one bounded verified frame for both pure and observing builders."""
    geometry = bounded_mowing_map_batch(batch)
    vector_map = _parse_drawable_batch_vector_map(geometry, current_map_index=map_index)
    if vector_map is None:
        vector_map = parse_app_map_geometry(app_maps, map_index)
    if vector_map is None:
        raise ValueError("No geometry is available for the current map.")
    if vector_map.boundary is None:
        raise ValueError("The current map has no drawable boundary.")
    return vector_map
