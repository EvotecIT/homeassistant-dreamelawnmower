"""Read-only API authorization, coalescing, lifecycle and image identity contracts."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from aiohttp import web

from custom_components.dreame_lawn_mower import mowing_map_api as module
from custom_components.dreame_lawn_mower.const import DOMAIN
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import map_projection
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.mowing_map import (
    MowingMapScene,
)


def scene():
    return MowingMapScene(
        "a" * 64,
        0,
        1,
        "Garden",
        b"private-png",
        map_projection.vector_map_projection(0, 0, 1000, 500),
        (0, 0, 1000, 500),
    )


def environment(load):
    coordinator = SimpleNamespace(
        client=SimpleNamespace(
            async_get_mowing_map_scene=load,
            descriptor=SimpleNamespace(unique_id="mower"),
        ),
        entry=SimpleNamespace(options={}),
        app_maps={
            "map_list_valid": True,
            "current_map_index": 0,
            "maps": [
                {
                    "idx": 0,
                    "current": True,
                    "created": True,
                    "info": {"hash": "garden-a", "size": 100},
                }
            ],
        },
    )
    hass = SimpleNamespace(
        data={DOMAIN: {"entry": coordinator}},
        async_create_task=lambda coro, name: asyncio.create_task(coro, name=name),
    )
    return hass, coordinator, module.MowingMapAPI(hass)


def test_readers_share_background_and_cancelled_waiter_does_not_cancel_worker():
    async def run():
        started, finish = asyncio.Event(), asyncio.Event()
        calls = 0

        async def load(**kwargs):
            nonlocal calls
            calls += 1
            started.set()
            await finish.wait()
            return scene()

        _, _, api = environment(load)
        first = asyncio.create_task(api.scene("entry"))
        await started.wait()
        second = asyncio.create_task(api.scene("entry"))
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        finish.set()
        assert (await second).scene.revision == "a" * 64
        assert (await api.scene("entry")).scene.image_png == b"private-png"
        assert calls == 1

    asyncio.run(run())


@pytest.mark.parametrize("change", ["generation", "rotation", "replacement", "unload"])
def test_inflight_scene_cannot_publish_after_context_change(change):
    async def run():
        started, finish = asyncio.Event(), asyncio.Event()

        async def load(**kwargs):
            started.set()
            await finish.wait()
            return scene()

        hass, coordinator, api = environment(load)
        pending = asyncio.create_task(api.scene("entry"))
        await started.wait()
        if change == "generation":
            coordinator.app_maps["maps"][0]["info"]["hash"] = "garden-b"
        elif change == "rotation":
            coordinator.entry.options["map_rotation"] = 90
        elif change == "replacement":
            hass.data[DOMAIN]["entry"] = environment(load)[1]
        else:
            hass.data[DOMAIN].pop("entry")
        finish.set()
        with pytest.raises((web.HTTPConflict, web.HTTPNotFound)):
            await pending
        assert not api._cache

    asyncio.run(run())


def test_failed_background_is_private_and_backed_off():
    async def run():
        calls = 0

        async def load(**kwargs):
            nonlocal calls
            calls += 1
            raise ValueError("private-vendor-payload")

        _, _, api = environment(load)
        for _ in range(2):
            with pytest.raises(web.HTTPBadGateway) as failure:
                await api.scene("entry")
            assert "private-vendor" not in failure.value.text
            assert failure.value.headers["Cache-Control"] == "private, no-store"
        assert calls == 1

    asyncio.run(run())


def test_camera_read_permission_is_required(monkeypatch):
    _, _, api = environment(None)
    registry = SimpleNamespace(async_get_entity_id=lambda *args: "camera.garden_map")
    monkeypatch.setattr(module.er, "async_get", lambda hass: registry)
    permitted = Mock(return_value=False)
    request = {
        "hass_user": SimpleNamespace(
            permissions=SimpleNamespace(check_entity=permitted)
        )
    }
    with pytest.raises(web.HTTPForbidden):
        api.authorize(request, "entry")
    permitted.assert_called_once_with("camera.garden_map", module.POLICY_READ)
    permitted.return_value = True
    assert api.authorize(request, "entry") is api.coordinator("entry")


def test_background_requires_exact_revision_and_current_context(monkeypatch):
    async def run():
        async def load(**kwargs):
            return scene()

        _, coordinator, api = environment(load)
        monkeypatch.setattr(api, "authorize", lambda request, entry: coordinator)
        await api.scene("entry")
        view = module.MowingMapBackgroundView(api)
        response = await view.get({}, "entry", "a" * 64)
        assert response.body == b"private-png"
        assert response.headers["Cache-Control"] == "private, no-store"
        with pytest.raises(web.HTTPConflict):
            await view.get({}, "entry", "b" * 64)
        coordinator.app_maps["current_map_index"] = 1
        with pytest.raises(web.HTTPConflict):
            await view.get({}, "entry", "a" * 64)

    asyncio.run(run())
