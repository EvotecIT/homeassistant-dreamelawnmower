"""Authenticated read-only map layers, with no geometry in HA recorder state."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from io import BytesIO
from typing import Any

from aiohttp import web
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.http import HomeAssistantView

from .const import (
    CONF_MAP_BACKGROUND,
    CONF_MAP_LABEL_SCALE,
    DEFAULT_MAP_BACKGROUND,
    DEFAULT_MAP_LABEL_SCALE,
    DOMAIN,
)
from .control_options import active_map_index
from .dreame_lawn_mower_client.map_inventory import (
    _app_map_inventory_identity,
    selected_map_revision,
)
from .dreame_lawn_mower_client.map_projection import MapProjection
from .dreame_lawn_mower_client.mowing_map import MowingMapScene
from .dreame_lawn_mower_client.real_scene import RealSceneImage, paint_real_scene
from .dreame_lawn_mower_client.zone_mowing_history import (
    lineage_render_matches_history,
    reported_charger_for_map,
)
from .georeference import entry_georeference
from .map_presentation import map_style
from .real_scene_cache import real_scene_owner
from .runtime_data import get_coordinator

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
        self._photo_layers: OrderedDict[tuple[str, str, str], bytes] = OrderedDict()
        self._photo_inflight: dict[tuple[str, str, str], asyncio.Task[bytes]] = {}
        self._photo_render_lock = asyncio.Lock()

    def coordinator(self, entry_id: str) -> Any:
        """Resolve a loaded integration, never a cached unloaded instance."""
        coordinator = get_coordinator(self.hass, entry_id)
        if coordinator is None:
            raise web.HTTPNotFound()
        return coordinator

    def context(self, coordinator: Any) -> tuple[Any, ...]:
        """Include map generation and presentation, but no moving telemetry."""
        app_maps = coordinator.app_maps
        switch_event = getattr(coordinator, "_last_map_switch_event_at", None)
        if getattr(
            coordinator, "_map_switch_inventory_pending_event", None
        ) is not None:
            raise web.HTTPConflict(text="The switched map inventory is not available.")
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
            switch_event,
            # The station is drawn into the background once a summary reports it.
            reported_charger_for_map(
                getattr(coordinator.client, "_charger_history", None), app_maps, index,
                getattr(coordinator.client, "_map_lineage", None),
            ),
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
        real_scene_owner(coordinator).request()
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
        history = getattr(coordinator.client, "_charger_history", None)
        try:
            scene = await coordinator.client.async_get_mowing_map_scene(
                map_index=context[0],
                style=context[2],
                label_scale=context[3],
                app_maps=coordinator.app_maps,
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
        if coordinator is not self.coordinator(entry_id):
            raise web.HTTPConflict(text="The map changed while loading.")
        current = self.context(coordinator)
        charger = current[-1]
        if (
            context[-1] is None and context[:-1] == current[:-1]
            and lineage_render_matches_history(
                history, getattr(coordinator.client, "_charger_history", None),
                scene.charger, (charger.x, charger.y) if charger is not None else None,
                scene.bounds,
            )
        ):
            context = current
        if context != current:
            raise web.HTTPConflict(text="The map changed while loading.")
        entry = _SceneEntry(coordinator, context, scene, time.monotonic())
        self._failures.pop(entry_id, None)
        self._cache[entry_id] = entry
        self._cache.move_to_end(entry_id)
        while len(self._cache) > _MAX_ENTRIES:
            self._cache.popitem(last=False)
        return entry

    def _require_photo_context(
        self, entry_id: str, entry: _SceneEntry, digest: str,
    ) -> None:
        coordinator = entry.coordinator
        if (
            coordinator is not self.coordinator(entry_id)
            or self._cache.get(entry_id) is not entry
            or entry.context != self.context(coordinator)
        ):
            raise web.HTTPConflict(text="The map changed while its photo was loading.")
        photo = _real_scene_for(entry)
        if photo is None or photo.digest != digest:
            raise web.HTTPNotFound(headers=_HEADERS)

    async def photo_layer(
        self, entry_id: str, entry: _SceneEntry, photo: RealSceneImage,
    ) -> bytes:
        """Bound and coalesce photo transforms; disconnected waiters retain work."""
        self._require_photo_context(entry_id, entry, photo.digest)
        key = (entry_id, entry.scene.revision, photo.digest)
        layer = self._photo_layers.get(key)
        if layer is not None:
            self._photo_layers.move_to_end(key)
            return layer
        task = self._photo_inflight.get(key)
        if task is None:
            if len(self._photo_inflight) >= _MAX_ENTRIES:
                raise web.HTTPServiceUnavailable(headers={"Retry-After": "5"})
            task = self.hass.async_create_task(
                self._load_photo_layer(entry_id, entry, photo, key),
                f"{DOMAIN} real-scene layer",
            )
            self._photo_inflight[key] = task
            task.add_done_callback(lambda done: self._finish_photo_layer(key, done))
        layer = await asyncio.shield(task)
        self._require_photo_context(entry_id, entry, photo.digest)
        return layer

    async def _load_photo_layer(
        self, entry_id: str, entry: _SceneEntry, photo: RealSceneImage,
        key: tuple[str, str, str],
    ) -> bytes:
        async with self._photo_render_lock:
            self._require_photo_context(entry_id, entry, photo.digest)
            layer = await self.hass.async_add_executor_job(
                _render_real_scene_layer, entry.scene, photo,
            )
        self._require_photo_context(entry_id, entry, photo.digest)
        self._photo_layers[key] = layer
        while len(self._photo_layers) > _MAX_ENTRIES:
            self._photo_layers.popitem(last=False)
        return layer

    def _finish_photo_layer(
        self, key: tuple[str, str, str], task: asyncio.Task[bytes],
    ) -> None:
        if self._photo_inflight.get(key) is task:
            self._photo_inflight.pop(key)
        if not task.cancelled():
            task.exception()

    async def purge_entry(self, entry_id: str) -> None:
        """Drop private state and drain a pending read during entry unload."""
        self._cache.pop(entry_id, None)
        self._failures.pop(entry_id, None)
        photos = [task for key, task in self._photo_inflight.items()
                  if key[0] == entry_id]
        if photos:
            await asyncio.gather(*(asyncio.shield(task) for task in photos),
                                 return_exceptions=True)
        for key in tuple(self._photo_layers):
            if key[0] == entry_id:
                self._photo_layers.pop(key)
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
                "map_revision": selected_map_revision(
                    entry.coordinator.app_maps, scene.map_index
                ),
                "map_id": scene.map_id,
                "name": scene.name,
                "width": scene.projection.width,
                "height": scene.projection.height,
                "background_path": (
                    f"{mowing_map_api_path(entry_id)}/background/{scene.revision}"
                ),
                "overlay": entry.coordinator.client.mowing_map_runtime_overlay(scene),
                "real_scene_path": _real_scene_path(entry_id, entry),
                "projection": projection_payload(scene.projection),
                "zones": [label.as_dict() for label in scene.zone_labels],
                "georeference": _georeference_payload(
                    entry.coordinator, scene.map_index
                ),
            },
            headers=_HEADERS,
        )


def projection_payload(projection: MapProjection) -> dict[str, Any]:
    """Publish the exact native-to-pixel transform of the background image.

    Pixel = rotate(((max_x - x) * scale + padding, (y - min_y) * scale +
    padding)) inside a native_width x native_height canvas, rotated clockwise
    by ``rotation`` degrees. Native X is mirrored by the renderer.
    """
    return asdict(projection)


def _georeference_payload(
    coordinator: Any, map_index: int
) -> dict[str, float] | None:
    """Share the entry's calibration so the card and the tracker agree."""
    georeference = entry_georeference(
        coordinator.entry, coordinator.app_maps, map_index
    )
    return georeference.as_dict() if georeference is not None else None


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
            body=background_image(entry.scene, request),
            content_type="image/png",
            headers=_HEADERS,
        )


