"""One generation-scoped photo owner shared by cameras and map delivery."""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Any

from .const import CONF_MAP_BACKGROUND, DEFAULT_MAP_BACKGROUND
from .control_options import active_map_index
from .dreame_lawn_mower_client.map_inventory import _app_map_inventory_identity
from .dreame_lawn_mower_client.real_scene import RealSceneImage, async_fetch_real_scene

_LOGGER = logging.getLogger(__name__)
_REFRESH_SECONDS = 30 * 60
_FAILURE_SECONDS = 60


class RealSceneCache:
    """Coalesce photo reads and reject obsolete map, option and unload contexts."""

    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator
        self._context: tuple[int, str, float | None] | None = None
        self._scene: RealSceneImage | None = None
        self._checked_at = 0.0
        self._retry_seconds = _REFRESH_SECONDS
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    def _current_context(self) -> tuple[int, str, float | None] | None:
        coordinator = self.coordinator
        if (
            self._closed or getattr(coordinator, "_shutting_down", False)
            or coordinator.entry.options.get(
                CONF_MAP_BACKGROUND, DEFAULT_MAP_BACKGROUND
            )
            != "real_scene"
            or getattr(coordinator, "_map_switch_inventory_pending_event", None)
            is not None
        ):
            return None
        maps = coordinator.app_maps
        index = active_map_index(maps)
        if index is None or not maps or maps.get("map_list_valid") is not True:
            return None
        identity = _app_map_inventory_identity(maps.get("maps") or ())
        if identity is None:
            return None
        return index, identity, getattr(coordinator, "_last_map_switch_event_at", None)

    @property
    def current(self) -> RealSceneImage | None:
        """Expose a photo only while its verified map generation is current."""
        context = self._current_context()
        if context != self._context:
            self._context = context
            self._scene = None
            self._checked_at = 0.0
            self.coordinator.real_scene = None
        return self._scene if context is not None else None

    def request(self) -> asyncio.Task[None] | None:
        """Start at most one bounded read; a changed context gets one follow-up."""
        _ = self.current
        context = self._context
        if context is None:
            return None
        if self._task is not None and not self._task.done():
            return self._task
        if self._checked_at and monotonic() - self._checked_at < self._retry_seconds:
            return None
        self._task = self.coordinator.hass.async_create_task(
            self._load(context), "dreame-lawn-mower-real-scene",
        )
        self._task.add_done_callback(self._finished)
        return self._task

    def _finished(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()
        if self._task is task:
            self._task = None
        if not self._closed:
            self.request()

    async def _load(self, context: tuple[int, str, float | None]) -> None:
        failed = False
        if context != self._current_context():
            return
        try:
            scene = await async_fetch_real_scene(
                self.coordinator.client, context[0], current=self.current,
            )
        except Exception:  # Photo failure must not fail vector-map delivery.
            _LOGGER.debug("Optional real-scene map read failed")
            scene, failed = self.current, True
        if context != self._current_context():
            return
        changed = scene != self._scene
        self._scene = scene
        self.coordinator.real_scene = scene
        self._checked_at = monotonic()
        self._retry_seconds = _FAILURE_SECONDS if failed else _REFRESH_SECONDS
        if changed:
            self.coordinator.async_update_listeners()

    async def async_close(self) -> None:
        """Stop the one shared read before the coordinator closes its client."""
        self._closed = True
        self._scene = None
        self.coordinator.real_scene = None
        task = self._task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._task = None


def real_scene_owner(coordinator: Any) -> RealSceneCache:
    """Resolve the entry's sole photo owner lazily for all read surfaces."""
    owner = getattr(coordinator, "_real_scene_owner", None)
    if not isinstance(owner, RealSceneCache):
        owner = RealSceneCache(coordinator)
        coordinator._real_scene_owner = owner
    return owner
