"""Unit tests for Home Assistant map camera helpers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from custom_components.dreame_lawn_mower.map_attributes import map_camera_attributes
from custom_components.dreame_lawn_mower.map_cache import DreameLawnMowerMapCameraCache
from dreame_lawn_mower_client.models import (
    DreameLawnMowerMapSummary,
    DreameLawnMowerMapView,
)


def test_map_camera_attributes_include_app_map_summary_counts() -> None:
    """Camera attributes preserve app-map counts validated from live payloads."""
    refreshed_at = datetime(2026, 4, 18, 12, 30, tzinfo=UTC)
    view = DreameLawnMowerMapView(
        source="app_action_map",
        summary=DreameLawnMowerMapSummary(
            available=True,
            map_id=0,
            width=521,
            height=900,
            segment_count=2,
            active_area_count=2,
            spot_area_count=2,
            no_go_area_count=0,
            path_point_count=63,
            robot_present=True,
            charger_present=True,
        ),
        image_png=b"png",
    )

    attributes = map_camera_attributes(
        view,
        image_cached=True,
        refreshed_at=refreshed_at,
        last_error=None,
    )

    assert attributes["map_cached"] is True
    assert attributes["map_placeholder"] is False
    assert attributes["map_source"] == "app_action_map"
    assert attributes["map_has_image"] is True
    assert attributes["map_available"] is True
    assert attributes["map_id"] == 0
    assert attributes["width"] == 521
    assert attributes["height"] == 900
    assert attributes["segment_count"] == 2
    assert attributes["active_area_count"] == 2
    assert attributes["spot_area_count"] == 2
    assert attributes["no_go_area_count"] == 0
    assert attributes["path_point_count"] == 63
    assert attributes["robot_present"] is True
    assert attributes["charger_present"] is True
    assert attributes["last_map_refresh"] == "2026-04-18T12:30:00+00:00"


def test_map_camera_attributes_report_placeholder_without_view() -> None:
    """Empty cache attributes remain explicit for HA diagnostics."""
    attributes = map_camera_attributes(
        None,
        image_cached=False,
        refreshed_at=None,
        last_error="offline",
    )

    assert attributes["map_cached"] is False
    assert attributes["map_placeholder"] is True
    assert attributes["map_source"] is None
    assert attributes["map_has_image"] is False
    assert attributes["map_error"] == "offline"
    assert attributes["map_available"] is None
    assert attributes["spot_area_count"] is None
    assert attributes["no_go_area_count"] is None
    assert attributes["last_map_refresh"] is None


def test_map_camera_cache_reuses_fresh_view() -> None:
    """Shared camera cache avoids duplicate app-map refreshes."""
    calls = 0
    cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
    first_now = datetime(2026, 4, 19, 8, 0, tzinfo=UTC)

    async def refresh() -> DreameLawnMowerMapView:
        nonlocal calls
        calls += 1
        return DreameLawnMowerMapView(source="app_action_map")

    async def run() -> None:
        first = await cache.async_get_view(refresh, now=first_now)
        second = await cache.async_get_view(
            refresh,
            now=first_now + timedelta(seconds=30),
        )
        assert first is second

    asyncio.run(run())

    assert calls == 1
    assert cache.last_view is not None
    assert cache.last_refresh_at == first_now


def test_map_camera_cache_refreshes_after_ttl() -> None:
    """Expired cache entries are refreshed on demand."""
    calls = 0
    cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
    first_now = datetime(2026, 4, 19, 8, 0, tzinfo=UTC)

    async def refresh() -> DreameLawnMowerMapView:
        nonlocal calls
        calls += 1
        return DreameLawnMowerMapView(source=f"app_action_map_{calls}")

    async def run() -> None:
        first = await cache.async_get_view(refresh, now=first_now)
        second = await cache.async_get_view(
            refresh,
            now=first_now + timedelta(seconds=61),
        )
        assert first.source == "app_action_map_1"
        assert second.source == "app_action_map_2"

    asyncio.run(run())

    assert calls == 2


def test_map_camera_cache_invalidates_image_when_view_refreshes() -> None:
    """A refreshed map view must not reuse an older rendered JPEG."""
    cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
    first_now = datetime(2026, 4, 19, 8, 0, tzinfo=UTC)

    cache.store_view(
        DreameLawnMowerMapView(source="app_action_map", image_png=b"first"),
        now=first_now,
    )
    cache.store_image(b"jpeg-first")

    cache.store_view(
        DreameLawnMowerMapView(source="app_action_map", image_png=b"second"),
        now=first_now + timedelta(seconds=61),
    )

    assert cache.last_image is None
    assert cache.last_view is not None
    assert cache.last_view.image_png == b"second"


def test_map_camera_cache_coalesces_concurrent_refreshes() -> None:
    """Concurrent map camera refreshes share the same in-flight result."""
    calls = 0
    cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
    now = datetime(2026, 4, 19, 8, 0, tzinfo=UTC)

    async def refresh() -> DreameLawnMowerMapView:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return DreameLawnMowerMapView(source="app_action_map")

    async def run() -> None:
        first, second = await asyncio.gather(
            cache.async_get_view(refresh, now=now),
            cache.async_get_view(refresh, now=now),
        )
        assert first is second

    asyncio.run(run())

    assert calls == 1


def test_map_camera_cache_stores_error_view() -> None:
    """Refresh failures are cached as explicit diagnostic map views."""
    cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
    now = datetime(2026, 4, 19, 8, 0, tzinfo=UTC)

    cache.store_view(
        DreameLawnMowerMapView(source="app_action_map", image_png=b"first"),
        now=now - timedelta(seconds=61),
    )
    cache.store_image(b"jpeg-first")

    view = cache.store_error("offline", source="app_action_map", now=now)

    assert view.source == "app_action_map"
    assert view.error == "offline"
    assert cache.last_view is view
    assert cache.last_image is None
    assert cache.last_error == "offline"
    assert cache.last_refresh_at == now
