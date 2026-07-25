"""Authenticated, private delivery of mower point-cloud files."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.decorators import require_admin
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .control_options import active_map_index, current_map_index
from .diagnostic_events import record_diagnostic_event
from .dreame_lawn_mower_client import (
    DreameLawnMowerPointCloudDownload,
    DreameLawnMowerPointCloudError,
)
from .performance import format_performance_sample

if TYPE_CHECKING:
    from .coordinator import DreameLawnMowerCoordinator

_LOGGER = logging.getLogger(__name__)

POINT_CLOUD_API_DATA_KEY = "point_cloud_api"
POINT_CLOUD_API_PATH = f"/api/{DOMAIN}/point-cloud"
POINT_CLOUD_CACHE_TTL_SECONDS = 60.0
POINT_CLOUD_CACHE_MAX_ENTRIES = 4
POINT_CLOUD_PROBLEM_SCHEMA_VERSION = 1

_POINT_CLOUD_PROBLEM_STATUS = {
    "point_cloud_generation_in_progress": 409,
    "point_cloud_invalid_request": 400,
    "point_cloud_timeout": 504,
    "point_cloud_not_published": 504,
    "point_cloud_download_invalid": 504,
    "point_cloud_entry_reloaded": 503,
    "point_cloud_download_unsupported": 503,
}
_POINT_CLOUD_PROBLEM_TITLE = {
    "point_cloud_generation_in_progress": "3D map generation already in progress",
    "point_cloud_invalid_request": "Invalid 3D map request",
    "point_cloud_timeout": "3D map generation timed out",
    "point_cloud_not_published": "The mower did not publish a fresh 3D map",
    "point_cloud_download_invalid": "The generated 3D map could not be used",
    "point_cloud_entry_reloaded": "The mower integration was reloaded",
    "point_cloud_download_unsupported": "3D map download is unavailable",
    "point_cloud_mower_request_failed": "The mower rejected the 3D map request",
    "point_cloud_mower_response_invalid": "The mower returned an invalid response",
    "point_cloud_failed": "3D map unavailable",
}


def point_cloud_api_path(entry_id: str, map_index: int) -> str:
    """Return the local authenticated API path advertised to frontends."""
    return f"{POINT_CLOUD_API_PATH}/{entry_id}/{map_index}"


def current_point_cloud_api_path(
    entry_id: str,
    app_maps: Mapping[str, Any] | None,
    batch_device_data: Mapping[str, Any] | None = None,
    *,
    selected_map_index: int | None = None,
) -> str:
    """Return the API path for the coordinator's current map scope."""
    return point_cloud_api_path(
        entry_id,
        current_map_index(
            app_maps,
            batch_device_data,
            selected_map_index=selected_map_index,
        ),
    )


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    """Store a generated point cloud briefly in private process memory."""

    created_at: float
    download: DreameLawnMowerPointCloudDownload


