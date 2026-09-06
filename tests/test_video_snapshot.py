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


def test_successive_snapshots_wait_for_fresh_frames() -> None:
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
        first = asyncio.create_task(owner.async_get(hass, capture))
        await started.wait()
        second = asyncio.create_task(owner.async_get(hass, capture))
        await asyncio.sleep(0)
        assert not first.done() and not second.done()
        assert calls == 1
        release.set()
        assert await first == await second == b"fresh"
        assert await owner.async_get(
            hass, lambda: asyncio.sleep(0, result=b"newer")
        ) == b"newer"
        await owner.async_cancel()

    asyncio.run(scenario())


@pytest.mark.parametrize("age,expected", [(1, b"completed"), (6, b"newer")])
def test_abandoned_completed_frame_has_bounded_retry_age(monkeypatch, age, expected):
    from custom_components.dreame_lawn_mower import video_snapshot

    async def scenario():
        clock = [100.0]
        monkeypatch.setattr(video_snapshot, "monotonic", lambda: clock[0])
        release = asyncio.Event()

        async def capture():
            await release.wait()
            return b"completed"

        hass = SimpleNamespace(async_create_task=asyncio.create_task)
        owner = VideoSnapshotRequest()
        waiter = asyncio.create_task(owner.async_get(hass, capture))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        await owner._task
        await asyncio.sleep(0)
        clock[0] += age
        assert await owner.async_get(
            hass, lambda: asyncio.sleep(0, result=b"newer")
        ) == expected
        await owner.async_cancel()

    asyncio.run(scenario())
