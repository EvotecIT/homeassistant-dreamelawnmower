"""Unit tests for Home Assistant map camera helpers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from homeassistant.const import MATCH_ALL

from custom_components.dreame_lawn_mower import camera as camera_module
from custom_components.dreame_lawn_mower.camera import (
    DreameLawnMowerAllMapsCamera,
    DreameLawnMowerLivePathMapCamera,
    DreameLawnMowerMapCamera,
    DreameLawnMowerMapDataCamera,
)
from custom_components.dreame_lawn_mower.map_attributes import map_camera_attributes
from custom_components.dreame_lawn_mower.map_cache import (
    DreameLawnMowerMapCameraCache,
    map_camera_available,
    map_camera_followup_refresh_required,
    map_camera_refresh_demand_active,
    map_camera_should_refresh,
)
from dreame_lawn_mower_client.models import (
    DreameLawnMowerMapSummary,
    DreameLawnMowerMapView,
)


def test_primary_map_camera_is_the_only_map_camera_enabled_by_default() -> None:
    """Users get one useful map entity without enabling diagnostic variants."""
    enabled_default = "__attr_entity_registry_enabled_default"
    assert DreameLawnMowerMapCamera.__dict__[enabled_default] is True
    assert DreameLawnMowerLivePathMapCamera.__dict__[enabled_default] is False
    assert DreameLawnMowerAllMapsCamera.__dict__[enabled_default] is False
    assert DreameLawnMowerMapDataCamera.__dict__[enabled_default] is False


def test_optional_map_cameras_refresh_only_when_requested() -> None:
    """Enabling diagnostic map variants must not create background render loops."""
    refresh_policy = "_refresh_cached_view_on_coordinator_update"
    assert DreameLawnMowerLivePathMapCamera.__dict__[refresh_policy] is False
    assert DreameLawnMowerAllMapsCamera.__dict__[refresh_policy] is False
    assert DreameLawnMowerMapDataCamera.__dict__[refresh_policy] is False


def test_map_camera_attributes_are_excluded_from_recorder() -> None:
    """Large current-map payloads belong in live state, not recorder history."""
    assert DreameLawnMowerMapCamera._unrecorded_attributes == frozenset({MATCH_ALL})
    assert DreameLawnMowerLivePathMapCamera._unrecorded_attributes == frozenset(
        {MATCH_ALL}
    )
    assert DreameLawnMowerAllMapsCamera._unrecorded_attributes == frozenset({MATCH_ALL})
    assert DreameLawnMowerMapDataCamera._unrecorded_attributes == frozenset({MATCH_ALL})


@pytest.mark.parametrize(
    ("model", "observed", "video_expected"),
    [
        ("dreame.mower.p2255", False, False),
        ("dreame.mower.p2255", True, True),
        ("dreame.mower.g2408", False, True),
        ("dreame.mower.future", False, True),
    ],
)
def test_camera_setup_only_omits_definitively_unsupported_video(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    observed: bool,
    video_expected: bool,
) -> None:
    """Unknown models keep detection while the known A1 avoids a dead entity."""
    descriptor = SimpleNamespace(model=model)
    coordinator = SimpleNamespace(
        data=SimpleNamespace(
            descriptor=descriptor,
            capabilities=(),
            raw_info={},
        ),
        client=SimpleNamespace(descriptor=descriptor),
        feature_capability_evidence=lambda: (
            frozenset({"live_video"}) if observed else frozenset(),
            frozenset(),
        ),
    )
    entry = SimpleNamespace(entry_id="entry-1")
    hass = SimpleNamespace(
        data={camera_module.DOMAIN: {entry.entry_id: coordinator}},
    )
    added: list[object] = []

    def _entity(name: str):
        return lambda *_args, **_kwargs: name

    monkeypatch.setattr(camera_module, "DreameLawnMowerMapCamera", _entity("map"))
    monkeypatch.setattr(
        camera_module,
        "DreameLawnMowerLivePathMapCamera",
        _entity("live_path_map"),
    )
    monkeypatch.setattr(
        camera_module,
        "DreameLawnMowerAllMapsCamera",
        _entity("all_maps"),
    )
    monkeypatch.setattr(
        camera_module,
        "DreameLawnMowerMapDataCamera",
        _entity("map_data"),
    )
    monkeypatch.setattr(
        camera_module,
        "DreameLawnMowerVideoCamera",
        _entity("live_video"),
    )

    asyncio.run(
        camera_module.async_setup_entry(
            hass,
            entry,
            lambda entities: added.extend(entities),
        )
    )

    assert ("live_video" in added) is video_expected


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


def test_offline_map_camera_does_not_expose_cached_image() -> None:
    snapshot = SimpleNamespace(
        available=False,
        mapping_available=True,
        capabilities=("map",),
    )

    assert map_camera_available(snapshot, image_cached=True) is False


def test_online_diagnostic_map_camera_does_not_require_map_capability() -> None:
    snapshot = SimpleNamespace(
        available=True,
        mapping_available=False,
        capabilities=(),
    )

    assert (
        map_camera_available(
            snapshot,
            image_cached=False,
            requires_map_capability=False,
        )
        is True
    )


def test_offline_diagnostic_map_camera_remains_unavailable() -> None:
    snapshot = SimpleNamespace(
        available=False,
        mapping_available=False,
        capabilities=(),
    )

    assert (
        map_camera_available(
            snapshot,
            image_cached=False,
            requires_map_capability=False,
        )
        is False
    )


def test_all_maps_camera_skips_cached_view_refresh_on_coordinator_updates() -> None:
    """A direct-fetch diagnostic camera must not start the shared map refresher."""
    assert (
        map_camera_should_refresh(
            context_changed=True,
            runtime_active=True,
            manages_cached_view=False,
        )
        is False
    )


def test_map_camera_background_refresh_requires_recent_viewer_demand() -> None:
    """Mowing updates alone must not keep an unviewed map renderer active."""
    assert (
        map_camera_should_refresh(
            context_changed=True,
            runtime_active=True,
            demand_active=False,
        )
        is False
    )
    assert (
        map_camera_should_refresh(
            context_changed=False,
            runtime_active=True,
            demand_active=True,
        )
        is True
    )


def test_map_camera_refresh_demand_expires_after_window() -> None:
    assert (
        map_camera_refresh_demand_active(
            100.0,
            now=190.0,
            window_seconds=90.0,
        )
        is True
    )
    assert (
        map_camera_refresh_demand_active(
            100.0,
            now=190.001,
            window_seconds=90.0,
        )
        is False
    )
    assert (
        map_camera_refresh_demand_active(
            None,
            now=190.0,
            window_seconds=90.0,
        )
        is False
    )


@pytest.mark.parametrize(
    "camera_type",
    [
        DreameLawnMowerMapCamera,
        DreameLawnMowerLivePathMapCamera,
        DreameLawnMowerAllMapsCamera,
    ],
)
def test_map_cameras_return_loading_image_while_refresh_starts(camera_type) -> None:
    """Cold map caches must not wait for downloads beyond HA's camera timeout."""
    cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
    entity = object.__new__(camera_type)
    entity._map_cache = cache
    entity._restart_preview = None
    refresh_calls = 0

    async def async_add_executor_job(target: object) -> bytes:
        return target()  # type: ignore[operator]

    def start_refresh() -> None:
        nonlocal refresh_calls
        refresh_calls += 1

    entity.hass = SimpleNamespace(async_add_executor_job=async_add_executor_job)
    entity._start_map_refresh = start_refresh  # type: ignore[method-assign]

    image = asyncio.run(entity._async_camera_image_impl())

    assert image is not None
    assert image.startswith(b"\xff\xd8")
    assert refresh_calls == 1
    assert cache.last_image is None