@dataclass(frozen=True, slots=True)
class _InflightGeneration:
    """Track one generation and the config-entry lifetime that started it."""

    epoch: int
    allow_stored: bool
    task: asyncio.Task[DreameLawnMowerPointCloudDownload]


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
        self._cache_expiry_handles: dict[
            tuple[str, int], asyncio.TimerHandle
        ] = {}
        self._inflight: dict[tuple[str, int], _InflightGeneration] = {}
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

        epoch = self._entry_epochs.get(entry_id, 0)
        inflight = self._inflight.get(key)
        if inflight is not None:
            if inflight.epoch == epoch:
                if refresh and inflight.allow_stored:
                    await self._async_wait_for_stored_generation(key, inflight)
                    return await self.async_get(
                        entry_id,
                        map_index,
                        refresh=True,
                    )
                return await asyncio.shield(inflight.task)
            await self._async_discard_stale_generation(key, inflight)
            return await self.async_get(entry_id, map_index, refresh=refresh)

        other_generation = next(
            (
                (other_key, generation)
                for other_key, generation in self._inflight.items()
                if other_key[0] == entry_id
            ),
            None,
        )
        if other_generation is not None:
            other_key, inflight = other_generation
            if inflight.epoch != epoch:
                await self._async_discard_stale_generation(other_key, inflight)
                return await self.async_get(entry_id, map_index, refresh=refresh)
            self._reject_request(
                coordinator,
                DreameLawnMowerPointCloudError(
                    "Another point-cloud generation is already in progress "
                    "for this mower.",
                    code="point_cloud_generation_in_progress",
                    stage="queue",
                    public_message=(
                        "A 3D map is already being generated for this mower."
                    ),
                    retry_after_seconds=5,
                ),
            )

        allow_stored = (
            not refresh
            and _single_active_map_index(coordinator) == map_index
        )
        generation = self._async_generate(
            key,
            coordinator,
            epoch=epoch,
            allow_stored=allow_stored,
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
        inflight = _InflightGeneration(
            epoch=epoch,
            allow_stored=allow_stored,
            task=task,
        )
        self._inflight[key] = inflight
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

    async def _async_wait_for_stored_generation(
        self,
        key: tuple[str, int],
        inflight: _InflightGeneration,
    ) -> None:
        """Wait for stored-capable work before starting a forced refresh."""
        try:
            await asyncio.shield(inflight.task)
        except Exception:  # noqa: BLE001 - the refresh starts independently.
            pass
        if self._inflight.get(key) is inflight:
            self._inflight.pop(key, None)

    async def _async_discard_stale_generation(
        self,
        key: tuple[str, int],
        inflight: _InflightGeneration,
    ) -> None:
        """Wait for old-entry work to finish without returning its result."""
        try:
            await asyncio.shield(inflight.task)
        except Exception:  # noqa: BLE001 - stale result and details are discarded.
            pass
        if self._inflight.get(key) is inflight:
            self._inflight.pop(key, None)

    @callback
    def purge_entry(self, entry_id: str) -> None:
        """Discard private cache state for one unloaded entry."""
        for key in [key for key in self._cache if key[0] == entry_id]:
            self._remove_cache_entry(key)
        self._entry_epochs[entry_id] = self._entry_epochs.get(entry_id, 0) + 1

    async def _async_generate(
        self,
        key: tuple[str, int],
        coordinator: DreameLawnMowerCoordinator,
        *,
        epoch: int,
        allow_stored: bool,
    ) -> DreameLawnMowerPointCloudDownload:
        """Run and cache one generation independently of HTTP waiters."""
        performance = getattr(coordinator, "performance", None)
        cycle = (
            performance.start("point_cloud_generation")
            if hasattr(performance, "start")
            else None
        )
        try:
            if self._entry_epochs.get(key[0], 0) != epoch:
                raise DreameLawnMowerPointCloudError(
                    "The mower entry changed before point-cloud generation started.",
                    code="point_cloud_entry_reloaded",
                    stage="lifecycle",
                    public_message=(
                        "The mower integration was reloaded before the 3D map "
                        "request started."
                    ),
                    retry_after_seconds=2,
                )
            if cycle is not None:
                download = await cycle.measure(
                    "generate_download_validate",
                    lambda: coordinator.client.async_download_app_map_point_cloud(
                        map_index=key[1],
                        allow_stored=allow_stored,
                    ),
                )
            else:
                download = (
                    await coordinator.client.async_download_app_map_point_cloud(
                        map_index=key[1],
                        allow_stored=allow_stored,
                    )
                )
            if self._entry_epochs.get(key[0], 0) != epoch:
                raise DreameLawnMowerPointCloudError(
                    "The mower entry changed during point-cloud generation.",
                    code="point_cloud_entry_reloaded",
                    stage="lifecycle",
                    public_message=(
                        "The mower integration was reloaded while generating the "
                        "3D map."
                    ),
                    retry_after_seconds=2,
                )
        except DreameLawnMowerPointCloudError as err:
            sample = cycle.finish(outcome=err.code) if cycle is not None else None
            self._record_generation_failure(coordinator, err, sample)
            raise
        except Exception as err:
            public_error = DreameLawnMowerPointCloudError(
                "Unexpected point-cloud generation failure.",
                code="point_cloud_failed",
                stage="generation",
                public_message="The mower point cloud is temporarily unavailable.",
                retry_after_seconds=10,
            )
            sample = (
                cycle.finish(outcome=public_error.code)
                if cycle is not None
                else None
            )
            self._record_generation_failure(coordinator, public_error, sample)
            raise public_error from err
        else:
            if cycle is not None:
                sample = cycle.finish()
                total, phases = format_performance_sample(sample)
                _LOGGER.info(
                    "Dreame mower performance: operation=point_cloud_generation "
                    "outcome=completed total=%.3fs phases=%s",
                    total,
                    phases,
                )

        created_at = time.monotonic()
        self._remove_cache_entry(key)
        self._cache[key] = _CacheEntry(
            created_at=created_at,
            download=download,
        )
        self._cache_expiry_handles[key] = asyncio.get_running_loop().call_later(
            max(0.0, self._cache_ttl),
            self._expire_cache_entry,
            key,
            created_at,
        )
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_max_entries:
            oldest_key = next(iter(self._cache))
            self._remove_cache_entry(oldest_key)
        return download

    def _reject_request(
        self,
        coordinator: DreameLawnMowerCoordinator,
        error: DreameLawnMowerPointCloudError,
    ) -> NoReturn:
        """Record and raise a safe request-level point-cloud failure."""
        record_diagnostic_event(
            coordinator,
            code=error.code,
            source="point_cloud_api",
            message=error.public_message,
            context={
                "stage": error.stage,
                "retryable": error.retryable,
                "retry_after_seconds": error.retry_after_seconds,
            },
        )
        _LOGGER.warning(
            "Dreame point-cloud request failed: code=%s stage=%s retryable=%s",
            error.code,
            error.stage,
            error.retryable,
        )
        raise error

    def _record_generation_failure(
        self,
        coordinator: DreameLawnMowerCoordinator,
        error: DreameLawnMowerPointCloudError,
        sample: Any,
    ) -> None:
        """Keep one privacy-safe failure event and benchmark sample."""
        context: dict[str, Any] = {
            "stage": error.stage,
            "retryable": error.retryable,
            "retry_after_seconds": error.retry_after_seconds,
            "timeout_seconds": error.timeout_seconds,
        }
        if sample is not None:
            context["duration_ms"] = round(sample.total_seconds * 1000, 1)
            total, phases = format_performance_sample(sample)
        else:
            total, phases = 0.0, "none"
        record_diagnostic_event(
            coordinator,
            code=error.code,
            source="point_cloud_api",
            message=error.public_message,
            context=context,
        )
        _LOGGER.warning(
            "Dreame mower performance: operation=point_cloud_generation "
            "outcome=%s total=%.3fs phases=%s stage=%s retryable=%s",
            error.code,
            total,
            phases,
            error.stage,
            error.retryable,
        )

    @callback
    def _generation_done(
        self,
        key: tuple[str, int],
        task: asyncio.Task[DreameLawnMowerPointCloudDownload],
    ) -> None:
        """Retire an in-flight generation only after its actual work finishes."""
        inflight = self._inflight.get(key)
        if inflight is not None and inflight.task is task:
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
            self._remove_cache_entry(key)
            return None
        self._cache.move_to_end(key)
        return cached

    @callback
    def _expire_cache_entry(
        self,
        key: tuple[str, int],
        created_at: float,
    ) -> None:
        """Evict only the cache generation scheduled by this timer."""
        cached = self._cache.get(key)
        if cached is not None and cached.created_at == created_at:
            self._remove_cache_entry(key)

    @callback
    def _remove_cache_entry(self, key: tuple[str, int]) -> None:
        """Remove one cache entry and cancel its pending expiry timer."""
        self._cache.pop(key, None)
        handle = self._cache_expiry_handles.pop(key, None)
        if handle is not None:
            handle.cancel()


def _single_active_map_index(
    coordinator: DreameLawnMowerCoordinator,
) -> int | None:
    """Return the active map only when stored-object identity is unambiguous."""
    app_maps = getattr(coordinator, "app_maps", None)
    if not isinstance(app_maps, Mapping):
        return None
    maps = app_maps.get("maps")
    if not isinstance(maps, Sequence) or isinstance(
        maps,
        str | bytes | bytearray,
    ):
        return None
    indices = {
        entry.get("idx")
        for entry in maps
        if isinstance(entry, Mapping)
        and isinstance(entry.get("idx"), int)
        and not isinstance(entry.get("idx"), bool)
        and entry["idx"] >= 0
        and entry.get("created") is not False
    }
    if len(indices) != 1:
        return None
    only_index = next(iter(indices))
    active_index = active_map_index(
        app_maps,
        selected_map_index=getattr(
            coordinator,
            "selected_map_index",
            None,
        ),
    )
    return only_index if active_index == only_index else None


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
        started_at = time.monotonic()
        try:
            normalized_index = int(map_index)
        except ValueError:
            return _point_cloud_problem_response(
                DreameLawnMowerPointCloudError(
                    "Invalid point-cloud map index.",
                    code="point_cloud_invalid_request",
                    stage="request",
                    retryable=False,
                    public_message="The 3D map request contains an invalid map index.",
                ),
                elapsed_seconds=time.monotonic() - started_at,
            )
        if not 0 <= normalized_index <= 255:
            return _point_cloud_problem_response(
                DreameLawnMowerPointCloudError(
                    "Invalid point-cloud map index.",
                    code="point_cloud_invalid_request",
                    stage="request",
                    retryable=False,
                    public_message="The 3D map request contains an invalid map index.",
                ),
                elapsed_seconds=time.monotonic() - started_at,
            )

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
            return _point_cloud_problem_response(
                err,
                elapsed_seconds=time.monotonic() - started_at,
            )
        except Exception:  # noqa: BLE001 - keep private cloud details private.
            elapsed_seconds = time.monotonic() - started_at
            _LOGGER.warning(
                "Dreame point-cloud request failed: code=point_cloud_failed "
                "stage=delivery elapsed=%.3fs retryable=True",
                elapsed_seconds,
            )
            return _point_cloud_problem_response(
                DreameLawnMowerPointCloudError(
                    "Unexpected point-cloud delivery failure.",
                    code="point_cloud_failed",
                    stage="delivery",
                    public_message=(
                        "The mower point cloud is temporarily unavailable."
                    ),
                    retry_after_seconds=10,
                ),
                elapsed_seconds=elapsed_seconds,
            )

        elapsed_ms = round((time.monotonic() - started_at) * 1000, 1)
        return web.Response(
            body=download.content,
            content_type="application/octet-stream",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": (
                    f'attachment; filename="dreame-map-{normalized_index}.pcd"'
                ),
                "X-Content-Type-Options": "nosniff",
                "X-Dreame-Operation-Elapsed-Ms": str(elapsed_ms),
                "Server-Timing": f"dreame-point-cloud;dur={elapsed_ms}",
            },
        )


