"""Native remote-control properties retain safety and non-replayed dispatch."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device_commands,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerProperty,
    DreameMowerStatus,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import (
    DreameLawnMowerConnectionError,
    cloud_strings,
    login_response,
    server,
)


def configure(client, *, blocked=False, refresh_error=False):
    device = client._device
    state = SimpleNamespace(
        status=SimpleNamespace(fast_mapping=False, status=DreameMowerStatus.CHARGING),
        _remote_control=False,
        property_mapping={DreameMowerProperty.REMOTE_CONTROL: {"siid": 4, "piid": 15}},
    )
    device._prepare_remote_control_step = lambda *args: (
        device_commands._DreameMowerDeviceCommandMixin._prepare_remote_control_step(
            state, *args))
    client._async_update_device = AsyncMock(
        return_value=device,
        side_effect=DreameLawnMowerConnectionError("refresh failed")
        if refresh_error else None,
    )
    client._remote_control_support_from_device = lambda owner: SimpleNamespace(
        supported=True, state_block_reason="unsafe" if blocked else None,
    )
    return state


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("case", [
    "move", "stop", "unsafe", "unsafe_stop", "refresh_failed",
    "disconnect", "rejected", "missing", "replaced",
])
def test_remote_native_property(monkeypatch, account, case):
    strings = cloud_strings(account)
    requests = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        body = (await request.json())["data"]
        requests.append(body)
        assert body["method"] == "set_properties"
        row, = body["params"]
        assert {key: row[key] for key in ("did", "siid", "piid")} == {
            "did": "42", "siid": 4, "piid": 15}
        payload = json.loads(row["value"])
        stopping = case in {"stop", "unsafe_stop"}
        assert payload["spdv"] == (0 if stopping else 200)
        assert payload["spdw"] == 0
        assert payload["audio"] == "false"
        assert 0 <= payload["random"] < 65535
        if case == "disconnect":
            request.transport.close()
            return web.Response()
        return web.json_response({"code": 80001 if case == "rejected" else 0,
                                  "data": {} if case == "missing" else {
                                      "result": [{"code": 0}]}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            state = configure(client, blocked=case in {"unsafe", "unsafe_stop"},
                              refresh_error=case in {"refresh_failed", "unsafe_stop"})
            if case == "replaced":
                client._async_update_device.return_value = object()
            stop = case in {"stop", "unsafe_stop"}
            try:
                operation = client.async_remote_control_move_step(
                    velocity=0 if stop else 200, prompt=False)
                if case in {"unsafe", "refresh_failed", "disconnect", "rejected",
                            "replaced"}:
                    with pytest.raises(DreameLawnMowerConnectionError):
                        await operation
                else:
                    assert await operation == (
                        None if case == "missing" else [{"code": 0}])
                assert len(requests) == (
                    0 if case in {"unsafe", "refresh_failed", "replaced"} else 1)
                assert client._async_update_device.await_count == (0 if stop else 1)
                assert state._remote_control == bool(requests)
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["refresh", "command"])
@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_remote_native_cancellation(monkeypatch, stage, stop):
    strings = cloud_strings("dreame")
    writes = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            writes.append(await request.json())
            entered.set()
            await release.wait()
            return web.json_response({"code": 0, "data": {"result": [{"code": 0}]}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            configure(client)
            if stage == "refresh":
                async def refresh(**kwargs):
                    entered.set()
                    await release.wait()
                    return client._device
                client._async_update_device = refresh
            operation = asyncio.create_task(
                client.async_remote_control_move_step(velocity=200))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if stop == "close":
                    await asyncio.wait_for(client.async_close(), 2)
                else:
                    operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
                assert len(writes) == (stage == "command")
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await asyncio.gather(operation, return_exceptions=True)
                await client.async_close()
    asyncio.run(scenario())

@pytest.mark.parametrize("stopping", [False, True])
def test_remote_preparation_preserves_state_then_rpc_lock_order(monkeypatch, stopping):
    """MQTT state callbacks may request properties while holding state ownership."""
    import threading

    strings = cloud_strings("dreame")

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        return web.json_response({"code": 0, "data": {"result": [{"code": 0}]}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            configure(client)
            state_lock = client._device._state_lock
            rpc_lock = client._device._protocol.cloud._operation_lock()
            state_held, support_entered = threading.Event(), threading.Event()
            acquired = []

            def callback():
                with state_lock:
                    state_held.set()
                    if support_entered.wait(2):
                        got_rpc = rpc_lock.acquire(timeout=0.5)
                        acquired.append(got_rpc)
                        if got_rpc:
                            rpc_lock.release()

            def support(device):
                support_entered.set()
                with state_lock:
                    return SimpleNamespace(supported=True, state_block_reason=None)

            client._remote_control_support_from_device = support
            thread = threading.Thread(target=callback)
            thread.start()
            try:
                assert await asyncio.to_thread(state_held.wait, 2)
                await asyncio.wait_for(client.async_remote_control_move_step(
                    velocity=0 if stopping else 200), 3)
                assert acquired == [True]
            finally:
                support_entered.set()
                await asyncio.to_thread(thread.join, 3)
                await client.async_close()
    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_remote_cancel_drains_preparation_without_dispatch(monkeypatch, stop):
    import threading

    async def scenario():
        async with ClientSession() as session:
            client = client_for(session)
            state = configure(client)
            entered, release = threading.Event(), threading.Event()

            def support(device):
                entered.set()
                assert release.wait(2)
                return SimpleNamespace(supported=True, state_block_reason=None)

            client._remote_control_support_from_device = support
            operation = asyncio.create_task(client.async_remote_control_stop())
            closing = None
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                if stop == "close":
                    closing = asyncio.create_task(client.async_close())
                else:
                    operation.cancel()
                await asyncio.sleep(0.02)
                assert not operation.done()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(operation, 1)
                if closing:
                    await closing
                assert state._remote_control is False
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await asyncio.gather(operation, return_exceptions=True)
                await client.async_close()
    asyncio.run(scenario())