def background_image(scene: MowingMapScene, request: Any) -> bytes:
    """Return the background, without zone names when ``labels=0`` is asked.

    Both variants are rendered with the scene and share its revision, so the
    scene cache already holds the choice and no request renders anything.
    """
    query = getattr(request, "query", None) or {}
    if query.get("labels") == "0" and scene.image_png_unlabeled:
        return scene.image_png_unlabeled
    return scene.image_png


def _real_scene_for(entry: _SceneEntry) -> RealSceneImage | None:
    """Return the camera's photo only when it belongs to this scene's map."""
    coordinator = entry.coordinator
    real_scene = real_scene_owner(coordinator).current
    if (
        real_scene is None
        or coordinator.entry.options.get(CONF_MAP_BACKGROUND, DEFAULT_MAP_BACKGROUND)
        != "real_scene"
        or real_scene.map_index != entry.scene.map_index
    ):
        return None
    return real_scene


def _real_scene_path(entry_id: str, entry: _SceneEntry) -> str | None:
    real_scene = _real_scene_for(entry)
    if real_scene is None:
        return None
    return (
        f"{mowing_map_api_path(entry_id)}/real-scene/"
        f"{entry.scene.revision}/{real_scene.digest}"
    )


def _render_real_scene_layer(
    scene: MowingMapScene, real_scene: RealSceneImage
) -> bytes:
    """Warp the photo into the exact canvas of the transparent background."""
    from PIL import Image

    canvas = Image.new(
        "RGBA", (scene.projection.width, scene.projection.height), (0, 0, 0, 0)
    )
    if not paint_real_scene(
        canvas, real_scene, scene.projection.point_exact, opacity=1.0
    ):
        return b""
    output = BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()


class MowingMapRealSceneView(HomeAssistantView):
    """Serve the real-scene photo layer for one advertised background."""

    url = f"{MOWING_MAP_API_PATH}/{{entry_id}}/real-scene/{{revision}}/{{digest}}"
    name = "api:dreame_lawn_mower:mowing_map_real_scene"
    requires_auth = True

    def __init__(self, api: MowingMapAPI) -> None:
        self._api = api

    async def get(
        self, request: web.Request, entry_id: str, revision: str, digest: str
    ) -> web.Response:
        coordinator = self._api.authorize(request, entry_id)
        entry = self._api._cache.get(entry_id)
        if (
            entry is None
            or entry.coordinator is not coordinator
            or entry.context != self._api.context(coordinator)
            or entry.scene.revision != revision
        ):
            raise web.HTTPConflict(text="Refresh the map scene before its photo.")
        real_scene = _real_scene_for(entry)
        if real_scene is None or real_scene.digest != digest:
            raise web.HTTPNotFound(headers=_HEADERS)
        layer = await self._api.photo_layer(entry_id, entry, real_scene)
        if not layer:
            raise web.HTTPNotFound(headers=_HEADERS)
        return web.Response(body=layer, content_type="image/png", headers=_HEADERS)


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
    hass.http.register_view(MowingMapRealSceneView(api))
    domain_data[MOWING_MAP_API_KEY] = api
    return api
