from __future__ import annotations

import asyncio

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
            async def async_save(self, payload: dict[str, object]) -> None:
                saved.update(payload)

        cache = object.__new__(DreameLawnMowerVideoProvisioningCache)
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
