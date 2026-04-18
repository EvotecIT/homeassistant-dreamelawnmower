"""Shared cache helpers for Home Assistant map camera entities."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .dreame_client.models import DreameLawnMowerMapView

MapViewRefresh = Callable[[], Awaitable[DreameLawnMowerMapView]]


@dataclass(slots=True)
class DreameLawnMowerMapCameraCache:
    """Shared map-view and image cache for the paired map camera entities."""

    ttl: timedelta
    last_image: bytes | None = None
    last_view: DreameLawnMowerMapView | None = None
    last_refresh_at: datetime | None = None
    last_error: str | None = None
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

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

            view = await refresh_view()
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
        self.last_error = error
        self.last_refresh_at = now or datetime.now(UTC)
        self.last_view = DreameLawnMowerMapView(source=source, error=error)
        return self.last_view

    def store_image(self, image: bytes) -> None:
        """Store rendered JPEG bytes for reuse by both map camera entities."""
        self.last_image = image
