"""Property-read plans retain async transport and cancellation ownership."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_device_actions,
    client_state_reads,
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


@pytest.mark.parametrize("outcome,busy,drain_path", [
    ("cancel", "lock", "state"),
    ("timeout", "lock", "state"),
    ("cancel_again", "lock", "state"),
    ("cancel", "lock", "disconnect"),
    ("cancel", "executor", "state"),
])
def test_plan_cleanup_finishes_with_busy_state_lock(
    monkeypatch, outcome, busy, drain_path,
):
    entered, closed = Event(), Event()
    monkeypatch.setattr(client_device_actions, "_ACTION_CLEANUP_GRACE", 0.1)

    def plan(device):
        try:
            entered.set()
            yield device_action_plan.ActionDelay(5)
        finally:
            assert device._state_lock._is_owned()
            closed.set()

    async def scenario():
        async with ClientSession() as session:
            client = client_for(session)
            device = client._ensure_device()
            task = asyncio.create_task(client_device_actions.async_run_device_plan(
                client, plan, deadline=time.monotonic() + 1,
            ))
            locked = False
            executor_busy, executor_release = Event(), Event()
            blocking = None
            try:
                while not entered.is_set():
                    await asyncio.sleep(0)
                if busy == "lock":
                    locked = device._state_lock.acquire(blocking=False)
                    assert locked
                else:
                    loop = asyncio.get_running_loop()
                    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))

                    def occupy_executor():
                        executor_busy.set()
                        assert executor_release.wait(5)

                    blocking = loop.run_in_executor(None, occupy_executor)
                    while not executor_busy.is_set():
                        await asyncio.sleep(0)
                if outcome != "timeout":
                    task.cancel()
                    if outcome == "cancel_again":
                        await asyncio.sleep(0.02)
                        task.cancel()
                done, _ = await asyncio.wait({task}, timeout=2)
                assert task in done, "Cleanup waited indefinitely on device state"
                if outcome != "timeout":
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    with pytest.raises(
                        client_device_actions.DreameLawnMowerConnectionError,
                        match="timed out",
                    ):
                        await task
                assert not closed.is_set(), "State cleanup ran without its lock"
                if locked:
                    device._state_lock.release()
                    locked = False
                executor_release.set()
                if blocking is not None:
                    await blocking
                if drain_path == "disconnect":
                    await client.async_close()
                else:
                    await client_state_reads.async_read_device_state(
                        client, lambda current: None, refresh=False
                    )
                assert closed.is_set(), "Deferred cleanup was lost"
            finally:
                if locked:
                    device._state_lock.release()
                executor_release.set()
                if blocking is not None:
                    await blocking
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())
