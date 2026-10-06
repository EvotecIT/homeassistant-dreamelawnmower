"""Native docking preserves control policy and owns the full command sequence."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import device_commands
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.device_types import (
    DreameMowerAction,
    DreameMowerProperty,
    DreameMowerState,
    DreameMowerStatus,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerCommandRejectedError,
)

from .test_async_app_commands import client_for
from .test_async_cancel_task import configure_stop
from .test_async_cloud_session import cloud_strings, login_response, server


def configure_dock(client, *, mapping=False):
    fake, changes, updates = configure_stop(client)
    fake.status.docked = False
    fake.status.fast_mapping = mapping
    fake.status.go_to_zone = SimpleNamespace(cleaning_mode=None, stop=True)
    fake.action_mapping[DreameMowerAction.DOCK] = {"siid": 2, "aiid": 3}
    owner = device_commands._DreameMowerDeviceCommandMixin
    fake._restore_go_to_zone = lambda: owner._restore_go_to_zone(fake)
    fake._dock_plan = lambda: owner._dock_plan(fake)
    fake._stop_plan = lambda: owner._stop_plan(fake)
    client._device._dock_plan = fake._dock_plan
    client._device._ordinary_stop_plan = lambda: owner._ordinary_stop_plan(fake)
    client._async_refresh_authoritative_snapshot = AsyncMock(
        return_value=SimpleNamespace(returning=True, docked=False, state="returning",
                                     mowing_session_active=False))
    return fake, changes, updates


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("reply", ["success", "missing", "disconnect", "rejected"])
def test_native_dock_policy(monkeypatch, account, reply):
    strings = cloud_strings(account)
    requests = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        body = (await request.json())["data"]
        requests.append(body)
        assert body["params"] == {"did": "42", "siid": 2, "aiid": 3, "in": []}
        if reply == "disconnect":
            request.transport.close()
            return web.Response()
        result = None if reply == "missing" else {
            "code": 7 if reply == "rejected" else 0}
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            fake, changes, _ = configure_dock(client)
            try:
                if reply == "rejected":
                    with pytest.raises(DreameLawnMowerCommandRejectedError):
                        await client.async_dock_without_stopping()
                else:
                    await client.async_dock_without_stopping()
                assert len(requests) == 1
                assert fake.status.go_to_zone is None
                assert changes == [
                    (DreameMowerProperty.STATUS, DreameMowerStatus.BACK_HOME.value),
                    (DreameMowerProperty.STATE, DreameMowerState.RETURNING.value),
                    "notify",
                ]
                assert client._async_refresh_authoritative_snapshot.await_count == (
                    1 if reply in {"missing", "disconnect"} else 0)
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
    asyncio.run(scenario())


@pytest.mark.parametrize("mapping", [False, True])
def test_ordinary_stop_native_branch(monkeypatch, mapping):
    strings = cloud_strings("dreame")
    actions = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        actions.append((await request.json())["data"]["params"]["aiid"])
        return web.json_response({"code": 0, "data": {"result": {"code": 0}}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            configure_dock(client, mapping=mapping)
            try:
                await client._async_device_control(dock=False)
                assert actions == [3 if mapping else 1]
            finally:
                await client.async_close()
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["preflight", "stop", "between", "dock",
                                   "confirmation"])
@pytest.mark.parametrize("shutdown", [False, True])
def test_dock_sequence_lifetime(monkeypatch, stage, shutdown):
    strings = cloud_strings("dreame")
    actions = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            action = (await request.json())["data"]["params"]["aiid"]
            actions.append(action)
            if (stage in {"stop", "between"} and action == 1
                    or stage in {"dock", "confirmation"} and action == 3):
                entered.set()
            if stage == "stop" and action == 1 or stage == "dock" and action == 3:
                await release.wait()
            result = None if stage == "confirmation" else {"code": 0}
            return web.json_response({"code": 0, "data": {"result": result}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            configure_dock(client)
            reads = 0

            async def refresh():
                nonlocal reads
                reads += 1
                if stage == "preflight":
                    entered.set()
                    await release.wait()
                return SimpleNamespace(
                    state=("mowing" if reads == 1 and stage != "confirmation"
                           else "idle"),
                    task_status=None,
                    mowing_session_active=reads == 1 and stage != "confirmation",
                )

            client.async_refresh = AsyncMock(side_effect=refresh)
            operation = asyncio.create_task(client.async_dock())
            try:
                await asyncio.wait_for(entered.wait(), 3)
                if stage in {"between", "confirmation"}:
                    await asyncio.sleep(0.05)
                if shutdown:
                    await asyncio.wait_for(client.async_close(), 2)
                else:
                    operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(operation, 0.5)
                expected = ([] if stage == "preflight" else [1] if stage in {
                    "stop", "between"} else [3] if stage == "confirmation" else [1, 3])
                assert actions == expected
                client._async_refresh_authoritative_snapshot.assert_not_awaited()
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await asyncio.gather(operation, return_exceptions=True)
                await client.async_close()
    asyncio.run(scenario())
