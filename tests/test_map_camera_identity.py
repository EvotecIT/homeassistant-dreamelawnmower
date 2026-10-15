"""Camera publication must agree with the selected map and its orientation."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.dreame_lawn_mower import camera as camera_module
from custom_components.dreame_lawn_mower import map_preview as preview_module
from custom_components.dreame_lawn_mower.camera import (
    DreameLawnMowerAllMapsCamera,
    DreameLawnMowerLivePathMapCamera,
    DreameLawnMowerMapCamera,
)
from custom_components.dreame_lawn_mower.map_cache import DreameLawnMowerMapCameraCache
from dreame_lawn_mower_client.models import (
    DreameLawnMowerMapSummary,
    DreameLawnMowerMapView,
)


class _Camera(DreameLawnMowerMapCamera):
    _selected_map_index = 0
    _map_rotation = 270
    _map_label_scale = 1
    _map_style = None
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


@pytest.mark.parametrize("during_conversion", [False, True])
def test_same_index_switch_during_render_rejects_obsolete_pixels(during_conversion):
    entity = _camera()
    entity.coordinator = SimpleNamespace(_last_map_switch_event_at=None)

    async def refresh():
        if not during_conversion:
            entity.coordinator._last_map_switch_event_at = 100
        return DreameLawnMowerMapView(
            source="app_action_map", image_png=b"png",
            summary=DreameLawnMowerMapSummary(available=True, map_id=0),
        )

    async def convert(_):
        entity.coordinator._last_map_switch_event_at = 100
        return b"obsolete-jpeg"

    entity._async_refresh_map_view = refresh
    entity.hass.async_add_executor_job = convert
    assert asyncio.run(entity._async_refresh_and_render_map_image()) is None
    assert entity._map_cache.last_image is None
    assert entity._map_refresh_pending is True


def test_same_index_switch_discards_idle_cached_camera_image(monkeypatch):
    entity = _camera()
    entity.coordinator = SimpleNamespace(data=None, _last_map_switch_event_at=100)
    entity._last_refresh_context = entity._map_refresh_context
    entity._last_image_request_at = 0
    entity._map_cache.store_image(b"old", source_image=b"old-png")
    entity._start_map_refresh = Mock()
    monkeypatch.setattr(
        camera_module.CoordinatorEntity, "_handle_coordinator_update", Mock()
    )
    entity._handle_coordinator_update()
    assert entity._map_cache.last_image is None


def test_same_index_switch_rejects_inflight_contact_sheet():
    entity = _camera()
    entity.coordinator = SimpleNamespace(
        client=SimpleNamespace(), _last_map_switch_event_at=None,
    )

    async def convert(_):
        entity.coordinator._last_map_switch_event_at = 100
        return b"obsolete-contact-sheet"

    entity.coordinator.client.async_get_app_maps = AsyncMock(return_value={"maps": []})
    entity.hass.async_add_executor_job = convert
    # The real contact-sheet owner uses this entity's map identity/style
    # properties; conversion is the only external worker boundary here.
    assert asyncio.run(
        DreameLawnMowerAllMapsCamera._async_refresh_and_render_map_image(entity)
    ) is None
    assert entity._map_cache.last_image is None
    assert entity._map_refresh_pending is True


@pytest.mark.parametrize("during_placeholder", [False, True])
def test_same_index_switch_rejects_failed_contact_sheet(during_placeholder):
    entity = _camera()
    entity.coordinator = SimpleNamespace(
        client=SimpleNamespace(), _last_map_switch_event_at=None,
    )

    async def download(**kwargs):
        if not during_placeholder:
            entity.coordinator._last_map_switch_event_at = 100
        raise TimeoutError("Old contact sheet failed")

    async def placeholder(_):
        entity.coordinator._last_map_switch_event_at = 100
        return b"obsolete-placeholder"

    entity.coordinator.client.async_get_app_maps = download
    entity.hass.async_add_executor_job = placeholder
    assert asyncio.run(
        DreameLawnMowerAllMapsCamera._async_refresh_and_render_map_image(entity)
    ) is None
    assert entity._map_cache.last_view is None
    assert entity._map_cache.last_image is None
    assert entity._map_refresh_pending is True


@pytest.mark.parametrize("camera_type", [
    DreameLawnMowerMapCamera, DreameLawnMowerLivePathMapCamera,
])
def test_pre_switch_camera_failure_cannot_publish_error_attributes(camera_type):
    entity = _camera()
    entity.coordinator = SimpleNamespace(
        client=SimpleNamespace(), _last_map_switch_event_at=None,
    )

    async def download(**kwargs):
        entity.coordinator._last_map_switch_event_at = 100
        raise TimeoutError("Old map failed")

    entity.coordinator.client.async_refresh_map_view = download
    entity.coordinator.client.async_refresh_vector_map_view = download
    asyncio.run(camera_type._async_refresh_map_view(entity))
    assert entity._map_cache.last_view is None
    assert entity._map_cache.last_refresh_at is None
    entity.async_write_ha_state.assert_not_called()


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


def test_observed_switch_cannot_restore_pre_switch_disk_preview():
    entity = _camera()
    entity.coordinator = SimpleNamespace(_last_map_switch_event_at=100)
    entity._descriptor = SimpleNamespace(unique_id="one-mower")
    entity._preview_attempted_scope = None
    entity._restart_preview = SimpleNamespace(async_load=AsyncMock(
        return_value=(b"old-garden", 123.0),
    ))
    entity._start_map_refresh = Mock()
    asyncio.run(entity._async_get_map_image())
    entity._restart_preview.async_load.assert_not_called()
    assert entity._map_cache.last_image is None
    entity._start_map_refresh.assert_called_once()


def test_switch_during_camera_resize_cannot_return_old_image():
    entity = _camera()
    entity.coordinator = SimpleNamespace(_last_map_switch_event_at=None)
    entity._last_refresh_context = entity._map_refresh_context
    entity._async_camera_image_impl = AsyncMock(return_value=b"old-garden")

    async def resize(*args):
        entity.coordinator._last_map_switch_event_at = 100
        return b"old-garden-resized"

    entity._image_variants = SimpleNamespace(async_get=resize)
    assert asyncio.run(entity.async_camera_image(width=400)) is None


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


def test_restart_preview_scope_excludes_process_local_switch_receipt():
    entity = _camera()
    entity.coordinator = SimpleNamespace(
        entry=SimpleNamespace(options={"map_restart_preview": True}),
        _last_map_switch_event_at=None,
    )
    entity._descriptor = SimpleNamespace(unique_id="one-mower")
    entity._restart_preview = SimpleNamespace(async_save=AsyncMock())

    async def run():
        await entity._async_save_restart_preview(b"jpeg", entity._map_render_context)
        entity.coordinator._last_map_switch_event_at = 100
        await entity._async_save_restart_preview(b"jpeg", entity._map_render_context)

    asyncio.run(run())
    first, second = entity._restart_preview.async_save.call_args_list
    assert first.args[1] == second.args[1]
    assert [first.kwargs["generation"], second.kwargs["generation"]] == [None, 100]


def test_same_slot_camera_switch_updates_durable_preview_inside_cooldown(monkeypatch):
    async def scenario():
        records = []

        async def save(record):
            records.append(record)

        monkeypatch.setattr(preview_module, "preview_store", lambda *_:
            SimpleNamespace(async_save=save, async_remove=AsyncMock()))
        monkeypatch.setattr(preview_module.time, "monotonic", lambda: 100.0)
        entity = _camera()
        entity.coordinator = SimpleNamespace(
            entry=SimpleNamespace(options={"map_restart_preview": True}),
            _last_map_switch_event_at=None,
        )
        entity._descriptor = SimpleNamespace(unique_id="one-mower")
        entity._restart_preview = preview_module.RestartMapPreview(entity.hass, "entry")
        old, new = b"\xff\xd8\xffold", b"\xff\xd8\xffnew"
        await entity._async_save_restart_preview(old, entity._map_render_context)
        entity.coordinator._last_map_switch_event_at = 100
        entity._restart_preview.invalidate(100)
        await entity._async_save_restart_preview(new, entity._map_render_context)
        assert len(records) == 2
        assert records[0]["scope"] == records[1]["scope"]
        assert preview_module.decode_preview(records[1], records[1]["scope"])[0] == new

    asyncio.run(scenario())
