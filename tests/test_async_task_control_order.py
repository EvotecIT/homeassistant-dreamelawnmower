"""Complete task controls preserve preflight/dispatch order and queued lifetime."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client as client_module,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    client_task_control,
    device_action_plan,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.exceptions import (
    DreameLawnMowerCommandRejectedError,
    DreameLawnMowerConnectionError,
)

from .test_async_app_commands import client_for
from .test_async_cloud_session import cloud_strings, login_response, server
from .test_async_start_control import configure_start
from .test_async_targeted_commands import snapshot
from .test_docking import _task_snapshot


def prepare_start(client):
    fake, _, _ = configure_start(client)
    client.async_get_status_blob = AsyncMock(
        return_value=SimpleNamespace(
            task_resumable=False,
            task_status="idle",
            mowing_session_active=False,
        )
    )
    return fake


async def wait_for_map_delay(fake):
    async with asyncio.timeout(2):
        while fake._map_select_time is not None:
            await asyncio.sleep(0.001)


@pytest.mark.parametrize("account", ["dreame", "mova"])
def test_cancel_waits_for_pending_start_and_reads_its_result(monkeypatch, account):
    """A completed Cancel cannot leave an older Start dispatchable after its delay."""
    monkeypatch.setattr(
        client_module, "_TASK_CANCEL_CONFIRMATION_INITIAL_DELAY_SECONDS", 0.001
    )
    monkeypatch.setattr(
        device_action_plan,
        "time",
        SimpleNamespace(time=lambda: 5.0, sleep=time.sleep),
    )
    strings = cloud_strings(account)
    actions = []

    async def scenario():
        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            actions.append((await request.json())["data"]["params"]["aiid"])
            return web.json_response({"code": 0, "data": {"result": {"code": 0}}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session, account)
            fake = prepare_start(client)
            fake._map_select_time = 0.3
            client.async_refresh_authoritative_snapshot = AsyncMock(
                side_effect=[
                    _task_snapshot(state="mowing", active=True),
                    _task_snapshot(state="idle", active=False),
                ]
            )
            start = asyncio.create_task(client.async_start_mowing())
            cancel = None
            try:
                await wait_for_map_delay(fake)
                assert not actions and not start.done()
                cancel = asyncio.create_task(client.async_cancel_current_task())
                await asyncio.sleep(0.02)
                client.async_refresh_authoritative_snapshot.assert_not_awaited()
                assert not cancel.done()
                assert await asyncio.wait_for(start, 2) is True
                assert await asyncio.wait_for(cancel, 2) is True
                assert actions == [4, 1]
                assert not client._cloud_read_tasks
            finally:
                if cancel is not None:
                    cancel.cancel()
                start.cancel()
                await asyncio.gather(
                    start, *([cancel] if cancel else []), return_exceptions=True
                )
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("control", ["fresh", "zone"])
def test_queued_start_checks_state_after_prior_control(monkeypatch, control):
    """Fresh/targeted guards must read state after the preceding Start completes."""
    strings = cloud_strings("dreame")
    actions = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            actions.append((await request.json())["data"])
            entered.set()
            await release.wait()
            return web.json_response({"code": 0, "data": {"result": {"code": 0}}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            prepare_start(client)
            state_read = AsyncMock(return_value=snapshot("zone"))
            client.async_refresh_authoritative_snapshot = state_read
            client._async_refresh_authoritative_snapshot = state_read
            first = asyncio.create_task(client.async_start_mowing())
            second = None
            try:
                await asyncio.wait_for(entered.wait(), 2)
                second = asyncio.create_task(
                    client.async_start_fresh_mowing()
                    if control == "fresh"
                    else client.async_start_zone_mowing([2])
                )
                await asyncio.sleep(0.02)
                state_read.assert_not_awaited()
                release.set()
                await asyncio.wait_for(first, 2)
                with pytest.raises(DreameLawnMowerCommandRejectedError):
                    await asyncio.wait_for(second, 2)
                state_read.assert_awaited_once()
                assert len(actions) == 1
            finally:
                release.set()
                first.cancel()
                if second is not None:
                    second.cancel()
                await asyncio.gather(
                    first, *([second] if second else []), return_exceptions=True
                )
                await client.async_close()

    asyncio.run(scenario())


@pytest.mark.parametrize("interruption", ["queued_cancel", "active_cancel", "close"])
def test_queued_control_interruption_never_dispatches(monkeypatch, interruption):
    """Cancelled waiters and client shutdown cannot produce a delayed action."""
    strings = cloud_strings("dreame")
    actions = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            actions.append((await request.json())["data"])
            entered.set()
            await release.wait()
            return web.json_response({"code": 0, "data": {"result": {"code": 0}}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            prepare_start(client)
            client.async_refresh_authoritative_snapshot = AsyncMock(
                return_value=_task_snapshot(state="idle", active=False)
            )
            first = asyncio.create_task(client.async_start_mowing())
            second = None
            try:
                await asyncio.wait_for(entered.wait(), 2)
                second = asyncio.create_task(client.async_cancel_current_task())
                await asyncio.sleep(0.02)
                if interruption == "close":
                    release.set()
                    await asyncio.wait_for(client.async_close(), 2)
                    with pytest.raises(asyncio.CancelledError):
                        await first
                    with pytest.raises(asyncio.CancelledError):
                        await second
                    client.async_refresh_authoritative_snapshot.assert_not_awaited()
                else:
                    selected = second if interruption == "queued_cancel" else first
                    selected.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(selected, 2)
                    release.set()
                    if interruption == "queued_cancel":
                        await asyncio.wait_for(first, 2)
                        assert (
                            await asyncio.wait_for(
                                client.async_cancel_current_task(), 2
                            )
                            is False
                        )
                    else:
                        assert await asyncio.wait_for(second, 2) is False
                assert len(actions) == 1
                assert not client._cloud_read_tasks
            finally:
                release.set()
                first.cancel()
                if second is not None:
                    second.cancel()
                await asyncio.gather(
                    first, *([second] if second else []), return_exceptions=True
                )
                await client.async_close()

    asyncio.run(scenario())


def test_control_queue_wait_has_a_deadline_without_dispatch(monkeypatch):
    """A busy control times out before preflight and leaves later controls usable."""
    monkeypatch.setattr(client_task_control, "_WAIT_TIMEOUT_SECONDS", 0.03)
    strings = cloud_strings("dreame")
    actions = []

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.path == strings[17]:
                return web.json_response(login_response(strings))
            actions.append((await request.json())["data"])
            entered.set()
            await release.wait()
            return web.json_response({"code": 0, "data": {"result": {"code": 0}}})

        async with server(monkeypatch, handler), ClientSession() as session:
            client = client_for(session)
            prepare_start(client)
            client.async_refresh_authoritative_snapshot = AsyncMock(
                return_value=_task_snapshot(state="idle", active=False)
            )
            first = asyncio.create_task(client.async_start_mowing())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                with pytest.raises(
                    DreameLawnMowerConnectionError, match="waiting for another"
                ):
                    await client.async_cancel_current_task()
                client.async_refresh_authoritative_snapshot.assert_not_awaited()
                release.set()
                await asyncio.wait_for(first, 2)
                assert await client.async_cancel_current_task() is False
                assert len(actions) == 1
            finally:
                release.set()
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)
                await client.async_close()

    asyncio.run(scenario())
