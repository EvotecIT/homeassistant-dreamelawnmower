"""Async configuration staging owns publication through entry shutdown."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web

from custom_components.dreame_lawn_mower import video_provisioning_cache as cache_owner
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import xp2p_config

from .test_async_cloud_session import server
from .test_xp2p_host_runtime import _inputs


@pytest.mark.parametrize("finish", ["success", "cancel", "close", "remove"])
def test_configuration_stage_never_publishes_after_shutdown(monkeypatch, finish):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            entered.set()
            await release.wait()
            return web.json_response({"code": 0, "data": {"Config": {
                "StunHost": "stun.example.test", "StunPort": 20003,
                "Protocol": "TCP", "EnableCrossStunTurn": 1,
            }}})

        store = SimpleNamespace(async_save=AsyncMock(), async_remove=AsyncMock())
        monkeypatch.setattr(cache_owner, "_cache_store", lambda hass, entry_id: store)
        inputs = _inputs()
        cache = cache_owner.DreameLawnMowerVideoProvisioningCache(
            SimpleNamespace(), entry_id="entry-test", did=inputs.did,
        )
        async with server(monkeypatch, handler) as url, ClientSession() as session:
            monkeypatch.setattr(xp2p_config, "TENCENT_XP2P_APP_API", url)
            monkeypatch.setattr(
                cache_owner, "async_get_clientsession", lambda hass: session
            )
            task = asyncio.create_task(cache.async_stage_fresh_device_config(inputs))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                if finish == "cancel":
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    if finish == "close":
                        await cache.async_close()
                    elif finish == "remove":
                        await cache.async_remove()
                    release.set()
                    result = await task
                    assert result.server == "stun.example.test"
                    if finish == "success":
                        assert cache.resolve_device_config(inputs) is result
                        auto = cache.resolve_for_transport(inputs, auto=True)
                        assert (
                            auto.server, auto.port, auto.protocol_type, auto.cross
                        ) == (
                            result.server, result.port,
                            xp2p_config.XP2P_PROTOCOL_AUTO, False,
                        )
                        assert cache.resolve_for_transport(inputs, auto=False) is result
                if finish != "success":
                    assert cache.resolve_device_config(inputs) is None
                store.async_save.assert_not_awaited()
                if finish == "remove":
                    store.async_remove.assert_awaited_once()
                assert not session.closed
                assert not session.connector.closed
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
