"""Camera publication must agree with the selected map and its orientation."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.dreame_lawn_mower import camera as camera_module
from custom_components.dreame_lawn_mower.camera import DreameLawnMowerMapCamera
from custom_components.dreame_lawn_mower.map_cache import DreameLawnMowerMapCameraCache
from dreame_lawn_mower_client.models import (
    DreameLawnMowerMapSummary,
    DreameLawnMowerMapView,
)


class _Camera(DreameLawnMowerMapCamera):
    _selected_map_index = 0
    _map_rotation = 270
    _map_refresh_context = (0,)
    available = True


def _camera():
    entity = object.__new__(_Camera)
    entity._map_cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
    entity._map_refresh_pending = False
    entity._map_refresh_task = None
    entity._restart_preview = None
    entity._preview_saved_at = None
    entity.async_write_ha_state = Mock()
    entity.hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=b"jpeg")
    )
    return entity


@pytest.mark.parametrize(
    "source,returned_index",
    [("app_action_map", None), ("app_action_map", 1), ("legacy_current_map", 0)],
)
def test_camera_rejects_unverified_or_different_map_before_conversion(
    source,
    returned_index,
):
    entity = _camera()
    view = DreameLawnMowerMapView(
        source=source,
        image_png=b"png",
        summary=DreameLawnMowerMapSummary(available=True, map_id=returned_index),
    )
    entity._map_cache.store_image(b"old", source_image=b"old-png")
    entity._async_refresh_map_view = AsyncMock(return_value=view)
    assert asyncio.run(entity._async_refresh_and_render_map_image()) is None
    entity.hass.async_add_executor_job.assert_not_called()
    assert entity._map_cache.last_image is None
    assert entity._map_cache.last_view is None
    assert entity._map_refresh_pending is False


@pytest.mark.parametrize("rendered_rotation,expected", [(0, 270), (270, 0)])
def test_camera_applies_only_remaining_jpeg_rotation(rendered_rotation, expected):
    entity = _camera()
    entity._async_refresh_map_view = AsyncMock(
        return_value=DreameLawnMowerMapView(
            source="app_action_map",
            image_png=b"png",
            summary=DreameLawnMowerMapSummary(available=True, map_id=0),
            details={"render_rotation": rendered_rotation},
        )
    )
    assert asyncio.run(entity._async_refresh_and_render_map_image()) == b"jpeg"
    conversion = entity.hass.async_add_executor_job.call_args.args[0]
    assert conversion.keywords["rotation"] == expected
    assert entity._map_cache.last_image == b"jpeg"


def test_idle_map_change_with_camera_demand_starts_background_refresh(monkeypatch):
    entity = _camera()
    entity.coordinator = SimpleNamespace(data=None)
    entity._last_refresh_context = (1,)
    entity._last_image_request_at = camera_module.monotonic()
    entity._map_cache.store_image(b"old", source_image=b"old-png")
    entity._start_map_refresh = Mock()
    monkeypatch.setattr(
        camera_module.CoordinatorEntity, "_handle_coordinator_update", Mock()
    )
    entity._handle_coordinator_update()
    entity._start_map_refresh.assert_called_once()
    assert entity._map_cache.last_image is None


@pytest.mark.parametrize("during_conversion", [False, True])
def test_presentation_change_during_render_rejects_obsolete_pixels(during_conversion):
    entity = _camera()
    entity._map_refresh_context = (0, None, "light")

    async def refresh():
        if not during_conversion:
            entity._map_refresh_context = (0, None, "dark")
        return DreameLawnMowerMapView(
            source="app_action_map", image_png=b"png",
            summary=DreameLawnMowerMapSummary(available=True, map_id=0),
        )

    async def convert(_):
        entity._map_refresh_context = (0, None, "dark")
        return b"obsolete-jpeg"

    entity._async_refresh_map_view = refresh
    entity.hass.async_add_executor_job = convert
    assert asyncio.run(entity._async_refresh_and_render_map_image()) is None
    assert entity._map_cache.last_image is None
    assert entity._map_refresh_pending is True


@pytest.mark.parametrize("fresh_wins", [False, True])
def test_restart_preview_restores_only_before_current_image(fresh_wins):
    entity = _camera()
    entity._descriptor = SimpleNamespace(unique_id="one-mower")
    entity._preview_attempted_scope = None
    entity._start_map_refresh = Mock()

    async def restore(_):
        if fresh_wins:
            entity._map_cache.store_image(b"fresh")
        return b"saved", 123.0

    entity._restart_preview = SimpleNamespace(async_load=restore)
    image = asyncio.run(entity._async_get_map_image())
    assert image == (b"fresh" if fresh_wins else b"saved")
    assert entity._preview_saved_at == (None if fresh_wins else 123.0)
    assert entity._map_cache.last_refresh_at is None
    entity._start_map_refresh.assert_called_once()


def test_restart_preview_cannot_override_mower_unavailability():
    entity = _camera()
    entity.available = False
    entity._restart_preview = SimpleNamespace(async_load=AsyncMock())
    assert asyncio.run(entity.async_camera_image()) is None
    entity._restart_preview.async_load.assert_not_called()


def test_identical_map_refresh_renews_preview_without_jpeg_conversion():
    entity = _camera()
    entity.coordinator = SimpleNamespace(entry=SimpleNamespace(
        options={"map_restart_preview": True}
    ))
    entity._descriptor = SimpleNamespace(unique_id="one-mower")
    entity._restart_preview = SimpleNamespace(async_save=AsyncMock())
    entity._map_cache.store_image(
        b"jpeg", source_image=b"png", render_context=entity._map_rotation
    )
    entity._async_refresh_map_view = AsyncMock(return_value=DreameLawnMowerMapView(
        source="app_action_map", image_png=b"png",
        summary=DreameLawnMowerMapSummary(available=True, map_id=0),
    ))
    assert asyncio.run(entity._async_refresh_and_render_map_image()) == b"jpeg"
    entity.hass.async_add_executor_job.assert_not_called()
    entity._restart_preview.async_save.assert_awaited_once()
    assert entity._restart_preview.async_save.call_args.args[0] == b"jpeg"
