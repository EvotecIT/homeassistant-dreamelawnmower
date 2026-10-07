"""Property-read plans retain async transport and cancellation ownership."""

import asyncio
from unittest.mock import Mock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_device_actions,
    device_action_plan,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import cloud_strings, login_response, server


@pytest.mark.parametrize("account", ["dreame", "mova"])
@pytest.mark.parametrize("cancel", [False, True])
def test_property_read_plan_applies_only_completed_response(
    monkeypatch, account, cancel
):
    strings = cloud_strings(account)
    rows = [{"did": "3", "siid": 2, "piid": 3}]
    response = [{**rows[0], "code": 0, "value": 75}]
    applied = []

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            assert body["data"]["method"] == "get_properties"
            assert body["data"]["params"] == rows
            lock = client._device._state_lock
            assert lock.acquire(blocking=False)
            lock.release()
            entered.set()
            await release.wait()
            return web.json_response({"code": 0, "data": {"result": response}})

        def plan(device):
            result = yield device_action_plan.PropertyReadRequest(rows)
            applied.append(result)
            return result

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            task = asyncio.create_task(
                client_device_actions.async_run_device_plan(client, plan)
            )
            try:
                await asyncio.wait_for(entered.wait(), 5)
                if cancel:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    assert not applied
                else:
                    release.set()
                    assert await task == response
                    assert applied == [response]
                assert not session.closed
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["recovery", "backup"])
def test_map_completion_plan_uses_native_read_after_map_flags(monkeypatch, operation):
    strings = cloud_strings("dreame")
    events = []

    async def scenario():
        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            body = await request.json()
            assert body["data"]["method"] == "get_properties"
            assert body["data"]["params"] == [{"did": str(prop.value), **mapping}]
            events.append("read")
            return web.json_response({"code": 0, "data": {"result": []}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            device = client._device
            prop = next(
                p
                for p in device.property_mapping
                if p.name == f"MAP_{operation.upper()}_STATUS"
            )
            mapping = device.property_mapping[prop]
            device._ready = True
            device.data[prop.value] = 3
            manager = Mock()
            manager.request_next_map.side_effect = lambda: events.append("map")
            manager.request_next_recovery_map_list.side_effect = lambda: events.append(
                "list"
            )
            monkeypatch.setattr(device, "_map_manager", manager)
            monkeypatch.setattr(
                device._protocol,
                "get_properties",
                Mock(side_effect=AssertionError("Blocking read")),
            )
            original_apply = device._handle_properties_plan

            def apply(rows):
                result = yield from original_apply(rows)
                events.append("apply")
                return result

            monkeypatch.setattr(device, "_handle_properties_plan", apply)
            try:
                await client_device_actions.async_run_device_plan(
                    client,
                    lambda current: getattr(
                        current, f"_map_{operation}_status_changed_plan"
                    )(2),
                )
                expected = ["map", "list"] if operation == "recovery" else ["list"]
                assert events == [*expected, "read", "apply"]
                assert not session.closed
            finally:
                await client.async_close()

    asyncio.run(scenario())
