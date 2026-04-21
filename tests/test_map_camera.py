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
    assert attributes["app_map_count"] is None
    assert attributes["app_maps"] is None
    assert attributes["app_map_object_count"] is None
    assert attributes["app_map_objects"] is None


def test_map_camera_attributes_include_all_app_map_metadata() -> None:
    """Camera attributes expose all app maps, not only the rendered map."""
    view = DreameLawnMowerMapView(
        source="app_action_map",
        app_maps={
            "map_count": 2,
            "current_map_index": 0,
            "available_map_count": 2,
            "created_map_count": 2,
            "error_count": 0,
            "object_count": 2,
            "object_error": None,
            "objects": [
                {"name": "map-a.bin", "extension": "bin", "url_present": False},
                {"name": "map-b.bin", "extension": "bin", "url_present": False},
            ],
            "maps": [
                {"idx": 0, "current": True, "available": True},
                {"idx": 1, "current": False, "available": True},
            ],
        },
    )

    attributes = map_camera_attributes(
        view,
        image_cached=False,
        refreshed_at=None,
        last_error=None,
    )

    assert attributes["app_map_count"] == 2
    assert attributes["app_current_map_index"] == 0
    assert attributes["app_available_map_count"] == 2
    assert attributes["app_created_map_count"] == 2
    assert attributes["app_map_error_count"] == 0
    assert attributes["app_map_object_count"] == 2
    assert attributes["app_map_object_error"] is None
    assert attributes["app_map_objects"] == [
        {"name": "map-a.bin", "extension": "bin", "url_present": False},
        {"name": "map-b.bin", "extension": "bin", "url_present": False},
    ]
    assert attributes["app_maps"] == [
        {"idx": 0, "current": True, "available": True},
        {"idx": 1, "current": False, "available": True},
    ]
    assert attributes["map_has_live_path"] is None
    assert attributes["map_details"] is None


def test_map_camera_attributes_include_live_path_metadata() -> None:
    """Camera attributes expose vector live-path details when present."""
    view = DreameLawnMowerMapView(
        source="batch_vector_map",
        details={
            "map_name": "Primary",
            "map_id": 1,
            "map_index": 0,
            "current_map_id": 2,
            "total_area": 10.5,
            "zone_count": 2,
            "zone_names": ["Front Yard", "Back Yard"],
            "contour_count": 1,
            "contour_ids": [[1, 0]],
            "clean_point_count": 1,
            "cruise_point_count": 0,
            "mow_path_count": 1,
            "mow_path_segment_count": 3,
            "mow_path_point_count": 18,
            "mow_path_length_m": 9.87,
            "has_live_path": True,
            "available_map_count": 2,
            "available_maps": [
                {"map_id": 1, "map_index": 0, "name": "Primary", "total_area": 10.5},
                {"map_id": 2, "map_index": 1, "name": "Back", "total_area": 8.0},
            ],
        },
    )

    attributes = map_camera_attributes(
        view,
        image_cached=False,
        refreshed_at=None,
        last_error=None,
    )

    assert attributes["map_name"] == "Primary"
    assert attributes["map_index"] == 0
    assert attributes["map_current_map_id"] == 2
    assert attributes["map_total_area"] == 10.5
    assert attributes["map_zone_count"] == 2
    assert attributes["map_zone_names"] == ["Front Yard", "Back Yard"]
    assert attributes["map_contour_count"] == 1
    assert attributes["map_contour_ids"] == [[1, 0]]
    assert attributes["map_clean_point_count"] == 1
    assert attributes["map_cruise_point_count"] == 0
    assert attributes["map_trajectory_count"] is None
    assert attributes["map_trajectory_point_count"] is None
    assert attributes["map_cut_relation_count"] is None
    assert attributes["mow_path_count"] == 1
    assert attributes["mow_path_segment_count"] == 3
    assert attributes["mow_path_point_count"] == 18
    assert attributes["mow_path_length_m"] == 9.87
    assert attributes["map_has_live_path"] is True
    assert attributes["map_available_vector_map_count"] == 2
    assert attributes["map_available_vector_maps"] == [
        {"map_id": 1, "map_index": 0, "name": "Primary", "total_area": 10.5},
        {"map_id": 2, "map_index": 1, "name": "Back", "total_area": 8.0},
    ]
    assert attributes["map_details"] == {
        "map_name": "Primary",
        "map_id": 1,
        "map_index": 0,
        "current_map_id": 2,
        "total_area": 10.5,
        "zone_count": 2,
        "zone_names": ["Front Yard", "Back Yard"],
        "contour_count": 1,
        "contour_ids": [[1, 0]],
        "clean_point_count": 1,
        "cruise_point_count": 0,
        "mow_path_count": 1,
        "mow_path_segment_count": 3,
        "mow_path_point_count": 18,
        "mow_path_length_m": 9.87,
        "has_live_path": True,
        "available_map_count": 2,
        "available_maps": [
            {"map_id": 1, "map_index": 0, "name": "Primary", "total_area": 10.5},
            {"map_id": 2, "map_index": 1, "name": "Back", "total_area": 8.0},
        ],
    }


def test_map_camera_attributes_include_app_trajectory_details() -> None:
    """App-map trajectory metadata is promoted into camera attributes."""
    view = DreameLawnMowerMapView(
        source="app_action_map",
        details={
            "map_name": "Garden",
            "map_index": 1,
            "total_area": 550,
            "map_area_total": 550.0,
            "zone_count": 2,
            "spot_area_count": 0,
            "clean_point_count": 0,
            "trajectory_count": 1,
            "trajectory_point_count": 64,
            "trajectory_length_m": 12.34,
            "cut_relation_count": 0,
            "has_live_path": True,
            "current": True,
            "created": True,
        },
    )

    attributes = map_camera_attributes(
        view,
        image_cached=False,
        refreshed_at=None,
        last_error=None,
    )

    assert attributes["map_name"] == "Garden"
    assert attributes["map_total_area"] == 550
    assert attributes["map_zone_count"] == 2
    assert attributes["map_clean_point_count"] == 0
    assert attributes["map_trajectory_count"] == 1
    assert attributes["map_trajectory_point_count"] == 64
    assert attributes["map_trajectory_length_m"] == 12.34
    assert attributes["map_cut_relation_count"] == 0
    assert attributes["map_has_live_path"] is True


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
