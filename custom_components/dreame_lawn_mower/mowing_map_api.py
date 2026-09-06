"""Authenticated read-only map layers, with no geometry in HA recorder state."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from aiohttp import web
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import CONF_MAP_LABEL_SCALE, DEFAULT_MAP_LABEL_SCALE, DOMAIN
from .control_options import active_map_index
from .dreame_lawn_mower_client.client_maps import _app_map_inventory_identity
from .dreame_lawn_mower_client.mowing_map import MowingMapScene
from .map_presentation import map_style

MOWING_MAP_API_KEY = "mowing_map_api"
MOWING_MAP_API_PATH = f"/api/{DOMAIN}/mowing-map"
_CACHE_SECONDS = 60
_MAX_ENTRIES = 4
_HEADERS = {
    "Cache-Control": "private, no-store",
    "Vary": "Authorization",
    "X-Content-Type-Options": "nosniff",
}


def mowing_map_api_path(entry_id: str) -> str:
    """Advertise the read-only scene endpoint without embedding credentials."""
    return f"{MOWING_MAP_API_PATH}/{entry_id}"


@dataclass(frozen=True, slots=True)
class _SceneEntry:
    coordinator: Any
    context: tuple[Any, ...]
    scene: MowingMapScene
    created_at: float


class MowingMapAPI:
    """Coalesce background reads; overlay requests reuse current client telemetry."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._cache: OrderedDict[str, _SceneEntry] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[_SceneEntry]] = {}
        self._failures: OrderedDict[str, tuple[Any, tuple[Any, ...], float]] = (
            OrderedDict()
        )

    def coordinator(self, entry_id: str) -> Any:
        """Resolve a loaded integration, never a cached unloaded instance."""
        coordinator = self.hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None or not hasattr(coordinator, "client"):
            raise web.HTTPNotFound()
        return coordinator

    def context(self, coordinator: Any) -> tuple[Any, ...]:
        """Include map generation and presentation, but no moving telemetry."""
        app_maps = coordinator.app_maps
        index = active_map_index(app_maps)
        if index is None or not app_maps or app_maps.get("map_list_valid") is not True:
            raise web.HTTPConflict(text="The current map identity is not available.")
        identity = _app_map_inventory_identity(app_maps.get("maps") or ())
        if identity is None:
            raise web.HTTPConflict(text="The current map generation is not available.")
        options = coordinator.entry.options
        return (
            index,
            identity,
            map_style(options, index),
            float(options.get(CONF_MAP_LABEL_SCALE, DEFAULT_MAP_LABEL_SCALE)),
        )

    def authorize(self, request: web.Request, entry_id: str) -> Any:
        """Apply the same entity-read permission as the map camera."""
        coordinator = self.coordinator(entry_id)
        user = request.get("hass_user")
        entity_id = er.async_get(self.hass).async_get_entity_id(
            "camera", DOMAIN, f"{coordinator.client.descriptor.unique_id}_map"
        )
        if (
            user is None
            or entity_id is None
            or not user.permissions.check_entity(entity_id, POLICY_READ)
        ):
            raise web.HTTPForbidden()
        return coordinator

    async def scene(self, entry_id: str) -> _SceneEntry:
        """Reuse a recent static scene and share one bounded background refresh."""
        coordinator = self.coordinator(entry_id)
        context = self.context(coordinator)
        cached = self._cache.get(entry_id)
        failure = self._failures.get(entry_id)
        if (
            failure is not None
            and failure[0] is coordinator
            and failure[1] == context
            and time.monotonic() - failure[2] < 15
        ):
            raise web.HTTPBadGateway(
                text="The current map could not be loaded.",
                headers={**_HEADERS, "Retry-After": "15"},
            )
        if (
            cached is not None
            and cached.coordinator is coordinator
            and cached.context == context
            and time.monotonic() - cached.created_at < _CACHE_SECONDS
        ):
            self._cache.move_to_end(entry_id)
            return cached
        task = self._inflight.get(entry_id)
        if task is None:
            if len(self._inflight) >= _MAX_ENTRIES:
                raise web.HTTPServiceUnavailable(headers={"Retry-After": "5"})
            task = self.hass.async_create_task(
                self._load(entry_id, coordinator, context),
                f"{DOMAIN} mowing map background",
            )
            self._inflight[entry_id] = task
            task.add_done_callback(lambda completed: self._finish(entry_id, completed))
        # A disconnected dashboard must not abandon a worker or trigger another
        # cloud read while the original worker is still finishing.
        entry = await asyncio.shield(task)
        if entry.coordinator is not self.coordinator(
            entry_id
        ) or entry.context != self.context(entry.coordinator):
            raise web.HTTPConflict(text="The map changed while loading.")
        return entry

    def _finish(self, entry_id: str, task: asyncio.Task[_SceneEntry]) -> None:
        if self._inflight.get(entry_id) is task:
            self._inflight.pop(entry_id, None)
        if not task.cancelled():
            task.exception()  # Retrieve errors when the last HTTP waiter left.

    async def _load(
        self, entry_id: str, coordinator: Any, context: tuple[Any, ...]
    ) -> _SceneEntry:
        try:
            scene = await coordinator.client.async_get_mowing_map_scene(
                map_index=context[0],
                style=context[2],
                label_scale=context[3],
            )
        except Exception as err:
            self._failures[entry_id] = (coordinator, context, time.monotonic())
            self._failures.move_to_end(entry_id)
            while len(self._failures) > _MAX_ENTRIES:
                self._failures.popitem(last=False)
            raise web.HTTPBadGateway(
                text="The current map could not be loaded.",
                headers={**_HEADERS, "Retry-After": "15"},
            ) from err
        if coordinator is not self.coordinator(entry_id) or context != self.context(
            coordinator
        ):
            raise web.HTTPConflict(text="The map changed while loading.")
        entry = _SceneEntry(coordinator, context, scene, time.monotonic())
        self._failures.pop(entry_id, None)
        self._cache[entry_id] = entry
        self._cache.move_to_end(entry_id)
        while len(self._cache) > _MAX_ENTRIES:
            self._cache.popitem(last=False)
        return entry

    async def purge_entry(self, entry_id: str) -> None:
        """Drop private state and drain a pending read during entry unload."""
        self._cache.pop(entry_id, None)
        self._failures.pop(entry_id, None)
        task = self._inflight.get(entry_id)
        if task is not None:
            # The client read uses a worker thread: cancellation would stop the
            # await, not the worker. Drain it before this entry can be replaced.
            await asyncio.gather(asyncio.shield(task), return_exceptions=True)
        self._cache.pop(entry_id, None)
        self._failures.pop(entry_id, None)