def test_map_diagnostics_serves_stale_shared_view_during_background_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Diagnostics must not wait for a slow provider when shared map data exists."""
    cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
    cache.store_view(
        DreameLawnMowerMapView(source="batch_vector_map"),
        now=datetime(2026, 4, 19, 8, 0, tzinfo=UTC),
    )
    entity = object.__new__(DreameLawnMowerMapDataCamera)
    entity._map_cache = cache
    entity._descriptor = SimpleNamespace(name="Garden", display_model="A2")
    refresh_calls = 0
    rendered_lines: list[str] = []

    async def async_add_executor_job(target: object) -> bytes:
        return target()  # type: ignore[operator]

    def start_refresh() -> None:
        nonlocal refresh_calls
        refresh_calls += 1

    def render(*, lines: list[str]) -> bytes:
        rendered_lines.extend(lines)
        return b"diagnostics-jpeg"

    entity.hass = SimpleNamespace(async_add_executor_job=async_add_executor_job)
    entity._start_map_refresh = start_refresh  # type: ignore[method-assign]
    monkeypatch.setattr(camera_module, "map_diagnostics_jpeg", render)

    image = asyncio.run(entity._async_camera_image_impl())

    assert image == b"diagnostics-jpeg"
    assert refresh_calls == 1
    assert "Source: batch_vector_map" in rendered_lines


def test_map_diagnostics_refreshes_in_foreground_without_shared_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first diagnostics request still hydrates an otherwise empty cache."""
    cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
    entity = object.__new__(DreameLawnMowerMapDataCamera)
    entity._map_cache = cache
    entity._descriptor = SimpleNamespace(name="Garden", display_model="A2")
    refresh_calls = 0
    view = DreameLawnMowerMapView(source="app_action_map")

    async def async_add_executor_job(target: object) -> bytes:
        return target()  # type: ignore[operator]

    async def refresh_view() -> DreameLawnMowerMapView:
        nonlocal refresh_calls
        refresh_calls += 1
        return view

    entity.hass = SimpleNamespace(async_add_executor_job=async_add_executor_job)
    entity._async_refresh_map_view = refresh_view  # type: ignore[method-assign]
    monkeypatch.setattr(
        camera_module,
        "map_diagnostics_jpeg",
        lambda *, lines: b"diagnostics-jpeg",
    )

    image = asyncio.run(entity._async_camera_image_impl())

    assert image == b"diagnostics-jpeg"
    assert refresh_calls == 1


