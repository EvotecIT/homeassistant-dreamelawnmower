"""Read-only API authorization, coalescing, lifecycle and image identity contracts."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web

from custom_components.dreame_lawn_mower import mowing_map_api as module
from custom_components.dreame_lawn_mower import real_scene_cache
from custom_components.dreame_lawn_mower.const import CONF_GEOREFERENCE
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import map_projection
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.mowing_map import (
    MowingMapScene,
)
from custom_components.dreame_lawn_mower.georeference import Georeference
from tests.runtime_fixtures import runtime_hass

from .test_georeference import REFERENCE
from .test_real_scene_cache import _photo


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
    hass = runtime_hass(
        coordinators={"entry": coordinator},
        async_create_task=lambda coro, name: asyncio.create_task(coro, name=name),
    )
    coordinator.hass = hass
    coordinator.async_update_listeners = Mock()
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


@pytest.mark.parametrize(
    "change", ["generation", "rotation", "replacement", "unload", "switch"]
)
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
        elif change == "switch":
            coordinator._last_map_switch_event_at = 100
            coordinator._map_switch_inventory_pending_event = 100
            coordinator.app_maps_refresh_succeeded = False
        elif change == "rotation":
            coordinator.entry.options["map_rotation"] = 90
        elif change == "replacement":
            entry = hass.config_entries.async_get_entry("entry")
            entry.runtime_data = environment(load)[1]
        else:
            del hass.config_entries.async_get_entry("entry").runtime_data
        finish.set()
        with pytest.raises((web.HTTPConflict, web.HTTPNotFound)):
            await pending
        assert not api._cache

    asyncio.run(run())


def test_switch_holds_cached_scene_until_inventory_is_refreshed():
    async def run():
        async def load(**kwargs):
            return scene()

        _, coordinator, api = environment(load)
        first = await api.scene("entry")
        coordinator._last_map_switch_event_at = 100
        coordinator._map_switch_inventory_pending_event = 100
        coordinator.app_maps_refresh_succeeded = False
        with pytest.raises(web.HTTPConflict):
            await api.scene("entry")
        coordinator.app_maps_refresh_succeeded = True
        coordinator._map_switch_inventory_pending_event = None
        assert await api.scene("entry") is not first

    asyncio.run(run())


def test_confirmed_switch_scene_survives_later_inventory_timeout():
    async def run():
        async def load(**kwargs):
            return scene()

        _, coordinator, api = environment(load)
        coordinator._last_map_switch_event_at = 100
        coordinator.app_maps_refresh_succeeded = True
        first = await api.scene("entry")
        coordinator.app_maps_refresh_succeeded = False
        assert await api.scene("entry") is first

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


def test_real_scene_layer_matches_the_background_canvas(monkeypatch):
    from io import BytesIO

    from PIL import Image

    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.real_scene import (  # noqa: E501
        real_scene_from_png,
    )

    async def run():
        async def load(**kwargs):
            return scene()

        hass, coordinator, api = environment(load)

        async def executor(func, *args):
            return func(*args)

        hass.async_add_executor_job = executor
        monkeypatch.setattr(api, "authorize", lambda request, entry: coordinator)
        photo = BytesIO()
        Image.new("RGBA", (40, 20), (0, 128, 0, 255)).save(photo, format="PNG")
        photo_scene = real_scene_from_png(
            0, "x/IQ1/a_20_10_3cm.png", photo.getvalue()
        )
        entry = await api.scene("entry")
        view = module.MowingMapRealSceneView(api)

        assert module._real_scene_path("entry", entry) is None
        coordinator.entry.options["map_background"] = "real_scene"
        monkeypatch.setattr(
            real_scene_cache, "async_fetch_real_scene",
            AsyncMock(return_value=photo_scene)
        )
        await real_scene_cache.real_scene_owner(coordinator).request()
        path = module._real_scene_path("entry", entry)
        digest = coordinator.real_scene.digest
        assert path.endswith(f"/real-scene/{'a' * 64}/{digest}")

        response = await view.get({}, "entry", "a" * 64, digest)
        assert response.headers["Cache-Control"] == "private, no-store"
        with Image.open(BytesIO(response.body)) as layer:
            projection = entry.scene.projection
            assert layer.size == (projection.width, projection.height)
            assert layer.getpixel(projection.point(0, 0))[1] == 128
        with pytest.raises(web.HTTPNotFound):
            await view.get({}, "entry", "a" * 64, "0" * 64)
        coordinator.app_maps["maps"][0]["info"]["hash"] = "replacement-garden"
        assert module._real_scene_path("entry", entry) is None

    asyncio.run(run())


@pytest.mark.parametrize("change", ["none", "map", "rotation", "disable", "unload"])
def test_photo_layer_coalesces_requests_and_rejects_changed_context(
    monkeypatch, change,
):
    async def run():
        async def load(**kwargs):
            return scene()

        hass, coordinator, api = environment(load)
        coordinator.entry.options["map_background"] = "real_scene"
        photo = _photo()
        monkeypatch.setattr(real_scene_cache, "async_fetch_real_scene",
                            AsyncMock(return_value=photo))
        monkeypatch.setattr(api, "authorize", lambda request, entry: coordinator)
        await api.scene("entry")
        pending_photo = real_scene_cache.real_scene_owner(coordinator).request()
        if pending_photo is not None:
            await pending_photo
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def executor(*args):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return b"layer-png"

        hass.async_add_executor_job = executor
        view = module.MowingMapRealSceneView(api)
        first = asyncio.create_task(view.get({}, "entry", "a" * 64, photo.digest))
        await entered.wait()
        second = asyncio.create_task(view.get({}, "entry", "a" * 64, photo.digest))
        await asyncio.sleep(0)
        assert len(api._photo_inflight) == 1
        if change == "map":
            coordinator.app_maps["maps"][0]["info"]["hash"] = "replacement-map"
        elif change == "rotation":
            coordinator.entry.options["map_rotation"] = 90
        elif change == "disable":
            coordinator.entry.options["map_background"] = "standard"
        elif change == "unload":
            del hass.config_entries.async_get_entry("entry").runtime_data
        else:
            first.cancel()
        release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        assert calls == 1
        if change == "none":
            assert isinstance(results[0], asyncio.CancelledError)
            assert results[1].body == b"layer-png"
        else:
            assert all(isinstance(result, web.HTTPException) for result in results)
            assert not api._photo_layers
        await api.purge_entry("entry")
        assert not api._photo_layers
        assert not api._photo_inflight
        await real_scene_cache.real_scene_owner(coordinator).async_close()

    asyncio.run(run())


def test_scene_only_consumer_starts_photo_read_without_delaying_vector_scene(
    monkeypatch,
):
    async def run():
        async def load(**kwargs):
            return scene()

        _, coordinator, api = environment(load)
        coordinator.entry.options["map_background"] = "real_scene"
        entered, release = asyncio.Event(), asyncio.Event()
        photo = _photo()

        async def fetch(*args, **kwargs):
            entered.set()
            await release.wait()
            return photo

        monkeypatch.setattr(real_scene_cache, "async_fetch_real_scene", fetch)
        entry = await asyncio.wait_for(api.scene("entry"), 1)
        await entered.wait()
        assert entry.scene.image_png == b"private-png"
        assert module._real_scene_path("entry", entry) is None
        release.set()
        owner = real_scene_cache.real_scene_owner(coordinator)
        await owner.request()
        assert module._real_scene_path("entry", await api.scene("entry")) is not None
        await owner.async_close()

    asyncio.run(run())


def test_scene_json_publishes_projection_and_calibration(monkeypatch) -> None:
    async def run(options):
        async def load(**kwargs):
            return scene()

        _, coordinator, api = environment(load)
        coordinator.entry.options = options
        coordinator.client.mowing_map_runtime_overlay = Mock(return_value={})
        monkeypatch.setattr(api, "authorize", lambda request, entry: coordinator)
        response = await module.MowingMapView(api).get({}, "entry")
        return json.loads(response.body)

    payload = asyncio.run(run({CONF_GEOREFERENCE: REFERENCE}))
    projection = scene().projection
    assert payload["schema_version"] == 1
    assert payload["projection"] == {
        "max_x": projection.max_x,
        "min_y": projection.min_y,
        "scale": projection.scale,
        "padding": projection.padding,
        "native_width": projection.native_width,
        "native_height": projection.native_height,
        "rotation": projection.rotation,
    }
    assert payload["georeference"] == Georeference.parse(REFERENCE).as_dict()
    # Existing fields are kept.
    assert {"revision", "width", "height", "overlay", "background_path"} <= set(
        payload
    )
    assert asyncio.run(run({}))["georeference"] is None


def test_scene_json_lists_zone_labels_and_background_drops_names(monkeypatch):
    from dataclasses import replace

    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.mowing_map import (  # noqa: E501
        ZoneLabel,
    )

    labelled = replace(
        scene(), image_png_unlabeled=b"no-names",
        zone_labels=(ZoneLabel(1, "Front", 300, 250), ZoneLabel(2, "Back", 800, 200)),
    )

    async def run():
        async def load(**kwargs):
            return labelled

        _, coordinator, api = environment(load)
        coordinator.client.mowing_map_runtime_overlay = Mock(return_value={})
        monkeypatch.setattr(api, "authorize", lambda request, entry: coordinator)
        payload = json.loads(
            (await module.MowingMapView(api).get({}, "entry")).body
        )
        background = module.MowingMapBackgroundView(api)
        default = await background.get(SimpleNamespace(query={}), "entry", "a" * 64)
        stripped = await background.get(
            SimpleNamespace(query={"labels": "0"}), "entry", "a" * 64
        )
        other = await background.get(
            SimpleNamespace(query={"labels": "1"}), "entry", "a" * 64
        )
        return payload, default.body, stripped.body, other.body

    payload, default, stripped, other = asyncio.run(run())
    assert payload["zones"] == [
        {"zone_id": 1, "name": "Front", "label_x": 300, "label_y": 250},
        {"zone_id": 2, "name": "Back", "label_x": 800, "label_y": 200},
    ]
    assert payload["revision"] == labelled.revision
    assert (default, stripped, other) == (b"private-png", b"no-names", b"private-png")


def test_scene_json_without_zones_publishes_an_empty_list(monkeypatch):
    async def run():
        async def load(**kwargs):
            return scene()

        _, coordinator, api = environment(load)
        coordinator.client.mowing_map_runtime_overlay = Mock(return_value={})
        monkeypatch.setattr(api, "authorize", lambda request, entry: coordinator)
        return json.loads((await module.MowingMapView(api).get({}, "entry")).body)

    assert asyncio.run(run())["zones"] == []