class MowingMapView(HomeAssistantView):
    """Deliver compact overlays and a stable reference to the static background."""

    url = f"{MOWING_MAP_API_PATH}/{{entry_id}}"
    name = "api:dreame_lawn_mower:mowing_map"
    requires_auth = True

    def __init__(self, api: MowingMapAPI) -> None:
        self._api = api

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        self._api.authorize(request, entry_id)
        entry = await self._api.scene(entry_id)
        scene = entry.scene
        return web.json_response(
            {
                "schema_version": 1,
                "revision": scene.revision,
                "map_index": scene.map_index,
                "map_id": scene.map_id,
                "name": scene.name,
                "width": scene.projection.width,
                "height": scene.projection.height,
                "background_path": (
                    f"{mowing_map_api_path(entry_id)}/background/{scene.revision}"
                ),
                "overlay": entry.coordinator.client.mowing_map_runtime_overlay(scene),
            },
            headers=_HEADERS,
        )


class MowingMapBackgroundView(HomeAssistantView):
    """Only serve the exact background generation advertised in a scene."""

    url = f"{MOWING_MAP_API_PATH}/{{entry_id}}/background/{{revision}}"
    name = "api:dreame_lawn_mower:mowing_map_background"
    requires_auth = True

    def __init__(self, api: MowingMapAPI) -> None:
        self._api = api

    async def get(
        self, request: web.Request, entry_id: str, revision: str
    ) -> web.Response:
        coordinator = self._api.authorize(request, entry_id)
        entry = self._api._cache.get(entry_id)
        if (
            entry is None
            or entry.coordinator is not coordinator
            or entry.context != self._api.context(coordinator)
            or entry.scene.revision != revision
        ):
            raise web.HTTPConflict(text="Refresh the map scene before its background.")
        return web.Response(
            body=entry.scene.image_png, content_type="image/png", headers=_HEADERS
        )


@callback
def async_setup_mowing_map_api(hass: HomeAssistant) -> MowingMapAPI:
    """Register read-only map delivery once per HA instance."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    existing = domain_data.get(MOWING_MAP_API_KEY)
    if isinstance(existing, MowingMapAPI):
        return existing
    api = MowingMapAPI(hass)
    hass.http.register_view(MowingMapView(api))
    hass.http.register_view(MowingMapBackgroundView(api))
    domain_data[MOWING_MAP_API_KEY] = api
    return api