def test_all_maps_camera_serves_stale_image_during_background_refresh() -> None:
    """A slow refresh must not blank or delay the last good contact sheet."""
    cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
    cache.store_view(
        DreameLawnMowerMapView(source="app_maps_contact_sheet"),
        now=datetime(2026, 4, 19, 8, 0, tzinfo=UTC),
    )
    cache.store_image(b"cached-contact-sheet")
    entity = object.__new__(DreameLawnMowerAllMapsCamera)
    entity._map_cache = cache
    refresh_calls = 0

    def start_refresh() -> None:
        nonlocal refresh_calls
        refresh_calls += 1

    entity._start_map_refresh = start_refresh  # type: ignore[method-assign]

    image = asyncio.run(entity._async_camera_image_impl())

    assert image == b"cached-contact-sheet"
    assert refresh_calls == 1


def test_all_maps_camera_caches_failure_placeholder_until_ttl(monkeypatch) -> None:
    """A failed cloud fetch must not be retried on every HA camera poll."""
    cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
    entity = object.__new__(DreameLawnMowerAllMapsCamera)
    entity._map_cache = cache
    cloud_calls = 0
    refresh_calls = 0

    async def async_get_app_maps(**kwargs: object) -> dict[str, object]:
        nonlocal cloud_calls
        del kwargs
        cloud_calls += 1
        raise TimeoutError("cloud timeout")

    async def async_add_executor_job(target: object) -> bytes:
        return target()  # type: ignore[operator]

    def start_refresh() -> None:
        nonlocal refresh_calls
        refresh_calls += 1

    entity.coordinator = SimpleNamespace(
        client=SimpleNamespace(async_get_app_maps=async_get_app_maps),
        runtime_status_blob=None,
        entry=SimpleNamespace(options={}),
        app_maps=None,
        selected_map_index=None,
    )
    entity.hass = SimpleNamespace(async_add_executor_job=async_add_executor_job)
    entity.async_write_ha_state = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr(camera_module, "record_diagnostic_event", lambda *a, **k: None)

    failure_image = asyncio.run(entity._async_refresh_and_render_map_image())
    entity._start_map_refresh = start_refresh  # type: ignore[method-assign]
    repeated_image = asyncio.run(entity._async_camera_image_impl())

    assert failure_image is not None
    assert failure_image.startswith(b"\xff\xd8")
    assert repeated_image == failure_image
    assert cache.is_fresh() is True
    assert cache.last_error == "cloud timeout"
    assert cache.last_image_is_placeholder is True
    assert cloud_calls == 1
    assert refresh_calls == 0

    attributes = map_camera_attributes(
        cache.last_view,
        image_cached=cache.last_image is not None,
        image_placeholder=cache.last_image_is_placeholder,
        refreshed_at=cache.last_refresh_at,
        last_error=cache.last_error,
    )
    assert attributes["map_cached"] is False
    assert attributes["map_placeholder"] is True

    cache.last_refresh_at = datetime.now(UTC) - timedelta(seconds=61)
    expired_image = asyncio.run(entity._async_camera_image_impl())
    assert expired_image == failure_image
    assert refresh_calls == 1

    cache.store_image(b"last-good-contact-sheet")
    preserved_image = asyncio.run(entity._async_refresh_and_render_map_image())
    assert preserved_image == b"last-good-contact-sheet"
    assert cache.last_image_is_placeholder is False
    assert cloud_calls == 2


