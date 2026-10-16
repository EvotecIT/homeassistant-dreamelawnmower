"""Shared photo reads, map generation and lifecycle at the real host boundary."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.dreame_lawn_mower import real_scene_cache as module
from custom_components.dreame_lawn_mower.camera import (
    DreameLawnMowerLivePathMapCamera,
    DreameLawnMowerMapCamera,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.real_scene import (
    real_scene_from_png,
)

from .test_app_action_live_map import native_maps
from .test_real_scene import _png


def _host():
    return SimpleNamespace(
        entry=SimpleNamespace(options={"map_background": "real_scene"}),
        app_maps=native_maps(), client=SimpleNamespace(),
        hass=SimpleNamespace(
            async_create_task=lambda coro, name: asyncio.create_task(coro)
        ),
        async_update_listeners=Mock(), real_scene=None,
    )


def _photo(color=(255, 0, 0, 255)):
    return real_scene_from_png(0, "garden_20_20_3cm.png", _png(8, 8, {(4, 4): color}))


def test_two_camera_consumers_share_one_photo_read(monkeypatch):
    async def run():
        host = _host()
        photo = _photo()
        entered, release = asyncio.Event(), asyncio.Event()

        async def fetch(*args, **kwargs):
            entered.set()
            await release.wait()
            return photo

        read = AsyncMock(side_effect=fetch)
        monkeypatch.setattr(module, "async_fetch_real_scene", read)
        cameras = [object.__new__(kind) for kind in (
            DreameLawnMowerMapCamera, DreameLawnMowerLivePathMapCamera,
        )]
        for camera in cameras:
            camera.coordinator = host
            camera._maybe_refresh_real_scene()
        owner = module.real_scene_owner(host)
        await entered.wait()
        task = owner.request()
        release.set()
        await task
        assert owner.current is photo
        assert all(camera._real_scene is photo for camera in cameras)
        assert read.await_count == 1
        await owner.async_close()

    asyncio.run(run())


def test_same_slot_generation_change_rejects_old_fetch_and_reads_replacement(
    monkeypatch,
):
    async def run():
        host = _host()
        owner = module.real_scene_owner(host)
        old, new = _photo(), _photo((0, 255, 0, 255))
        entered = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        calls = 0

        async def fetch(*args, **kwargs):
            nonlocal calls
            call = calls
            calls += 1
            entered[call].set()
            await release[call].wait()
            return [old, new][call]

        monkeypatch.setattr(module, "async_fetch_real_scene", fetch)
        pending = owner.request()
        await entered[0].wait()
        host.app_maps["maps"][0]["info"]["hash"] = "replacement-map"
        release[0].set()
        await pending
        await entered[1].wait()
        assert owner.current is None and host.real_scene is None
        release[1].set()
        await owner.request()
        assert owner.current is new
        assert calls == 2
        assert owner.request() is None
        assert owner.current is new
        assert calls == 2
        await owner.async_close()

    asyncio.run(run())


@pytest.mark.parametrize("change", ["disable", "unload"])
def test_disabled_or_unloaded_photo_owner_cannot_publish_inflight_fetch(
    monkeypatch, change,
):
    async def run():
        host = _host()
        owner = module.real_scene_owner(host)
        entered, release = asyncio.Event(), asyncio.Event()

        async def fetch(*args, **kwargs):
            entered.set()
            await release.wait()
            return _photo()

        monkeypatch.setattr(module, "async_fetch_real_scene", fetch)
        task = owner.request()
        await entered.wait()
        if change == "disable":
            host.entry.options["map_background"] = "standard"
            release.set()
            await task
        else:
            await owner.async_close()
            assert task.cancelled()
        assert owner.current is None and host.real_scene is None
        assert owner.request() is None
        await owner.async_close()

    asyncio.run(run())


def test_later_photo_timeout_retains_verified_photo_and_backs_off(monkeypatch):
    async def run():
        host = _host()
        owner = module.real_scene_owner(host)
        photo = _photo()
        clock = [100.0]
        monkeypatch.setattr(module, "monotonic", lambda: clock[0])
        fetch = AsyncMock(side_effect=[photo, TimeoutError("cloud timeout")])
        monkeypatch.setattr(module, "async_fetch_real_scene", fetch)
        await owner.request()
        clock[0] = 2000.0
        await owner.request()
        assert owner.current is photo
        assert owner.request() is None
        assert fetch.await_count == 2
        await owner.async_close()

    asyncio.run(run())
