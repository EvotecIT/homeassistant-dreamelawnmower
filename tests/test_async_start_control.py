"""Native start preserves branch identity, fresh-task guards and ownership."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device_commands,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerAction,
    DreameMowerProperty,
    DreameMowerStatus,
    DreameMowerTaskStatus,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_dock import configure_dock


def configure_start(client, branch="fresh"):
    fake, changes, updates = configure_dock(client)
    fake.status.fast_mapping_paused = branch == "mapping"
    fake.status.returning_paused = branch == "returning"
    fake.status.cruising_paused = branch == "cruising"
    fake.status.paused = branch == "paused"
    fake.status.cleaning_paused = False
    fake.status.scheduled_clean = False
    fake.status.started = branch == "paused"
    fake.status.task_status = (
        DreameMowerTaskStatus.AUTO_CLEANING if branch == "paused"
        else DreameMowerTaskStatus.UNKNOWN if branch == "unknown"
        else DreameMowerTaskStatus.COMPLETED)
    fake.status.status = DreameMowerStatus.CLEANING
    fake.capability.cruising = branch == "cruising"
    fake.action_mapping.update({
        DreameMowerAction.START_MOWING: {"siid": 2, "aiid": 4},
        DreameMowerAction.START_CUSTOM: {"siid": 2, "aiid": 5},
    })
    fake.property_mapping = {DreameMowerProperty.STATUS: {"siid": 2, "piid": 9}}
    owner = device_commands._DreameMowerDeviceCommandMixin
    fake._start_custom_plan = lambda *args: owner._start_custom_plan(fake, *args)
    fake._start_mowing_plan = lambda: owner._start_mowing_plan(fake)
    client._device.status = fake.status
    client._device.capability = fake.capability
    client._device._start_mowing_plan = fake._start_mowing_plan
    client._async_refresh_authoritative_snapshot = AsyncMock(
        return_value=SimpleNamespace(started=True, mowing=True,
                                     mowing_session_active=True))
    return fake, changes, updates


@pytest.mark.parametrize("branch,identity,action", [
    ("fresh", True, 4), ("paused", False, 4), ("unknown", None, 4),
    ("mapping", False, 5), ("returning", False, 3), ("cruising", False, 5),
])
@pytest.mark.parametrize("account", ["dreame", "mova"])
def test_native_start_branch(monkeypatch, branch, identity, action, account):
    strings = cloud_strings(account)
    requests = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        body = (await request.json())["data"]
        requests.append(body)
        assert body["params"]["aiid"] == action
        expected = [] if action != 5 else [{
            "piid": 9, "value": (DreameMowerStatus.FAST_MAPPING.value
                                 if branch == "mapping" else
                                 DreameMowerStatus.CLEANING.value)}]
        assert body["params"]["in"] == expected
        return web.json_response({"code": 0, "data": {"result": {"code": 0}}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            configure_start(client, branch)
            # A stale heartbeat must not override the device's start decision.
            client.async_get_status_blob = AsyncMock(return_value=SimpleNamespace(
                task_resumable=False,
                task_status="mowing" if branch == "fresh" else "idle",
                mowing_session_active=branch == "fresh"))
            try:
                result = await client.async_start_mowing()
                assert result is identity
                assert len(requests) == 1
                client._async_refresh_authoritative_snapshot.assert_not_awaited()
            finally:
                await client.async_close()
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["delay", "command", "confirmation"])
@pytest.mark.parametrize("shutdown", [False, True])
def test_start_lifetime(monkeypatch, stage, shutdown):
    strings = cloud_strings("dreame")
    requests = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            requests.append(await request.json())
            entered.set()
            if stage == "command":
                await release.wait()
            return web.json_response({"code": 0, "data": {"result": None}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            fake, _, _ = configure_start(client)
            if stage == "delay":
                fake._map_select_time = time.time()
            operation = asyncio.create_task(
                client._async_call_start_mowing_with_session_identity())
            try:
                if stage == "delay":
                    await asyncio.sleep(0.05)
                    assert fake._map_select_time is None
                else:
                    await asyncio.wait_for(entered.wait(), 2)
                    if stage == "confirmation":
                        await asyncio.sleep(0.05)
                if shutdown:
                    await asyncio.wait_for(client.async_close(), 2)
                else:
                    operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(operation, 0.5)
                assert len(requests) == (0 if stage == "delay" else 1)
                client._async_refresh_authoritative_snapshot.assert_not_awaited()
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await asyncio.gather(operation, return_exceptions=True)
                await client.async_close()
    asyncio.run(scenario())


def test_start_identity_and_branch_share_lock_but_http_releases_it(monkeypatch):
    strings = cloud_strings("dreame")

    async def scenario():
        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            assert state_lock.acquire(blocking=False)
            state_lock.release()
            return web.json_response({"code": 0, "data": {"result": {"code": 0}}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            fake, changes, _ = configure_start(client)
            state_lock = client._device._state_lock

            class LockedStatus(SimpleNamespace):
                def __getattribute__(self, name):
                    if name in {"task_status", "started"}:
                        assert state_lock._is_owned()
                    return super().__getattribute__(name)

            fake.status = LockedStatus(**vars(fake.status))
            client._device.status = fake.status

            def update_status(*args):
                assert state_lock._is_owned()
                changes.append(args)

            fake._update_status = update_status
            try:
                assert await client._async_call_start_mowing_with_session_identity()
                assert changes
            finally:
                await client.async_close()
    asyncio.run(scenario())


@pytest.mark.parametrize("branch", ["paused", "unknown", "mapping", "returning",
                                    "cruising"])
def test_fresh_start_guard_precedes_device_mutation(monkeypatch, branch):
    async def handler(request):
        pytest.fail("A rejected fresh start must not issue HTTP")

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            _, changes, updates = configure_start(client, branch)
            try:
                with pytest.raises(DreameLawnMowerCommandRejectedError,
                                   match="cannot resume"):
                    await client._async_call_start_mowing_with_session_identity(
                        require_new_session=True)
                assert changes == []
                assert updates == []
            finally:
                await client.async_close()
    asyncio.run(scenario())


@pytest.mark.parametrize("reply", ["missing", "disconnect", "rejected"])
@pytest.mark.parametrize("confirmed", [False, True])
def test_start_uncertain_result_never_replays(monkeypatch, reply, confirmed):
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import client_core
    monkeypatch.setattr(client_core, "_MUTATION_CONFIRMATION_DELAYS_SECONDS", (0,))
    strings = cloud_strings("dreame")
    requests = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        requests.append(await request.json())
        if reply == "disconnect":
            request.transport.close()
            return web.Response()
        result = None if reply == "missing" else {"code": 7}
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            configure_start(client)
            client._async_refresh_authoritative_snapshot.return_value = SimpleNamespace(
                started=confirmed, mowing=confirmed, mowing_session_active=confirmed)
            try:
                if reply == "rejected" or not confirmed:
                    with pytest.raises(DreameLawnMowerConnectionError):
                        await client._async_call_start_mowing_with_session_identity()
                else:
                    assert await client._async_call_start_mowing_with_session_identity()
                assert len(requests) == 1
                assert client._async_refresh_authoritative_snapshot.await_count == (
                    0 if reply == "rejected" else 1)
            finally:
                await client.async_close()
    asyncio.run(scenario())