def _point_cloud_problem_response(
    error: DreameLawnMowerPointCloudError,
    *,
    elapsed_seconds: float,
) -> web.Response:
    """Return a bounded, machine-readable, privacy-safe failure response."""
    status = _POINT_CLOUD_PROBLEM_STATUS.get(error.code, 502)
    elapsed_ms = round(max(0.0, elapsed_seconds) * 1000, 1)
    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Dreame-Problem-Code": error.code,
        "X-Dreame-Problem-Stage": error.stage,
        "X-Dreame-Operation-Elapsed-Ms": str(elapsed_ms),
        "Server-Timing": f"dreame-point-cloud;dur={elapsed_ms}",
    }
    if error.retry_after_seconds is not None:
        headers["Retry-After"] = str(error.retry_after_seconds)
    return web.json_response(
        {
            "schema_version": POINT_CLOUD_PROBLEM_SCHEMA_VERSION,
            "title": _POINT_CLOUD_PROBLEM_TITLE.get(
                error.code,
                "3D map unavailable",
            ),
            "status": status,
            "detail": error.public_message,
            "code": error.code,
            "stage": error.stage,
            "retryable": error.retryable,
            "retry_after_seconds": error.retry_after_seconds,
            "elapsed_ms": elapsed_ms,
            "timeout_seconds": error.timeout_seconds,
        },
        status=status,
        content_type="application/problem+json",
        headers=headers,
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
