"""Contact-sheet publication and retention through the map camera boundary."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from custom_components.dreame_lawn_mower.camera import DreameLawnMowerAllMapsCamera
from custom_components.dreame_lawn_mower.map_attributes import map_camera_attributes
from custom_components.dreame_lawn_mower.map_cache import DreameLawnMowerMapCameraCache


def test_contact_sheet_retains_image_and_inventory_after_refresh_failure(
    tmp_path,
) -> None:
    """A later cloud failure keeps the last JPEG's inventory and exposes its error."""
    app_maps = {
        "map_count": 2,
        "current_map_index": 0,
        "available_map_count": 2,
        "created_map_count": 2,
        "maps": [
            {
                "idx": index,
                "current": index == 0,
                "created": True,
                "available": True,
                "payload": {
                    "map": [{"id": 1, "data": [[0, 0], [100, 0], [0, 100]]}]
                },
            }
            for index in range(2)
        ],
    }

    async def get_app_maps(**_kwargs):
        return app_maps

    async def execute(target):
        return target()

    cache = DreameLawnMowerMapCameraCache(ttl=timedelta(seconds=60))
    camera = object.__new__(DreameLawnMowerAllMapsCamera)
    camera._map_cache = cache
    camera.coordinator = SimpleNamespace(
        client=SimpleNamespace(async_get_app_maps=get_app_maps),
        runtime_status_blob=None,
        entry=SimpleNamespace(options={}),
        app_maps=app_maps,
        selected_map_index=0,
    )
    camera.hass = SimpleNamespace(
        config=SimpleNamespace(path=lambda _name: str(tmp_path)),
        async_add_executor_job=execute,
    )
    camera.async_write_ha_state = lambda: None

    rendered = asyncio.run(camera._async_refresh_and_render_map_image())
    assert rendered is not None
    with Image.open(BytesIO(rendered)) as image:
        assert image.format == "JPEG"
        assert image.width > image.height > 0
    assert cache.last_image == rendered
    assert cache.is_fresh()
    attributes = map_camera_attributes(
        cache.last_view,
        image_cached=cache.last_image is not None,
        image_placeholder=cache.last_image_is_placeholder,
        refreshed_at=cache.last_refresh_at,
        last_error=cache.last_error,
    )
    assert attributes["map_cached"] is True
    assert attributes["map_placeholder"] is False
    assert attributes["map_has_image"] is True
    assert attributes["app_map_count"] == 2
    assert attributes["app_current_map_index"] == 0
    assert attributes["app_available_map_count"] == 2
    assert [item["idx"] for item in attributes["app_maps"]] == [0, 1]
    assert all("payload" not in item for item in attributes["app_maps"])

    async def fail_get_app_maps(**_kwargs):
        raise TimeoutError("cloud timeout")

    camera.coordinator.client.async_get_app_maps = fail_get_app_maps
    fallback = asyncio.run(camera._async_refresh_and_render_map_image())
    assert fallback == rendered
    assert cache.last_image == rendered
    failed_attributes = map_camera_attributes(
        cache.last_view,
        image_cached=cache.last_image is not None,
        image_placeholder=cache.last_image_is_placeholder,
        refreshed_at=cache.last_refresh_at,
        last_error=cache.last_error,
    )
    assert failed_attributes["map_error"] == "cloud timeout"
    for key in (
        "map_cached",
        "map_placeholder",
        "map_has_image",
        "app_map_count",
        "app_current_map_index",
        "app_available_map_count",
        "app_created_map_count",
        "app_maps",
    ):
        assert failed_attributes[key] == attributes[key], key
