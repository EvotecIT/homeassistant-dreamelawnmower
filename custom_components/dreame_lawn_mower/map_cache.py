"""Shared cache helpers for Home Assistant map camera entities."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from .dreame_lawn_mower_client.models import DreameLawnMowerMapView

MapViewRefresh = Callable[[], Awaitable[DreameLawnMowerMapView]]


def map_camera_should_refresh(
    *,
    context_changed: bool,
    runtime_active: bool,
    manages_cached_view: bool = True,
    demand_active: bool = True,
) -> bool:
    """Return whether a coordinator update should refresh a cached map view."""
    return manages_cached_view and demand_active and (context_changed or runtime_active)


def map_camera_refresh_demand_active(
    last_request_at: float | None,
    *,
    now: float,
    window_seconds: float,
) -> bool:
    """Return whether a recent image request warrants background refreshes."""
    if last_request_at is None or window_seconds <= 0:
        return False
    age = now - last_request_at
    return 0 <= age <= window_seconds


def map_camera_followup_refresh_required(
    *,
    pending: bool,
    available: bool,
) -> bool:
    """Return whether a context change queued during rendering needs a follow-up."""
    return pending and available


def map_camera_available(
    snapshot: Any,
    *,
    image_cached: bool,
    requires_map_capability: bool = True,
) -> bool:
    """Return whether a map camera may expose live or cached map data."""
    if snapshot is None or not getattr(snapshot, "available", False):
        return False
    if not requires_map_capability:
        return True
    return bool(
        image_cached
        or getattr(snapshot, "mapping_available", False)
        or "map" in getattr(snapshot, "capabilities", ())
    )


@dataclass(slots=True)
class DreameLawnMowerMapCameraCache:
    """Shared map-view and image cache for the paired map camera entities."""

    ttl: timedelta
    last_image: bytes | None = None
    last_image_source_sha256: str | None = None
    last_image_render_context: Any = None
    last_image_is_placeholder: bool = False
    last_view: DreameLawnMowerMapView | None = None
    last_refresh_at: datetime | None = None
    last_error: str | None = None
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _generation: int = 0

    def is_fresh(self, now: datetime | None = None) -> bool:
        """Return whether the cached map data is still fresh."""
        if self.last_refresh_at is None:
            return False
        return ((now or datetime.now(UTC)) - self.last_refresh_at) <= self.ttl

    async def async_get_view(
        self,
        refresh_view: MapViewRefresh,
        *,
        now: datetime | None = None,
    ) -> DreameLawnMowerMapView:
        """Return the cached map view or refresh it once under a shared lock."""
        if self.last_view is not None and self.is_fresh(now):
            return self.last_view

        async with self._refresh_lock:
            if self.last_view is not None and self.is_fresh(now):
                return self.last_view

            generation = self._generation
            view = await refresh_view()
            if generation == self._generation:
                self.store_view(view, now=now)
            return view

    def store_view(
        self,
        view: DreameLawnMowerMapView,
        *,
        now: datetime | None = None,
    ) -> None:
        """Store a successful or diagnostic map view."""
        self.last_view = view
        self.last_error = view.error
        self.last_refresh_at = now or datetime.now(UTC)

    def store_error(
        self,
        error: str,
        *,
        source: str = "legacy_current_map",
        now: datetime | None = None,
    ) -> DreameLawnMowerMapView:
        """Store an error view and return it."""
        view = DreameLawnMowerMapView(source=source, error=error)
        self.store_view(view, now=now)
        return self.last_view

    def image_matches_source(
        self,
        source_image: bytes,
        *,
        render_context: Any = None,
    ) -> bool:
        """Return whether the JPEG cache was rendered from these source bytes."""
        return bool(
            self.last_image is not None
            and self.last_image_source_sha256 == sha256(source_image).hexdigest()
            and self.last_image_render_context == render_context
        )

    def view_image_needs_render(self, *, render_context: Any = None) -> bool:
        """Return whether the current map view differs from the rendered JPEG."""
        view = self.last_view
        return bool(
            view is not None
            and view.image_png is not None
            and not self.image_matches_source(
                view.image_png,
                render_context=render_context,
            )
        )

    def store_image(
        self,
        image: bytes,
        *,
        source_image: bytes | None = None,
        render_context: Any = None,
        placeholder: bool = False,
    ) -> None:
        """Store rendered JPEG bytes for reuse by both map camera entities."""
        self.last_image = image
        self.last_image_source_sha256 = (
            sha256(source_image).hexdigest() if source_image is not None else None
        )
        self.last_image_render_context = render_context
        self.last_image_is_placeholder = placeholder

    def invalidate_view(self, *, drop_image: bool = False) -> None:
        """Expire a view, discarding geometry when its map identity changed."""
        self._generation += 1
        self.last_refresh_at = None
        if drop_image:
            self.last_view = None
            self.last_image = None
            self.last_image_source_sha256 = None
            self.last_image_render_context = None
            self.last_image_is_placeholder = False
            self.last_error = None
