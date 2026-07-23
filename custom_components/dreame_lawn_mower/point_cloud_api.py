"""Authenticated, private delivery of mower point-cloud files."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.decorators import require_admin
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .dreame_lawn_mower_client import (
    DreameLawnMowerPointCloudDownload,
    DreameLawnMowerPointCloudError,
)

if TYPE_CHECKING:
    from .coordinator import DreameLawnMowerCoordinator

_LOGGER = logging.getLogger(__name__)

POINT_CLOUD_API_DATA_KEY = "point_cloud_api"
POINT_CLOUD_API_PATH = f"/api/{DOMAIN}/point-cloud"
POINT_CLOUD_CACHE_TTL_SECONDS = 60.0
POINT_CLOUD_CACHE_MAX_ENTRIES = 4


def point_cloud_api_path(entry_id: str, map_index: int) -> str:
    """Return the local authenticated API path advertised to frontends."""
    return f"{POINT_CLOUD_API_PATH}/{entry_id}/{map_index}"


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    """Store a generated point cloud briefly in private process memory."""

    created_at: float
    download: DreameLawnMowerPointCloudDownload


class DreameLawnMowerPointCloudAPI:
    """Coordinate bounded, de-duplicated point-cloud downloads."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        cache_ttl: float = POINT_CLOUD_CACHE_TTL_SECONDS,
        cache_max_entries: int = POINT_CLOUD_CACHE_MAX_ENTRIES,
    ) -> None:
        self._hass = hass
        self._cache_ttl = cache_ttl
        self._cache_max_entries = cache_max_entries
        self._cache: OrderedDict[tuple[str, int], _CacheEntry] = OrderedDict()
        self._inflight: dict[
            tuple[str, int],
            asyncio.Task[DreameLawnMowerPointCloudDownload],
        ] = {}
        self._entry_epochs: dict[str, int] = {}

    async def async_get(
        self,
        entry_id: str,
        map_index: int,
        *,
        refresh: bool = False,
    ) -> DreameLawnMowerPointCloudDownload:
        """Return one private point cloud, reusing recent/in-flight work."""
        coordinator = self._coordinator(entry_id)
        key = (entry_id, map_index)
        requested_at = time.monotonic()
        cached = self._fresh_cache_entry(key, requested_at)
        if cached is not None and not refresh:
            return cached.download

        task = self._inflight.get(key)
        if task is None:
            epoch = self._entry_epochs.get(entry_id, 0)
            generation = self._async_generate(
                key,
                coordinator,
                epoch=epoch,
            )
            create_task = getattr(self._hass, "async_create_task", None)
            if callable(create_task):
                task = create_task(
                    generation,
                    name=f"{DOMAIN} point cloud {entry_id}:{map_index}",
                )
            else:
                task = asyncio.create_task(
                    generation,
                    name=f"{DOMAIN} point cloud {entry_id}:{map_index}",
                )
            self._inflight[key] = task
            task.add_done_callback(
                lambda completed, *, inflight_key=key: self._generation_done(
                    inflight_key,
                    completed,
                )
            )

        # The HTTP requester may disconnect while asyncio.to_thread keeps running.
        # Shield the shared generation so another request joins it instead of
        # launching a second mower-side job.
        return await asyncio.shield(task)

    @callback
    def purge_entry(self, entry_id: str) -> None:
        """Discard private cache state for one unloaded entry."""
        for key in [key for key in self._cache if key[0] == entry_id]:
            self._cache.pop(key, None)
        self._entry_epochs[entry_id] = self._entry_epochs.get(entry_id, 0) + 1

    async def _async_generate(
        self,
        key: tuple[str, int],
        coordinator: DreameLawnMowerCoordinator,
        *,
        epoch: int,
    ) -> DreameLawnMowerPointCloudDownload:
        """Run and cache one generation independently of HTTP waiters."""
        download = await coordinator.client.async_download_app_map_point_cloud(
            map_index=key[1]
        )
        if self._entry_epochs.get(key[0], 0) == epoch:
            self._cache[key] = _CacheEntry(
                created_at=time.monotonic(),
                download=download,
            )
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_max_entries:
                self._cache.popitem(last=False)
        return download

    @callback
    def _generation_done(
        self,
        key: tuple[str, int],
        task: asyncio.Task[DreameLawnMowerPointCloudDownload],
    ) -> None:
        """Retire an in-flight generation only after its actual work finishes."""
        if self._inflight.get(key) is task:
            self._inflight.pop(key, None)
        if not task.cancelled():
            task.exception()

    def _coordinator(self, entry_id: str) -> DreameLawnMowerCoordinator:
        coordinator = self._hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None or not hasattr(coordinator, "client"):
            raise web.HTTPNotFound(text="Dreame lawn mower entry not found.")
        return coordinator

    def _fresh_cache_entry(
        self,
        key: tuple[str, int],
        now: float,
    ) -> _CacheEntry | None:
        cached = self._cache.get(key)
        if cached is None:
            return None
        if now - cached.created_at >= self._cache_ttl:
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return cached


class DreameLawnMowerPointCloudView(HomeAssistantView):
    """Serve a generated PCD file to authenticated Home Assistant admins."""

    url = f"{POINT_CLOUD_API_PATH}/{{entry_id}}/{{map_index}}"
    name = "api:dreame_lawn_mower:point_cloud"
    requires_auth = True

    def __init__(self, api: DreameLawnMowerPointCloudAPI) -> None:
        self._api = api

    @require_admin
    async def get(
        self,
        request: web.Request,
        entry_id: str,
        map_index: str,
    ) -> web.Response:
        """Generate and return one private PCD download."""
        try:
            normalized_index = int(map_index)
        except ValueError as err:
            raise web.HTTPBadRequest(text="Invalid point-cloud map index.") from err
        if not 0 <= normalized_index <= 255:
            raise web.HTTPBadRequest(text="Invalid point-cloud map index.")

        refresh = request.query.get("refresh") == "1"
        try:
            download = await self._api.async_get(
                entry_id,
                normalized_index,
                refresh=refresh,
            )
        except web.HTTPException:
            raise
        except DreameLawnMowerPointCloudError as err:
            _LOGGER.warning("Dreame point-cloud generation or validation failed")
            raise web.HTTPBadGateway(
                text="The mower point cloud is temporarily unavailable."
            ) from err
        except Exception as err:  # noqa: BLE001 - keep private cloud details private.
            _LOGGER.warning("Unexpected Dreame point-cloud generation failure")
            raise web.HTTPBadGateway(
                text="The mower point cloud is temporarily unavailable."
            ) from err

        return web.Response(
            body=download.content,
            content_type="application/octet-stream",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": (
                    f'attachment; filename="dreame-map-{normalized_index}.pcd"'
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )


@callback
def async_setup_point_cloud_api(
    hass: HomeAssistant,
) -> DreameLawnMowerPointCloudAPI:
    """Register the point-cloud view once per Home Assistant instance."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    existing = domain_data.get(POINT_CLOUD_API_DATA_KEY)
    if isinstance(existing, DreameLawnMowerPointCloudAPI):
        return existing

    api = DreameLawnMowerPointCloudAPI(hass)
    hass.http.register_view(DreameLawnMowerPointCloudView(api))
    domain_data[POINT_CLOUD_API_DATA_KEY] = api
    return api
