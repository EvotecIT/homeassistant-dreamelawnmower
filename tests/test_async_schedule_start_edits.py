"""Qualified native start-time edits against a local HTTP schedule peer."""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web

from .test_async_app_commands import client_for
from .test_async_cloud_session import (
    DreameLawnMowerClient,
    DreameLawnMowerConnectionError,
    cloud_strings,
    login_response,
    server,
)
from .test_schedule_start_edits import _RowTransport


@pytest.mark.parametrize("account_type", ["dreame", "mova"])
@pytest.mark.parametrize(
    "mode",
    [
        "execute",
        "dry_run",
        "unchanged",
        "unconfirmed",
        "blocked",
        "disconnect",
        "missing_ack",
        "contradict_readback",
        "changed_shape",
        "preparation_changed",
        "blocked_before_commit",
        "final_readback_changed",
    ],
)
def test_native_start_edit_preserves_transaction_contract(
    monkeypatch, account_type, mode
):
    peer = _RowTransport(
        missing_ack="SCHDDV3" if mode == "missing_ack" else None,
        contradict_readback=mode == "contradict_readback",
        changed_shape=mode == "changed_shape",
    )
    strings = cloud_strings(account_type)
    actions = []
    preflights = []

    async def refresh(client, *, force_request_properties, deadline):
        assert force_request_properties and client._schedule_async_gate.locked()
        preflights.append(True)
        if mode == "preparation_changed":
            peer.native["d"][1][2] = "External edit"
        blocked = mode == "blocked" or (
            mode == "blocked_before_commit" and len(preflights) == 2
        )
        return SimpleNamespace(
            available=True,
            activity="mowing" if blocked else "docked",
            state="mowing" if blocked else "idle",
            mowing_session_active=blocked,
            task_resumable=False,
        )

    monkeypatch.setattr(DreameLawnMowerClient, "_async_update_device", refresh)

    async def handler(request):
        if request.path == strings[17]:
            assert mode != "unconfirmed"
            return web.json_response(login_response(strings))
        action = (await request.json())["data"]["params"]["in"][0]
        actions.append(action)
        if action["m"] == "s" and mode == "disconnect":
            request.transport.close()
            return web.Response()
        result = peer.call_app_action(action, retry_count=0)
        if action["m"] == "s" and action["t"] == "SCHDSV3":
            assert len(preflights) == 2
            if mode == "final_readback_changed":
                peer.native["d"][1][2] = "External edit"
        return web.json_response({"code": 0, "data": {"result": result}})

    async def scenario():
        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account_type)
            client._snapshot_from_device = lambda device, **kwargs: device
            try:
                operation = client.async_set_app_schedule_task_start_time(
                    map_index=0,
                    plan_id=0,
                    week_day=2,
                    task_index=0,
                    start=658 if mode == "unchanged" else 663,
                    execute=mode != "dry_run",
                    confirm_write=mode != "unconfirmed",
                )
                if mode in {"execute", "dry_run", "unchanged"}:
                    result = await operation
                    assert result["executed"] is (mode == "execute")
                    assert result["confirmed"] is (mode != "dry_run")
                    if mode == "execute":
                        assert result["version"] == 43168
                        assert "raw_text" not in result["confirmed_schedule"]
                        before = base64.b64decode(peer.original["d"][0][3])
                        after = base64.b64decode(peer.native["d"][0][3])
                        assert [
                            i
                            for i, (a, b) in enumerate(zip(before, after, strict=True))
                            if a != b
                        ] == [17]
                        assert peer.native["d"][1] == peer.original["d"][1]
                else:
                    error = (
                        ValueError
                        if mode in {"unconfirmed", "changed_shape"}
                        else DreameLawnMowerConnectionError
                    )
                    with pytest.raises(error):
                        await operation
                writes = [a for a in actions if a["m"] == "s"]
                if mode in {
                    "dry_run",
                    "unchanged",
                    "unconfirmed",
                    "blocked",
                    "preparation_changed",
                }:
                    assert not writes
                elif mode == "disconnect":
                    assert len(writes) == 1
                if mode in {
                    "missing_ack",
                    "contradict_readback",
                    "changed_shape",
                    "blocked_before_commit",
                    "disconnect",
                }:
                    assert not any(a["t"] == "SCHDSV3" for a in writes)
                assert not client._schedule_async_gate.locked()
                assert not client._cloud_read_tasks
            finally:
                await client.async_close()
            assert not session.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["upload", "commit"])
@pytest.mark.parametrize("stop", ["cancel", "close"])
def test_native_start_edit_cancellation_stops_followup_requests(
    monkeypatch, stage, stop
):
    peer = _RowTransport()
    strings = cloud_strings("dreame")
    actions = []

    async def refresh(client, **kwargs):
        return SimpleNamespace(
            available=True,
            activity="docked",
            state="idle",
            mowing_session_active=False,
            task_resumable=False,
        )

    monkeypatch.setattr(DreameLawnMowerClient, "_async_update_device", refresh)

    async def scenario():
        blocked, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            action = (await request.json())["data"]["params"]["in"][0]
            actions.append(action)
            result = peer.call_app_action(action, retry_count=0)
            if action["m"] == "s" and action["t"] == (
                "SCHDDV3" if stage == "upload" else "SCHDSV3"
            ):
                blocked.set()
                await release.wait()
            return web.json_response({"code": 0, "data": {"result": result}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            client._snapshot_from_device = lambda device, **kwargs: device
            task = asyncio.create_task(
                client.async_set_app_schedule_task_start_time(
                    map_index=0,
                    plan_id=0,
                    week_day=2,
                    task_index=0,
                    start=663,
                    execute=True,
                    confirm_write=True,
                )
            )
            try:
                await asyncio.wait_for(blocked.wait(), 2)
                count = len(actions)
                if stop == "close":
                    await asyncio.wait_for(client.async_close(), 2)
                else:
                    task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                release.set()
                await client.async_close()
                assert len(actions) == count
                assert not client._schedule_async_gate.locked()
                assert not client._cloud_read_tasks
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())
