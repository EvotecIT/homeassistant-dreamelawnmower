"""Async LAN discovery owns real datagram sockets through completion/cancellation."""

import asyncio
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.dreame_lawn_mower import video_camera_startup
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import lan_video
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerVideoRuntimeError,
)

from .test_video_camera import _uninitialized_entity
from .test_video_lan_discovery import _response


def test_async_discovery_validates_responses_and_retains_probe_cadence():
    async def scenario():
        loop = asyncio.get_running_loop()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind(("127.0.0.2", 0))
            server.setblocking(False)
            port = server.getsockname()[1]
            received = []

            async def respond():
                for _ in range(3):
                    packet, sender = await loop.sock_recvfrom(server, 65535)
                    received.append(loop.time())
                    body = json.loads(packet[4:])
                    assert body["params"] == {"productId": "product-1"}
                    assert body["clientToken"] == "token-1"
                    for reply in (
                        _response(token="wrong"),
                        _response(device_name="other-mower"),
                        _response(),
                    ):
                        await loop.sock_sendto(server, reply, sender)

            responder = asyncio.create_task(respond())
            try:
                endpoint = await lan_video.async_discover_lan_video_endpoint(
                    "product-1",
                    device_name="mower-camera",
                    client_token="token-1",
                    timeout=0.3,
                    attempts=3,
                    probe_interval=0.08,
                    port=port,
                    bind_address="127.0.0.1",
                    broadcast_addresses=("127.0.0.2",),
                )
                await asyncio.wait_for(responder, 1)
                assert endpoint.address == "192.0.2.25"
                assert endpoint.device_name == "mower-camera"
                assert len(received) == 3
                assert all(
                    b - a >= 0.05 for a, b in zip(received, received[1:], strict=False)
                )
            finally:
                responder.cancel()
                await asyncio.gather(responder, return_exceptions=True)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reopened:
                reopened.bind(("127.0.0.1", port))

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_async_discovery_releases_socket_on_timeout_or_cancellation(cancel):
    async def scenario():
        loop = asyncio.get_running_loop()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind(("127.0.0.2", 0))
            server.setblocking(False)
            port = server.getsockname()[1]
            task = asyncio.create_task(
                lan_video.async_discover_lan_video_endpoint(
                    "product-1",
                    device_name="mower-camera",
                    timeout=5 if cancel else 0.1,
                    attempts=1,
                    port=port,
                    bind_address="127.0.0.1",
                    broadcast_addresses=("127.0.0.2",),
                )
            )
            try:
                await asyncio.wait_for(loop.sock_recvfrom(server, 65535), 1)
                if cancel:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    with pytest.raises(lan_video.DreameLawnMowerLanVideoDiscoveryError):
                        await task
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reopened:
                reopened.bind(("127.0.0.1", port))

    asyncio.run(scenario())


@pytest.mark.parametrize("cached,failed", [(False, False), (True, False), (True, True)])
def test_camera_discovers_before_native_start_and_preserves_cached_fallback(
    monkeypatch,
    cached,
    failed,
):
    async def scenario():
        entity = _uninitialized_entity()
        inputs = DreameLawnMowerCameraStreamRuntimeInputs(
            source="lan_video_cache",
            did="did-1",
            product_id="product-1",
            device_name="mower-camera",
            lan_client_token="token-1",
        )
        original = lan_video.DreameLawnMowerLanVideoEndpoint(
            "product-1",
            "mower-camera",
            "192.0.2.24",
            9000,
            "2.4",
        )
        discovered = lan_video.DreameLawnMowerLanVideoEndpoint(
            "product-1",
            "mower-camera",
            "192.0.2.25",
            9000,
            "2.4",
        )
        entity._lan_cache.endpoint = original if cached else None
        discover = AsyncMock(return_value=discovered)
        monkeypatch.setattr(
            video_camera_startup, "async_discover_lan_video_endpoint", discover
        )
        calls = []
        session = SimpleNamespace()

        class Runtime:
            def start_lan_stream(self, current, *, endpoint):
                assert current is inputs
                calls.append(endpoint)
                if failed and endpoint is original:
                    raise DreameLawnMowerVideoRuntimeError("stale endpoint")
                return session

        def execute(function, *args):
            future = asyncio.get_running_loop().create_future()
            try:
                future.set_result(function(*args))
            except Exception as error:
                future.set_exception(error)
            return future

        entity.hass = SimpleNamespace(async_add_executor_job=execute)
        assert (
            await entity._async_start_lan_runtime_session(Runtime(), inputs) is session
        )
        assert session.camera_toggle_managed is False
        if cached and not failed:
            discover.assert_not_called()
            assert calls == [original]
        else:
            discover.assert_awaited_once_with(
                "product-1",
                device_name="mower-camera",
                client_token="token-1",
                preferred_address=original.address if cached else None,
            )
            assert calls == ([original, discovered] if cached else [discovered])

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["discovery", "native"])
def test_camera_cancellation_owns_discovery_and_late_native_start(monkeypatch, phase):
    async def scenario():
        entity = _uninitialized_entity()
        inputs = DreameLawnMowerCameraStreamRuntimeInputs(
            source="lan_video_cache",
            did="did-1",
            product_id="product-1",
            device_name="mower-camera",
        )
        endpoint = lan_video.DreameLawnMowerLanVideoEndpoint(
            "product-1",
            "mower-camera",
            "192.0.2.25",
            9000,
            "2.4",
        )
        entered = asyncio.Event()
        pending = asyncio.get_running_loop().create_future()
        late = []
        starts = []

        async def discover(*_args, **_kwargs):
            if phase == "discovery":
                entered.set()
                await asyncio.Future()
            return endpoint

        class Runtime:
            def start_lan_stream(self, *_args, **_kwargs):
                raise AssertionError("executor boundary is controlled by the test")

        def execute(function, *args):
            starts.append(function)
            entered.set()
            return pending

        monkeypatch.setattr(
            video_camera_startup, "async_discover_lan_video_endpoint", discover
        )
        entity.hass = SimpleNamespace(async_add_executor_job=execute)
        entity._schedule_late_start_cleanup = lambda runtime, future: late.append(
            future
        )
        task = asyncio.create_task(
            entity._async_start_lan_runtime_session(Runtime(), inputs)
        )
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not pending.cancelled()
            assert len(starts) == (1 if phase == "native" else 0)
            assert late == ([pending] if phase == "native" else [])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if not pending.done():
                pending.set_result(SimpleNamespace())

    asyncio.run(scenario())
