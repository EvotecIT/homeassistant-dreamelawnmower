from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.models import (
    DreameLawnMowerCameraStreamRuntimeInputs,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.xp2p_config import (
    DreameLawnMowerXp2pDeviceConfig,
)
from custom_components.dreame_lawn_mower.video_provisioning_cache import (
    PROVISIONING_CACHE_SOURCE,
    DreameLawnMowerVideoProvisioningCache,
    _decode_provisioning_payload,
)


def test_provisioning_cache_restores_runtime_without_cloud_token_or_raw_data() -> None:
    inputs, config = _decode_provisioning_payload(
        {
            "did": "did-1",
            "inputs": {
                "channel_id": "channel-1",
                "product_id": "product-1",
                "device_name": "device-1",
                "p2p_info": "p2p-info-1",
                "secret_id": "secret-id-1",
                "secret_key": "secret-key-1",
                "stream_channel": 0,
            },
            "device_config": {
                "server": "stun.example.test",
                "ip": "192.0.2.1",
                "port": 20002,
                "protocol_type": 0,
                "cross": False,
            },
        },
        expected_did="did-1",
    )

    assert inputs is not None
    assert inputs.source == PROVISIONING_CACHE_SOURCE
    assert inputs.ready is True
    assert inputs.lan_client_token is None
    assert inputs.raw == {}
    assert config is not None
    assert config.port == 20002


def test_provisioning_cache_rejects_another_mower_or_incomplete_payload() -> None:
    assert _decode_provisioning_payload(
        {"did": "other"},
        expected_did="did-1",
    ) == (None, None)
    assert _decode_provisioning_payload(
        {
            "did": "did-1",
            "inputs": {"product_id": "product-1"},
            "device_config": {"port": 20002, "protocol_type": 0},
        },
        expected_did="did-1",
    ) == (None, None)


def test_provisioning_cache_save_omits_access_token_and_raw_cloud_payload() -> None:
    async def _run() -> dict[str, object]:
        saved: dict[str, object] = {}

        class _Store:
            hass = SimpleNamespace(state=None)

            async def async_save(self, payload: dict[str, object]) -> None:
                saved.update(payload)

        cache = object.__new__(DreameLawnMowerVideoProvisioningCache)
        cache._write_lock = asyncio.Lock()
        cache._removed = False
        cache._store = _Store()
        cache._did = "did-1"
        cache.inputs = None
        cache.device_config = None
        inputs = DreameLawnMowerCameraStreamRuntimeInputs(
            source="cloud",
            did="did-1",
            product_id="product-1",
            device_name="device-1",
            p2p_info="p2p-info-1",
            secret_id="secret-id-1",
            secret_key="secret-key-1",
            lan_client_token="must-not-be-saved",
            raw={"must": "not be saved"},
        )
        await cache.async_save(inputs, DreameLawnMowerXp2pDeviceConfig())
        return saved

    saved = asyncio.run(_run())

    rendered = repr(saved)
    assert "must-not-be-saved" not in rendered
    assert "not be saved" not in rendered
    assert "p2p-info-1" in rendered


def test_provisioning_cache_clear_removes_persisted_and_runtime_state() -> None:
    async def _run() -> tuple[int, object, object, object]:
        removes = 0

        class _Store:
            async def async_remove(self) -> None:
                nonlocal removes
                removes += 1

        cache = object.__new__(DreameLawnMowerVideoProvisioningCache)
        cache._write_lock = asyncio.Lock()
        cache._removed = False
        cache._store = _Store()
        cache.inputs = object()
        cache.device_config = object()
        cache._runtime_input_config = (object(), object())

        await cache.async_clear()
        return (
            removes,
            cache.inputs,
            cache.device_config,
            cache._runtime_input_config,
        )

    assert asyncio.run(_run()) == (1, None, None, None)


def test_real_cache_stages_http_configuration_and_fences_removal(monkeypatch):
    from aiohttp import ClientSession, web

    from custom_components.dreame_lawn_mower import (
        video_provisioning_cache as cache_module,
    )
    from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import xp2p_config

    from .test_async_cloud_session import server
    from .test_xp2p_host_runtime import _inputs

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            entered.set()
            await release.wait()
            return web.json_response(
                {
                    "code": 0,
                    "data": {
                        "Config": {"StunHost": "stun.example.test", "Protocol": "TCP"}
                    },
                }
            )

        async with server(monkeypatch, handler) as url, ClientSession() as session:
            monkeypatch.setattr(xp2p_config, "TENCENT_XP2P_APP_API", url)
            cache = object.__new__(DreameLawnMowerVideoProvisioningCache)
            cache._hass = object()
            cache._removed = False
            cache._runtime_input_config = None
            cache._write_lock = asyncio.Lock()

            class Store:
                async def async_remove(self):
                    pass

            cache._store = Store()

            def get_session(hass):
                assert hass is cache._hass
                return session

            monkeypatch.setattr(cache_module, "async_get_clientsession", get_session)
            inputs = _inputs()
            release.set()
            config = await cache.async_stage_fresh_device_config(inputs)
            assert cache.resolve_for_transport(inputs, auto=False) is config
            assert cache.resolve_for_transport(inputs, auto=True).protocol_type == 0
            assert config.server == "stun.example.test"
            assert config.protocol_type == 2
            entered.clear()
            release.clear()
            pending = asyncio.create_task(
                cache.async_stage_fresh_device_config(_inputs())
            )
            try:
                await asyncio.wait_for(entered.wait(), 2)
                await cache.async_remove()
                release.set()
                await pending
                assert cache._runtime_input_config is None
                assert cache.inputs is None
                assert cache.device_config is None
                assert not session.closed
            finally:
                release.set()
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(run())
