"""Snapshot HTTP cancellation and explicit camera-lifecycle contracts."""

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.dreame_lawn_mower.video_snapshot import VideoSnapshotRequest


def test_cancelled_http_request_does_not_restart_decoder() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def capture() -> bytes:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return b"real-frame"

        hass = SimpleNamespace(async_create_task=asyncio.create_task)
        owner = VideoSnapshotRequest()
        first = asyncio.create_task(owner.async_get(hass, capture))
        await started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(owner.async_get(hass, capture))
        await asyncio.sleep(0)
        assert calls == 1
        release.set()
        assert await second == b"real-frame"
        await owner.async_cancel()

    asyncio.run(scenario())


def test_camera_shutdown_cancels_and_drains_shared_decoder() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        drained = asyncio.Event()

        async def capture() -> bytes:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                drained.set()

        hass = SimpleNamespace(async_create_task=asyncio.create_task)
        owner = VideoSnapshotRequest()
        waiter = asyncio.create_task(owner.async_get(hass, capture))
        await started.wait()
        await owner.async_cancel()
        assert drained.is_set()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert (
            await owner.async_get(hass, lambda: asyncio.sleep(0, result=b"new"))
            == b"new"
        )
        await owner.async_cancel()

    asyncio.run(scenario())


def test_cached_image_returns_while_one_refresh_continues() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def capture() -> bytes:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return b"fresh"

        hass = SimpleNamespace(async_create_task=asyncio.create_task)
        owner = VideoSnapshotRequest()
        assert await owner.async_get(hass, capture, cached_image=b"old") == b"old"
        await started.wait()
        assert await owner.async_get(hass, capture, cached_image=b"old") == b"old"
        assert calls == 1
        release.set()
        assert await owner.async_get(hass, capture) == b"fresh"
        await owner.async_cancel()

    asyncio.run(scenario())
