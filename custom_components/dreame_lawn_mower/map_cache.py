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
        view = DreameLawnMowerMapView(source=source, error=error)
        self.store_view(view, now=now)
        return self.last_view

    def image_matches_source(self, source_image: bytes) -> bool:
        """Return whether the JPEG cache was rendered from these source bytes."""
        return bool(
            self.last_image is not None
            and self.last_image_source_sha256 == sha256(source_image).hexdigest()
        )

    def view_image_needs_render(self) -> bool:
        """Return whether the current map view differs from the rendered JPEG."""
        view = self.last_view
        return bool(
            view is not None
            and view.image_png is not None
            and not self.image_matches_source(view.image_png)
        )

    def store_image(self, image: bytes, *, source_image: bytes | None = None) -> None:
        """Store rendered JPEG bytes for reuse by both map camera entities."""
        self.last_image = image
        self.last_image_source_sha256 = (
            sha256(source_image).hexdigest() if source_image is not None else None
        )

    def invalidate_view(self) -> None:
        """Expire source metadata while preserving the last good image."""
        self.last_refresh_at = None
