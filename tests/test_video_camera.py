"""Contract tests for the Home Assistant Dreame live-video entity."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from homeassistant.components.camera import CameraEntityFeature

from custom_components.dreame_lawn_mower.const import CONF_XP2P_RUNNER_COMMAND
from custom_components.dreame_lawn_mower.video_camera import (
    DreameLawnMowerVideoCamera,
)

from .fixture_data import load_json_fixture


def _uninitialized_entity(*, snapshot: object | None = None):
    entity = object.__new__(DreameLawnMowerVideoCamera)
    entity._entry = SimpleNamespace(
        options={CONF_XP2P_RUNNER_COMMAND: "xp2p-runner"}
    )
    entity.coordinator = SimpleNamespace(data=snapshot)
    return entity


def test_video_camera_advertises_stop_control() -> None:
    features = _uninitialized_entity().supported_features

    assert features & CameraEntityFeature.STREAM
    assert features & CameraEntityFeature.ON_OFF
    assert "async_turn_on" in DreameLawnMowerVideoCamera.__dict__
    assert "async_turn_off" in DreameLawnMowerVideoCamera.__dict__


def test_video_camera_available_from_a2_video_metadata() -> None:
    payload = load_json_fixture("a2_paused_diagnostics.json")
    snapshot = SimpleNamespace(**payload["data"]["snapshot"])
    entity = _uninitialized_entity(snapshot=snapshot)

    assert "video" not in snapshot.capabilities
    assert entity.available is True


def test_video_camera_unavailable_without_video_metadata() -> None:
    snapshot = SimpleNamespace(
        capabilities=("map", "lidar_navigation"),
        raw_info={"deviceInfo": {"feature": "map", "permit": "pincode"}},
    )
    entity = _uninitialized_entity(snapshot=snapshot)

    assert entity.available is False


async def test_video_camera_serializes_concurrent_stream_starts() -> None:
    entity = _uninitialized_entity()
    entity._stream_lock = asyncio.Lock()
    active = 0
    maximum_active = 0

    async def _start() -> str:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1
        return "http://127.0.0.1/live.flv"

    entity._async_start_stream = _start

    results = await asyncio.gather(entity.stream_source(), entity.stream_source())

    assert results == [
        "http://127.0.0.1/live.flv",
        "http://127.0.0.1/live.flv",
    ]
    assert maximum_active == 1