def test_map_camera_queues_new_context_after_inflight_refresh() -> None:
    assert map_camera_followup_refresh_required(pending=True, available=True) is True
    assert map_camera_followup_refresh_required(pending=False, available=True) is False
    assert map_camera_followup_refresh_required(pending=True, available=False) is False


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
                {"extension": "bin", "url_present": False},
                {"extension": "bin", "url_present": False},
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
        {"extension": "bin", "url_present": False},
        {"extension": "bin", "url_present": False},
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
            "runtime_pose_x": -2240,
            "runtime_pose_y": -140,
            "runtime_heading_deg": 207.53,
            "runtime_region_id": 7,
            "runtime_position_updated_at": "2026-07-16T09:45:12+00:00",
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
    assert attributes["position_x"] == -2240
    assert attributes["position_y"] == -140
    assert attributes["position_heading"] == 207.53
    assert attributes["position_segment"] == 7
    assert attributes["position_updated_at"] == "2026-07-16T09:45:12+00:00"
    assert attributes["map_has_live_path"] is True
    assert attributes["runtime_position_valid"] is None
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


def test_map_camera_attributes_overlay_latest_realtime_pose() -> None:
    """Camera position attributes must not wait for a map-view refresh."""
    view = DreameLawnMowerMapView(
        source="batch_vector_map",
        details={
            "runtime_pose_x": -2240,
            "runtime_pose_y": -140,
            "runtime_heading_deg": 207.53,
            "runtime_region_id": 7,
            "runtime_position_updated_at": "2026-07-16T09:45:12+00:00",
            "runtime_position_valid": True,
        },
    )
    realtime_blob = SimpleNamespace(
        candidate_runtime_pose_x=-1800,
        candidate_runtime_pose_y=250,
        candidate_runtime_heading_deg=91.25,
        candidate_runtime_region_id=8,
        received_at="2026-07-25T11:58:00+00:00",
    )

    attributes = map_camera_attributes(
        view,
        image_cached=True,
        refreshed_at=None,
        last_error=None,
        runtime_status_blob=realtime_blob,
    )

    assert attributes["runtime_pose_x"] == -1800
    assert attributes["runtime_pose_y"] == 250
    assert attributes["runtime_heading_deg"] == 91.25
    assert attributes["runtime_region_id"] == 8
    assert attributes["runtime_position_updated_at"] == ("2026-07-25T11:58:00+00:00")
    assert attributes["position_x"] == -1800
    assert attributes["position_y"] == 250
    assert attributes["position_heading"] == 91.25
    assert attributes["position_segment"] == 8
    assert attributes["position_updated_at"] == "2026-07-25T11:58:00+00:00"
    assert attributes["runtime_position_valid"] is None


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


def test_map_camera_cache_preserves_image_until_refreshed_view_is_rendered() -> None:
    """A slow or failed refresh must not blank an already rendered map."""
    cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
    first_now = datetime(2026, 4, 19, 8, 0, tzinfo=UTC)

    cache.store_view(
        DreameLawnMowerMapView(source="app_action_map", image_png=b"first"),
        now=first_now,
    )
    cache.store_image(b"jpeg-first", source_image=b"first")
    assert cache.view_image_needs_render() is False

    cache.store_view(
        DreameLawnMowerMapView(source="app_action_map", image_png=b"second"),
        now=first_now + timedelta(seconds=61),
    )

    assert cache.last_image == b"jpeg-first"
    assert cache.last_view is not None
    assert cache.last_view.image_png == b"second"
    assert cache.view_image_needs_render() is True


def test_map_camera_cache_recognizes_unchanged_render_source() -> None:
    """Identical PNG bytes avoid unnecessary JPEG conversion work."""
    cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))

    cache.store_image(b"jpeg-first", source_image=b"same-png")

    assert cache.image_matches_source(b"same-png") is True
    assert cache.image_matches_source(b"different-png") is False


def test_map_camera_cache_includes_rotation_in_render_identity() -> None:
    cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
    cache.store_view(
        DreameLawnMowerMapView(
            source="app_action_map",
            image_png=b"same-png",
        )
    )
    cache.store_image(
        b"jpeg-first",
        source_image=b"same-png",
        render_context=0,
    )

    assert cache.image_matches_source(b"same-png", render_context=0) is True
    assert cache.image_matches_source(b"same-png", render_context=90) is False
    assert cache.view_image_needs_render(render_context=90) is True


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
    assert cache.last_image == b"jpeg-first"
    assert cache.last_error == "offline"
    assert cache.last_refresh_at == now
