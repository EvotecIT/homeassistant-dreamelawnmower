"""Native schedule mutation contracts against local synthetic protocol peers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_schedule_writes,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerCommandRejectedError,
    attempted_write_fields,
)

from .test_app_schedules import _FakeAppScheduleCloud, decode_schedule_payload_text
from .test_async_app_commands import client_for
from .test_async_cloud_session import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    cloud_strings,
    login_response,
    server,
)
from .test_schedule_tables import TableCloud


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize("kind", ["document", "table", "upload"])
@pytest.mark.parametrize(
    "mode", ["dry_run", "execute", "unconfirmed", "blocked", "disconnect"]
)
def test_native_schedule_write(monkeypatch, account_type, kind, mode):
    peer = TableCloud() if kind == "table" else _FakeAppScheduleCloud()
    actions = []
    preflights = []
    strings = cloud_strings(account_type)

    async def refresh(client, *, force_request_properties, deadline):
        assert force_request_properties
        assert client._schedule_async_gate.locked()
        preflights.append(True)
        snapshot = SimpleNamespace(
            available=True,
            activity="mowing" if mode == "blocked" else "docked",
            state="mowing" if mode == "blocked" else "idle",
            mowing_session_active=mode == "blocked",
            task_resumable=False,
        )
        client._snapshot_from_device = lambda device, *, fresh_task_state: (
            snapshot if fresh_task_state else pytest.fail("Fresh task state required")
        )
        return client._device

    monkeypatch.setattr(DreameLawnMowerClient, "_async_update_device", refresh)

    async def handler(request):
        if request.path == strings[17]:
            assert mode != "unconfirmed"
            return web.json_response(login_response(strings))
        action = (await request.json())["data"]["params"]["in"][0]
        actions.append(action)
        if action["m"] == "s":
            assert preflights == [True]
            if mode == "disconnect":
                request.transport.close()
                return web.Response()
        if kind == "table" and action["t"] not in {
            "SCHDT",
            "SCHDI",
            "SCHDC",
            "SCHDS",
            "SCHDIV2",
        }:
            result = {"out": [{"r": 7}]}
        elif kind != "table" and action["t"] in {"SCHDIV3", "SCHDI"}:
            result = {"out": [{"r": 7}]}
        else:
            result = peer.call_app_action(action)
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account_type)
            try:
                options = {
                    "execute": mode != "dry_run",
                    "confirm_write": mode != "unconfirmed",
                }
                if kind == "upload":
                    plans = decode_schedule_payload_text(peer.payloads[0]["text"])
                    operation = client.async_plan_app_schedule_upload(
                        map_index=0, plans=plans, chunk_size=10, **options
                    )
                else:
                    operation = client.async_set_app_schedule_plan_enabled(
                        map_index=2 if kind == "table" else 0,
                        plan_id=1 if kind == "table" else 0,
                        enabled=True,
                        **options,
                    )
                if mode in {"unconfirmed", "blocked", "disconnect"}:
                    error = (
                        ValueError
                        if mode == "unconfirmed"
                        else DreameLawnMowerCommandRejectedError
                        if mode == "blocked"
                        else DreameLawnMowerConnectionError
                    )
                    with pytest.raises(error):
                        await operation
                else:
                    result = await operation
                    assert result["executed"] is (mode == "execute")
                    if kind == "table" and mode == "execute":
                        assert result["confirmed"]
                        assert [
                            p["enabled"] for p in result["confirmed_schedule"]["plans"]
                        ] == [False, True]
                writes = [a for a in actions if a["m"] == "s"]
                if mode in {"unconfirmed", "dry_run", "blocked"}:
                    assert not writes
                elif mode == "disconnect" or kind != "upload":
                    assert len(writes) == 1
                else:
                    assert len(writes) > 1
                assert not client._schedule_async_gate.locked()
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", ["none", "empty", "missing", "rejected", "duplicate"]
)
def test_native_schedule_rejects_stale_task_state(monkeypatch, failure):
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        device_types,
    )

    DreameMowerProperty = device_types.DreameMowerProperty

    required = [
        DreameMowerProperty.STATE,
        DreameMowerProperty.STATUS,
        DreameMowerProperty.TASK_STATUS,
        DreameMowerProperty.CLEANING_PAUSED,
    ]
    strings = cloud_strings("dreame")
    peer = _FakeAppScheduleCloud()
    actions = []
    property_requests = []

    async def handler(request):
        if request.path == strings[17]:
            return web.json_response(login_response(strings))
        body = (await request.json())["data"]
        if body["method"] == "get_properties":
            property_requests.append(body["params"])
            rows = [
                {**row, "code": 0, "value": 0}
                for row in body["params"]
                if row["did"] != "100001"
            ]
            if failure == "none":
                rows = None
            elif failure == "empty":
                rows = []
            elif failure == "missing":
                rows.pop()
            elif failure == "rejected":
                rows[-1]["code"] = -1
            else:
                rows.append(dict(rows[-1]))
            return web.json_response({"code": 0, "data": {"result": rows}})
        action = body["params"]["in"][0]
        actions.append(action)
        result = (
            {"out": [{"r": 7}]}
            if action["t"] in {"SCHDIV3", "SCHDI"}
            else peer.call_app_action(action)
        )
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            device = client._device
            device._ready = True
            for prop in required:
                device.data[prop.value] = 0
            monkeypatch.setattr(device, "_select_update_properties", lambda: required)
            client._snapshot_from_device = lambda *args, **kwargs: pytest.fail(
                "Failed fresh read reused cached state"
            )
            try:
                with pytest.raises(
                    DreameLawnMowerConnectionError, match="Fresh mower task properties"
                ):
                    await client.async_set_app_schedule_plan_enabled(
                        map_index=0,
                        plan_id=0,
                        enabled=True,
                        execute=True,
                        confirm_write=True,
                    )
                assert len(property_requests) == 1
                assert all(a["m"] == "g" for a in actions)
                assert not client._schedule_async_gate.locked()
            finally:
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "close"])
@pytest.mark.parametrize("kind", ["upload", "table"])
def test_schedule_write_holds_transaction_and_cancels_without_later_legs(
    monkeypatch, stop, kind
):
    strings = cloud_strings("dreame")
    peer = TableCloud() if kind == "table" else _FakeAppScheduleCloud()

    async def refresh(client, **kwargs):
        snapshot = SimpleNamespace(
            available=True,
            activity="docked",
            state="idle",
            mowing_session_active=False,
            task_resumable=False,
        )
        client._snapshot_from_device = lambda device, *, fresh_task_state: (
            snapshot if fresh_task_state else pytest.fail("Fresh task state required")
        )
        return client._device

    monkeypatch.setattr(DreameLawnMowerClient, "_async_update_device", refresh)

    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        actions = []

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            action = (await request.json())["data"]["params"]["in"][0]
            actions.append(action)
            if action["m"] == "s":
                entered.set()
                await release.wait()
            if kind == "table" and action["t"] not in {
                "SCHDT", "SCHDI", "SCHDC", "SCHDS", "SCHDIV2",
            }:
                result = {"out": [{"r": 7}]}
            elif kind != "table" and action["t"] in {"SCHDIV3", "SCHDI"}:
                result = {"out": [{"r": 7}]}
            else:
                result = peer.call_app_action(action)
            return web.json_response({"code": 0, "data": {"result": result}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            pending_write = (
                client.async_set_app_schedule_plan_enabled(
                    map_index=2, plan_id=1, enabled=True,
                    execute=True, confirm_write=True,
                ) if kind == "table" else
                client.async_plan_app_schedule_upload(
                    map_index=0,
                    plans=decode_schedule_payload_text(peer.payloads[0]["text"]),
                    execute=True,
                    confirm_write=True,
                    chunk_size=10,
                )
            )
            operation = asyncio.create_task(pending_write)
            reader = None
            try:
                await asyncio.wait_for(entered.wait(), 2)
                count = len(actions)
                reader = asyncio.create_task(
                    client.async_get_app_schedules(
                        map_indices=[0], include_current_task=False
                    )
                )
                await asyncio.sleep(0.02)
                assert len(actions) == count
                if stop == "close":
                    await asyncio.wait_for(client.async_close(), 2)
                else:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
                    operation.cancel()
                with pytest.raises(asyncio.CancelledError) as raised:
                    await operation
                assert attempted_write_fields(raised.value) == ("schedule",)
                release.set()
                await client.async_close()
                assert len(actions) == count
                assert len([a for a in actions if a["m"] == "s"]) == 1
                assert not client._schedule_async_gate.locked()
                assert not client._cloud_read_tasks
            finally:
                release.set()
                if reader is not None:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
                await asyncio.gather(operation, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())


def test_queued_schedule_snapshot_obeys_transaction_deadline(monkeypatch):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
        schedule_write_plan,
    )

    monkeypatch.setattr(
        client_schedule_writes,
        "time",
        SimpleNamespace(monotonic=lambda: time.monotonic() - 119.9),
    )

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        release = Event()
        occupied = None
        async with ClientSession() as session:
            client = client_for(session)

            async def refresh(*args, **kwargs):
                nonlocal occupied
                occupied = loop.run_in_executor(None, release.wait)
                return SimpleNamespace()

            monkeypatch.setattr(client, "_async_update_device", refresh)
            client._snapshot_from_device = lambda *args, **kwargs: pytest.fail(
                "Expired queued snapshot ran"
            )

            def plan():
                yield schedule_write_plan.RequireWriteAllowed()
                yield schedule_write_plan.ScheduleCommand(
                    {"m": "s", "t": "SCHDS", "d": [0, 1]}
                )
                pytest.fail("Timed-out preparation reached a write")

            try:
                with pytest.raises(DreameLawnMowerConnectionError, match="timed out"):
                    await asyncio.wait_for(
                        client_schedule_writes.async_run_schedule_write(client, plan()),
                        0.5,
                    )
                assert not client._schedule_async_gate.locked()
                assert not client._cloud_read_tasks
            finally:
                release.set()
                if occupied is not None:
                    await occupied
                await client.async_close()

    asyncio.run(scenario())
